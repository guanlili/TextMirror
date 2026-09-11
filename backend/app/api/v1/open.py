"""
TextMirror 开放 API（对外稳定契约）——核心模块
认证：Authorization: Bearer tm_...（API 密钥）或 JWT 登录 Token
错误契约：非 2xx 响应的 detail 为 {"code": "...", "message": "..."}（含 422，见 open_common）

路由拆分（本文件为聚合入口，main.py 挂载本模块的 router）：
- open.py          文本审校 / 可用模型列表 / 多模型对比
- open_polish.py   AI 润色（同步 + SSE 流式）
- open_usage.py    用量统计
- open_documents.py 异步文档审校（提交 + 轮询）
"""
import json
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.open_common import ERROR_RESPONSES, _check_user_quota_contract
from app.core.database import get_db
from app.core.dependencies import get_current_user_or_apikey
from app.core.rate_limit import (
    charge_api_key_daily,
    check_api_key_rpm,
    refund_api_key_daily_usage,
)
from app.models.api_key import ApiKey
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.user import User
from app.schemas.open import (
    OpenCompareModelResult,
    OpenCompareRequest,
    OpenCompareResponse,
    OpenModelsResponse,
)
from app.schemas.proofread import ProofreadIssue, TextProofreadRequest, TextProofreadResponse
from app.services.audit_log import AuditTimer, record_audit_log
from app.services.proofread import proofread_text

router = APIRouter(tags=["开放API"])


