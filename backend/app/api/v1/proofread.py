"""
TextMirror 文本校对 API
"""
import json
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_current_user_optional, require_permission
from app.core.exceptions import (
    AppException,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.rate_limit import (
    charge_user_daily_quota,
    check_guest_rate_limit,
    refund_user_daily_quota,
    reject_guest_if_disabled,
)
from app.models.proofread import ProofreadRecord
from app.schemas.collaboration import CollaborationCreate, CollaborationSubmit
from app.schemas.proofread import TextProofreadRequest, TextProofreadResponse
from app.services.audit_log import AuditTimer, record_audit_log
from app.services.proofread import InvalidModelConfigError, proofread_text

router = APIRouter(prefix="/proofread", tags=["校对"])


@router.post("/collaborate", response_model=CollaborationSubmit, status_code=202, summary="提交角色协作审校（每任务一次额度）")
async def collaborate(
    data: CollaborationCreate, db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("proofread:text")),
):
    import hashlib
    from uuid import uuid4

    from sqlalchemy import select, update
    from sqlalchemy.exc import IntegrityError
    from starlette.concurrency import run_in_threadpool

    from app.models.llm_config import LLMConfig
    from app.models.proofread_task import ProofreadTask
    from app.models.user import User
    from app.services.collaboration import initial_report
    from app.tasks.collaboration_task import (
        ACTIVE,
        async_collaboration,
        expire_collaboration_task,
        refund_collaboration_quota,
    )

    request_hash = hashlib.sha256(json.dumps(
        data.model_dump(mode="json", exclude={"request_id"}), sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    key = hashlib.sha256(f"collaboration:{user.id}:{data.request_id}".encode()).hexdigest()
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    existing = await db.scalar(select(ProofreadTask).where(ProofreadTask.idempotency_key == key))
    if existing:
        if (existing.params_json or {}).get("request_hash") != request_hash:
            raise ConflictError(code="REQUEST_ID_REUSE", message="此请求 ID 已用于不同审校参数，请生成新的请求 ID")
        return CollaborationSubmit(task_id=existing.task_id, message="已提交，请恢复任务进度")
    active_query = select(ProofreadTask).where(
        ProofreadTask.owner_user_id == user.id, ProofreadTask.owner_kind == "user",
        ProofreadTask.params_json["kind"].as_string() == "collaboration", ProofreadTask.status.in_(ACTIVE),
    )
    for active in (await db.scalars(active_query)).all():
        await run_in_threadpool(expire_collaboration_task, active.id)
    if await db.scalar(active_query.execution_options(populate_existing=True)):
        raise ConflictError(code="COLLABORATION_IN_PROGRESS", message="已有协作审校正在执行，请先恢复进度或取消")
    query = select(LLMConfig).where(LLMConfig.is_enabled.is_(True))
    query = query.where(LLMConfig.id == data.config_id) if data.config_id else query.where(LLMConfig.is_active.is_(True))
    config = await db.scalar(query)
    from app.core.secret_crypto import decrypt_secret

    if not config or not decrypt_secret(config.api_key).strip():
        raise ValidationError(code="INVALID_MODEL_CONFIG", message="所选模型不存在、已停用或缺少可用密钥，请检查模型配置")
    task_id = str(uuid4())
    quota_key = await charge_user_daily_quota(user)
    task = ProofreadTask(
        task_id=task_id, owner_kind="user", owner_user_id=user.id,
        idempotency_key=key, status="PENDING", phase="collaboration", message="协作审校排队中",
        params_json={"kind": "collaboration", "text": data.text, "domain": data.domain,
                     "config_id": config.id, "request_hash": request_hash, "quota_key": quota_key},
        result_json={"collaboration": initial_report(config.id, config.model)},
    )
    db.add(task)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await run_in_threadpool(refund_collaboration_quota, task_id, quota_key)
        if isinstance(exc, IntegrityError):
            raise ConflictError(code="DUPLICATE_REQUEST", message="请求已提交，请使用同一请求 ID 恢复任务") from None
        raise
    await db.refresh(task)
    try:
        await run_in_threadpool(async_collaboration.apply_async, args=[task.id], task_id=task_id, retry=False)
    except Exception as exc:
        from datetime import datetime, timezone

        from app.tasks.collaboration_task import terminal_report

        logger.warning("协作任务投递失败 task={} error={}", task.id, type(exc).__name__)
        message = "任务队列暂时不可用，请稍后重新提交"
        marked = await db.execute(update(ProofreadTask).where(
            ProofreadTask.id == task.id, ProofreadTask.status == "PENDING",
        ).values(status="FAILURE", error_code="QUEUE_UNAVAILABLE", message=message,
                 result_json={"collaboration": terminal_report(task.result_json["collaboration"], "FAILURE", message)},
                 finished_at=datetime.now(timezone.utc)))
        await db.commit()
        if marked.rowcount:
            await run_in_threadpool(refund_collaboration_quota, task_id, quota_key)
    return CollaborationSubmit(task_id=task_id, message="已提交协作审校")


@router.get("/collaborate/{task_id}", summary="恢复本人协作任务的原文快照")
async def collaboration_input(
    task_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user),
):
    from sqlalchemy import select

    from app.models.proofread_task import ProofreadTask

    task = await db.scalar(select(ProofreadTask).where(
        ProofreadTask.task_id == task_id, ProofreadTask.owner_kind == "user",
        ProofreadTask.owner_user_id == user.id, ProofreadTask.params_json["kind"].as_string() == "collaboration",
    ))
    if task is None:
        raise NotFoundError(code="TASK_NOT_FOUND", message="协作任务不存在")
    return {"task_id": task_id, **{key: task.params_json[key] for key in ("text", "domain", "config_id")}}


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
    quota_key = None
    if current_user is None:
        await reject_guest_if_disabled(http_request)
        from app.services.guest_policy import get_guest_policy
        guest_policy = await get_guest_policy()
        # 先校验长度：非法请求不消耗游客当日次数
        max_text_length = guest_policy["max_text_length"]
        if len(request.text) > max_text_length:
            raise BadRequestError(code="GUEST_TEXT_TOO_LONG", message=f"游客模式文本长度不能超过{max_text_length}字，请登录后使用")
        await check_guest_rate_limit(http_request, daily_limit=guest_policy["daily_limit"])
    else:
        quota_key = await charge_user_daily_quota(current_user)

    timer = AuditTimer()
    timer.start()
    audit_extra = {"check_types": request.check_types, "domain": request.domain}

    try:
        # 调用校对服务
        result = await proofread_text(
            text=request.text,
            domain=request.domain,
            config_id=request.config_id,
            user_id=current_user.id if current_user else None,
            depth=request.depth,
        )
    except RuntimeError as e:
        import traceback
        logger.error(f"校对服务异常: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "proofread_text", user=current_user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        # 没拿到结果不消耗额度
        await refund_user_daily_quota(quota_key)
        # 指定的模型配置无效：明确告知（通常是配置被删除/停用）
        invalid_config = isinstance(e, InvalidModelConfigError)
        detail = str(e) if invalid_config else "校对服务暂时不可用，请稍后重试"
        if invalid_config:
            raise BadRequestError(code="INVALID_MODEL_CONFIG", message=detail)
        raise ServiceUnavailableError(code="PROOFREAD_SERVICE_ERROR", message=detail)
    except Exception as e:
        import traceback
        logger.error(f"校对过程发生未知错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "proofread_text", user=current_user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        await refund_user_daily_quota(quota_key)
        raise AppException(500, "PROOFREAD_UNEXPECTED_ERROR", "校对过程发生错误，请稍后重试")

    # 保存校对记录（已登录用户）
    record_id = None
    if current_user:
        record = ProofreadRecord(
            user_id=current_user.id,
            type="text",
            original_text=request.text,
            check_types=json.dumps(request.check_types or []),
            domain=result["domain"],
            result=result,
            total_issues=result["total_issues"],
            token_usage=result["usage"],
        )
        db.add(record)
        await db.flush()
        record_id = record.id

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

    return TextProofreadResponse(**result, record_id=record_id)


# ======================================================================
# 多模型并发校对对比
# ======================================================================

class ProofreadCompareRequest(BaseModel):
    """多模型校对对比请求"""
    text: str = Field(..., min_length=1, max_length=100000, description="待校对文本")
    check_types: Optional[List[str]] = Field(
        None,
        deprecated=True,
        description="（已废弃，传入无效果）历史参数：限定校对类型。总是全量审校，任何值都被静默忽略",
    )
    domain: str = Field(default="general", description="领域")
    config_ids: List[int] = Field(..., min_length=2, max_length=4, description="参与对比的模型配置ID")

    @field_validator("check_types", mode="before")
    @classmethod
    def _ignore_check_types(cls, v):
        return None


class ModelProofreadResult(TextProofreadResponse):
    """单模型结果；success 包含部分成功，complete 才表示完整审校。"""
    config_id: int
    config_name: str
    model: str
    success: bool = True
    complete: bool = False
    error: Optional[str] = None
    elapsed_ms: int = 0


class ProofreadCompareResponse(BaseModel):
    """多模型校对对比响应"""
    record_id: int | None = None
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
    if current_user is None:
        await reject_guest_if_disabled(http_request)
        await check_guest_rate_limit(http_request)

    # 加载模型配置（仅启用的可参与对比）
    from sqlalchemy import select as _select

    from app.models.llm_config import LLMConfig
    cfg_result = await db.execute(
        _select(LLMConfig).where(
            LLMConfig.id.in_(request.config_ids),
            LLMConfig.is_enabled.is_(True),
        )
    )
    configs = {c.id: c for c in cfg_result.scalars().all()}
    if len(configs) < 2:
        raise BadRequestError(code="INSUFFICIENT_MODELS", message="所选模型配置不足 2 个有效项（已停用的配置不可用）")

    # 对比一次消耗 N 倍额度（N=有效模型数，重复/无效 config_id 不重复计量）：原子预扣
    quota_key = await charge_user_daily_quota(current_user, len(configs))

    timer = AuditTimer()
    timer.start()

    from app.services.model_compare import run_proofread_compare
    try:
        raw_items, consensus, only_in = await run_proofread_compare(
            text=request.text,
            domain=request.domain,
            config_ids=request.config_ids,
            configs=configs,
            user_id=current_user.id if current_user else None,
        )
    except Exception:
        # 整体异常（模型加载等前置失败）：全额退还预扣
        await refund_user_daily_quota(quota_key, len(configs))
        raise
    items = [ModelProofreadResult(**i) for i in raw_items]

    # 失败模型不消耗额度（与密钥日配额按成功数结算同口径）
    failed = sum(1 for i in items if not i.success)
    if failed > 0:
        await refund_user_daily_quota(quota_key, failed)

    # 落带权重的对比记录：配额从「只预检不消耗」改为真正计量（与开放 API 同口径）。
    # 游客不落（游客配额走 IP 限流）；全部失败不落（零消耗）；
    # 问题按 (original, suggestion) 去重——同一错误多模型发现只留一条（带 found_by）
    record_id = None
    if current_user:
        successes = [i for i in items if i.success]
        if successes:
            from app.services.model_compare import dedupe_compare_issues

            merged_issues = dedupe_compare_issues([
                {"config_name": i.config_name, "issues": [issue.model_dump() for issue in i.issues]}
                for i in successes
            ])
            record = ProofreadRecord(
                user_id=current_user.id,
                type="text",
                original_text=request.text,
                check_types=json.dumps([]),
                domain=successes[0].domain,
                result={
                    "compare": True,
                    "models": [i.config_name for i in items],
                    "results": [i.model_dump() for i in items],
                    "domain": successes[0].domain,
                    "depth": successes[0].depth,
                    "consensus_originals": consensus,
                    "only_in": only_in,
                    "issues": merged_issues,
                    "issues_per_model": {str(i.config_id): i.total_issues for i in items},
                },
                total_issues=len(merged_issues),
                quota_weight=len(successes),
            )
            db.add(record)
            await db.flush()
            record_id = record.id

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
        record_id=record_id,
        results=items,
        consensus_originals=consensus,
        only_in=only_in,
    )


# ======================================================================
# 审校建议反馈（接受/忽略）——词库优化数据飞轮
# ======================================================================

class IssueFeedbackItem(BaseModel):
    """单条反馈"""
    original: str = Field(..., max_length=500, description="问题原文片段")
    suggestion: Optional[str] = Field(None, max_length=500, description="修改建议")
    issue_type: Optional[str] = Field(None, max_length=20, description="问题类型")
    action: str = Field(..., pattern="^(accept|ignore)$", description="动作: accept/ignore")


class IssueFeedbackRequest(BaseModel):
    """反馈上报请求"""
    record_id: Optional[int] = Field(None, description="校对记录ID（开放API调用可无）")
    items: List[IssueFeedbackItem] = Field(..., min_length=1, max_length=50)


@router.post("/feedback", summary='上报审校建议反馈（接受/忽略）')
async def submit_issue_feedback(
    request: IssueFeedbackRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """
    上报用户对审校建议的接受/忽略行为（fire-and-forget，前端不阻塞交互）。
    仅登录用户记录（游客无归属意义）；数据用于后续词库优化（高忽略率→放行词候选）。
    """
    from app.models.issue_feedback import IssueFeedback

    if current_user is None:
        return {"saved": 0}

    # 归属校验：否则可给他人记录写反馈，污染词库建议飞轮
    if request.record_id is not None:
        from sqlalchemy import select
        owner = await db.execute(
            select(ProofreadRecord.user_id).where(ProofreadRecord.id == request.record_id)
        )
        owner_id = owner.scalar_one_or_none()
        if owner_id is None:
            raise NotFoundError(code="RECORD_NOT_FOUND", message="校对记录不存在")
        if owner_id != current_user.id:
            raise ForbiddenError(code="FEEDBACK_NOT_ALLOWED", message="无权对该记录提交反馈")

    saved = 0
    for item in request.items:
        db.add(IssueFeedback(
            record_id=request.record_id,
            user_id=current_user.id,
            original=item.original,
            suggestion=item.suggestion,
            issue_type=item.issue_type,
            action=item.action,
        ))
        saved += 1
    await db.flush()

    logger.info(f"[反馈] user={current_user.id} record={request.record_id} 条数={saved}")
    return {"saved": saved}
