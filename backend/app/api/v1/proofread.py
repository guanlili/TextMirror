"""
TextMirror 文本校对 API
"""
import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.database import get_db
from app.core.dependencies import get_current_user_optional
from app.core.rate_limit import check_guest_rate_limit, check_user_quota
from app.core.config import settings
from app.models.proofread import ProofreadRecord
from app.schemas.proofread import (
    TextProofreadRequest,
    TextProofreadResponse,
    ProofreadIssue,
)
from app.services.proofread import proofread_text
from app.services.audit_log import record_audit_log, AuditTimer

router = APIRouter(prefix="/proofread", tags=["校对"])


@router.post("/text", response_model=TextProofreadResponse, summary='文本在线校对')
async def text_proofread(
    request: TextProofreadRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """
    文本在线校对
    支持游客使用（受限流限制）和登录用户使用
    """
    # 游客限流检查
    if current_user is None:
        await check_guest_rate_limit(http_request)
        # 游客文本长度限制
        if len(request.text) > settings.GUEST_TEXT_MAX_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"游客模式文本长度不能超过{settings.GUEST_TEXT_MAX_LENGTH}字，请登录后使用",
            )
    else:
        await check_user_quota(current_user, db)

    timer = AuditTimer()
    timer.start()
    audit_extra = {"check_types": request.check_types, "domain": request.domain}

    try:
        # 调用校对服务
        result = await proofread_text(
            text=request.text,
            check_types=request.check_types,
            domain=request.domain,
            config_id=request.config_id,
        )
    except RuntimeError as e:
        import traceback
        logger.error(f"校对服务异常: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "proofread_text", user=current_user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        # 指定的模型配置无效：明确告知（通常是配置被删除/停用）
        detail = str(e) if request.config_id is not None else "校对服务暂时不可用，请稍后重试"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST if request.config_id is not None else status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )
    except Exception as e:
        import traceback
        logger.error(f"校对过程发生未知错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "proofread_text", user=current_user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="校对过程发生错误，请稍后重试",
        )

    # 保存校对记录（已登录用户）
    record_id = None
    if current_user:
        record = ProofreadRecord(
            user_id=current_user.id,
            type="text",
            original_text=request.text,
            check_types=json.dumps(request.check_types or []),
            domain=request.domain,
            result=result,
            total_issues=result["total_issues"],
            token_usage=result["usage"],
        )
        db.add(record)
        await db.flush()
        record_id = record.id

    # 构建响应
    issues = [
        ProofreadIssue(
            original=item.get("original", ""),
            type=item.get("type", "unknown"),
            suggestion=item.get("suggestion", ""),
            explanation=item.get("explanation", ""),
            severity=item.get("severity", "warning"),
            chunk_index=item.get("chunk_index", 0),
        )
        for item in result["issues"]
    ]

    # 记录审计日志（成功）
    output_summary = f"发现{result['total_issues']}个问题"
    record_audit_log(
        http_request, "proofread_text", user=current_user,
        input_text=request.text,
        output_text=output_summary,
        extra_params={**audit_extra, "total_issues": result["total_issues"]},
        token_usage=result.get("usage"),
        duration_ms=timer.elapsed_ms(),
    )

    return TextProofreadResponse(
        issues=issues,
        total_issues=result["total_issues"],
        chunks_count=result["chunks_count"],
        usage=result["usage"],
        domain=result["domain"],
        check_types=result["check_types"],
        record_id=record_id,
    )


# ======================================================================
# 多模型并发校对对比
# ======================================================================

class ProofreadCompareRequest(BaseModel):
    """多模型校对对比请求"""
    text: str = Field(..., min_length=1, max_length=100000, description="待校对文本")
    check_types: Optional[List[str]] = Field(None, description="校对类型")
    domain: str = Field(default="general", description="领域")
    config_ids: List[int] = Field(..., min_length=2, max_length=4, description="参与对比的模型配置ID")


class ModelProofreadResult(BaseModel):
    """单模型的校对结果"""
    config_id: int
    config_name: str
    model: str
    issues: List[ProofreadIssue] = []
    total_issues: int = 0
    success: bool = True
    error: Optional[str] = None
    elapsed_ms: int = 0


class ProofreadCompareResponse(BaseModel):
    """多模型校对对比响应"""
    results: List[ModelProofreadResult]
    # 交叉统计：original 完全一致的问题算「共识」
    consensus_originals: List[str] = Field(default_factory=list, description="所有成功模型均发现的问题原文")
    only_in: Dict[int, List[str]] = Field(default_factory=dict, description="仅单一模型发现的问题原文（key=config_id）")


@router.post("/compare", response_model=ProofreadCompareResponse, summary='多模型并发校对对比：同一文本多个模型交叉审校')
async def text_proofread_compare(
    request: ProofreadCompareRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """
    多模型并发校对对比
    同一段文本用多个模型并发校对，返回各模型问题列表及交叉统计（共识/独有）
    """
    import asyncio as _asyncio
    import time as _time

    if current_user is None:
        await check_guest_rate_limit(http_request)
    else:
        await check_user_quota(current_user, db)

    # 加载模型配置
    from sqlalchemy import select as _select
    from app.models.llm_config import LLMConfig
    cfg_result = await db.execute(_select(LLMConfig).where(LLMConfig.id.in_(request.config_ids)))
    configs = {c.id: c for c in cfg_result.scalars().all()}
    if len(configs) < 2:
        raise HTTPException(status_code=400, detail="所选模型配置不足 2 个有效项")

    timer = AuditTimer()
    timer.start()

    async def _run_one(config):
        t0 = _time.perf_counter()
        try:
            r = await proofread_text(
                text=request.text,
                check_types=request.check_types,
                domain=request.domain,
                config_id=config.id,
            )
            return ModelProofreadResult(
                config_id=config.id,
                config_name=config.name,
                model=config.model,
                issues=r["issues"],
                total_issues=r["total_issues"],
                success=True,
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
            )
        except Exception as e:
            logger.error(f"[对比校对] 模型 {config.name} 失败: {e}")
            return ModelProofreadResult(
                config_id=config.id,
                config_name=config.name,
                model=config.model,
                success=False,
                error=str(e)[:300],
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
            )

    items = await _asyncio.gather(*[_run_one(c) for c in configs.values()])
    items = sorted(items, key=lambda i: request.config_ids.index(i.config_id))

    # 交叉统计：按 original 文本对齐（成功模型 ≥2 才有意义）
    ok_results = [i for i in items if i.success and i.issues]
    consensus: List[str] = []
    only_in: Dict[int, List[str]] = {}
    if len([i for i in items if i.success]) >= 2:
        # 每个 original 出现在哪些模型
        owner_map: Dict[str, List[int]] = {}
        for i in ok_results:
            for iss in i.issues:
                orig = (iss.original or "").strip()
                if orig:
                    owner_map.setdefault(orig, []).append(i.config_id)
        for orig, owners in owner_map.items():
            ok_ids = [i.config_id for i in items if i.success]
            if len(set(owners)) == len(ok_ids):
                consensus.append(orig)
            elif len(set(owners)) == 1:
                only_in.setdefault(owners[0], []).append(orig)

    record_audit_log(
        http_request, "proofread_compare", user=current_user,
        input_text=request.text,
        extra_params={
            "domain": request.domain,
            "configs": [i.config_name for i in items],
            "issues_per_model": {str(i.config_id): i.total_issues for i in items},
        },
        duration_ms=timer.elapsed_ms(),
    )

    return ProofreadCompareResponse(
        results=items,
        consensus_originals=consensus,
        only_in=only_in,
    )