@router.post(
    "/proofread",
    response_model=TextProofreadResponse,
    summary="文本审校",
    description=(
        "对文本进行智能审校，返回逐条问题（原文片段 + 修改建议 + 解释）。\n\n"
        "**认证**：请求头 `Authorization: Bearer tm_...`（API 密钥，网页端「API 密钥」页创建）。\n\n"
        "**快速开始**：只需传 `text`，其余参数全部可选——\n"
        "```json\n"
        '{"text": "这是一段需要审校的文本。"}\n'
        "```\n\n"
        "**参数说明**：\n"
        "- `domain`：文本领域（general/official/legal/...），影响校对规则侧重，默认 general\n"
        "- `config_id`：指定模型配置，普通集成方无需关心\n"
        "- `check_types`：已废弃，传入无效果（总是全量审校）\n\n"
        "**限制**：单密钥每分钟 12 次（`429 RATE_LIMITED`）；"
        "每日配额随归属账号（`429 KEY_QUOTA_EXCEEDED / QUOTA_EXCEEDED`）。\n\n"
        "**错误格式**：非 2xx 时 `detail` 统一为 `{'code': ..., 'message': ...}`，见下方各状态码示例。"
    ),
    responses=ERROR_RESPONSES,
)
async def open_proofread(
    request: TextProofreadRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    开放文本审校端点（复用 Web 端同一校对服务）
    """
    user, api_key = auth

    # 顺序：RPM → 用户配额（免费检查）→ 密钥日配额计费。
    # 用户配额不足时直接拒绝，不扣密钥额度
    if api_key is not None:
        await check_api_key_rpm(api_key)
    await _check_user_quota_contract(user, db)
    if api_key is not None:
        await charge_api_key_daily(api_key)

    timer = AuditTimer()
    timer.start()
    audit_extra = {"check_types": request.check_types, "domain": request.domain}
    if api_key is not None:
        audit_extra["api_key_id"] = api_key.id
        audit_extra["api_key_prefix"] = api_key.key_prefix

    try:
        result = await proofread_text(
            text=request.text,
            domain=request.domain,
            config_id=request.config_id,
            user_id=user.id,
            depth=request.depth,
        )
    except RuntimeError as e:
        import traceback
        logger.error(f"[OpenAPI] 校对服务异常: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "api_proofread", user=user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        if request.config_id is not None:
            # 用户指定了无效 config_id：用户错误，不退还配额
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_CONFIG", "message": str(e)},
            )
        # 服务端故障（如未配置活跃模型）：退还密钥日配额
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "MODEL_UNAVAILABLE", "message": "审校服务暂时不可用，请稍后重试"},
        )
    except Exception as e:
        import traceback
        logger.error(f"[OpenAPI] 校对未知错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "api_proofread", user=user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        # 服务端错误：退还密钥日配额
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": "审校过程发生错误，请稍后重试"},
        )

    # 落校对记录：用户配额按 ProofreadRecord 计数，API 调用必须入库
    record = ProofreadRecord(
        user_id=user.id,
        api_key_id=api_key.id if api_key is not None else None,
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

    record_audit_log(
        http_request, "api_proofread", user=user,
        input_text=request.text,
        output_text=f"发现{result['total_issues']}个问题",
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
        record_id=record.id,
    )



@router.get(
    "/models",
    response_model=OpenModelsResponse,
    summary="可用模型列表",
    description=(
        "返回当前已启用的模型配置（id/名称/模型标识，不含密钥）。\n\n"
        "`id` 用于：\n"
        "- `POST /proofread` 与 `POST /documents` 的 `config_id`（指定单模型，不填=系统默认，即 `is_active=true` 的那条）\n"
        "- `POST /proofread/compare` 的 `config_ids`（2-4 个）\n\n"
        "```bash\n"
        'curl -H "Authorization: Bearer tm_..." .../api/v1/open/models\n'
        "```\n\n"
        "调用对比接口前先查此列表拿 ID。列表内容由管理员在后台维护，"
        "若不足 2 个可用模型请联系管理员启用。"
    ),
    responses=ERROR_RESPONSES,
)
async def open_list_models(
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """可用模型列表（供集成方获取 config_id / config_ids 取值）"""
    result = await db.execute(
        select(LLMConfig.id, LLMConfig.name, LLMConfig.model, LLMConfig.is_active)
        .where(LLMConfig.is_enabled.is_(True))
        .order_by(LLMConfig.is_active.desc(), LLMConfig.id)
    )
    rows = result.all()
    return OpenModelsResponse(
        models=[{"id": r.id, "name": r.name, "model": r.model, "is_active": r.is_active} for r in rows]
    )


@router.post(
    "/proofread/compare",
    response_model=OpenCompareResponse,
    summary="多模型对比审校",
    description=(
        "同一文本用多个模型并发审校，返回各模型结果及交叉统计（共识/独有）。\n\n"
        "**第一步**：先调用 `GET /models` 获取可用模型的 `id`。\n\n"
        "**额度**：按**成功**的模型数计（如 2 个模型全部成功 = 消耗 2 次额度；"
        "1 个失败则只消耗 1 次，失败模型自动退还）。\n\n"
        "**config_ids**：2-4 个模型ID（来自 `GET /models`）。\n\n"
        "单模型调用失败不影响其他模型：对应 result 项 `success=false` 并带 `error` 说明。"
    ),
    responses=ERROR_RESPONSES,
)
async def open_proofread_compare(
    request: OpenCompareRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    开放多模型对比审校端点（复用 Web 端同一对比逻辑）
    """
    user, api_key = auth

    # 加载模型配置（仅启用的可参与对比）
    cfg_result = await db.execute(
        select(LLMConfig).where(
            LLMConfig.id.in_(request.config_ids),
            LLMConfig.is_enabled.is_(True),
        )
    )
    configs = {c.id: c for c in cfg_result.scalars().all()}
    if len(configs) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CONFIG",
                "message": "所选模型配置不足 2 个有效项。请先调用 GET /open/models 查看可用模型ID（已停用的配置不可用）",
            },
        )

    n = len(request.config_ids)
    # 顺序与文本端点一致：RPM → 用户配额预检（免费）→ 密钥计费（按模型数）
    if api_key is not None:
        await check_api_key_rpm(api_key)
    await _check_user_quota_contract(user, db, n)
    # 对比一次消耗 n 倍额度：密钥日配额按模型数计
    if api_key is not None:
        await charge_api_key_daily(api_key, n)

    timer = AuditTimer()
    timer.start()
    audit_extra = {"domain": request.domain, "configs": [c.name for c in configs.values()]}
    if api_key is not None:
        audit_extra["api_key_id"] = api_key.id

    from app.services.model_compare import run_proofread_compare
    raw_items, consensus, only_in = await run_proofread_compare(
        text=request.text,
        domain=request.domain,
        config_ids=request.config_ids,
        configs=configs,
        user_id=user.id,
        log_tag="OpenAPI对比",
    )
    items = [OpenCompareModelResult(**i) for i in raw_items]

    # 部分模型失败：失败模型退还密钥日配额（按成功数结算，失败的不计费）
    if api_key is not None:
        failed = sum(1 for i in items if not i.success)
        if failed > 0:
            await refund_api_key_daily_usage(api_key, failed)

    # 落一条带权重的对比记录：用户配额按 SUM(quota_weight) 计量（此前只预检不落库，
    # 预检通过后配额实际不消耗），用量统计归属到调用密钥。权重=成功模型数，
    # 与密钥日配额（按成功数结算）同口径；全部失败不落（用户配额零消耗）。
    # 问题按 (original, suggestion) 去重——同一错误多模型发现只留一条（带 found_by）
    successes = [i for i in items if i.success]
    if successes:
        from app.services.model_compare import dedupe_compare_issues

        merged_issues = dedupe_compare_issues([
            {"config_name": i.config_name, "issues": [issue.model_dump() for issue in i.issues]}
            for i in successes
        ])
        db.add(ProofreadRecord(
            user_id=user.id,
            api_key_id=api_key.id if api_key is not None else None,
            type="text",
            original_text=request.text,
            check_types=json.dumps([]),
            domain=request.domain,
            result={
                "compare": True,
                "models": [i.config_name for i in items],
                "issues": merged_issues,
                "issues_per_model": {str(i.config_id): i.total_issues for i in items},
            },
            total_issues=len(merged_issues),
            quota_weight=len(successes),
        ))
        await db.flush()

    record_audit_log(
        http_request, "api_proofread_compare", user=user,
        input_text=request.text,
        extra_params={**audit_extra, "issues_per_model": {str(i.config_id): i.total_issues for i in items}},
        duration_ms=timer.elapsed_ms(),
    )

    return OpenCompareResponse(results=items, consensus_originals=consensus, only_in=only_in)




# ---- 子模块路由聚合（挂载顺序不影响路径，路径均为绝对路径） ----
from app.api.v1 import open_documents, open_polish, open_usage  # noqa: E402

router.include_router(open_polish.router)
router.include_router(open_usage.router)
router.include_router(open_documents.router)
