"""
TextMirror 开放 API（对外稳定契约）
认证：Authorization: Bearer tm_...（API 密钥，个人中心创建）或 JWT 登录 Token
错误契约：非 2xx 响应的 detail 为 {"code": "...", "message": "..."}（含 422，见 validation_exception_handler）
"""
import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, get_db
from app.core.dependencies import get_current_user_or_apikey
from app.core.file_security import sanitize_filename
from app.core.rate_limit import (
    charge_api_key_daily,
    check_api_key_rpm,
    check_upload_rate_limit,
    check_user_quota,
    check_user_quota_n_times,
    refund_api_key_daily_usage,
)
from app.core.security import hash_scoped_idempotency_key
from app.models.api_key import ApiKey
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.uploaded_document import UploadedDocument
from app.models.user import User
from app.schemas.open import (
    OpenCompareModelResult,
    OpenCompareRequest,
    OpenCompareResponse,
    OpenDocumentSubmitResponse,
    OpenJobStatusResponse,
    OpenModelsResponse,
    OpenPolishRequest,
    OpenPolishResponse,
    OpenUsageDailyItem,
    OpenUsageKeyItem,
    OpenUsageResponse,
)
from app.schemas.polish import PolishVersion
from app.schemas.proofread import (
    Domain,
    ProofreadIssue,
    TextProofreadRequest,
    TextProofreadResponse,
)
from app.services.audit_log import AuditTimer, record_audit_log
from app.services.document import extract_text_from_file
from app.services.polish import (
    VERSION_INSTRUCTIONS,
    estimate_tokens_by_chars,
    polish_text,
    polish_text_stream,
)
from app.services.proofread import proofread_text
from app.services.upload import UploadRejected, remove_upload_silently, store_upload
from app.tasks.proofread_task import async_proofread_document

router = APIRouter(tags=["开放API"])


def _normalize_idempotency_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > 128:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_IDEMPOTENCY_KEY", "message": "Idempotency-Key 必须为 1-128 个非空字符"},
        )
    return value


async def _existing_submit_response(db: AsyncSession, db_task) -> OpenDocumentSubmitResponse:
    doc_record = (await db.execute(
        select(UploadedDocument).where(UploadedDocument.file_id == db_task.document_id)
    )).scalar_one_or_none()
    if doc_record is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": "已有任务的文档记录不可用"},
        )
    return OpenDocumentSubmitResponse(
        job_id=db_task.task_id,
        filename=doc_record.filename,
        text_length=doc_record.text_length,
        status="queued",
        status_url=f"/api/v1/open/jobs/{db_task.task_id}",
    )


# 错误响应示例（对外契约的一部分，写进 OpenAPI 文档）
def _error_example(code: str, msg: str) -> dict:
    return {
        "description": msg,
        "content": {"application/json": {"example": {"detail": {"code": code, "message": msg}}}},
    }

ERROR_RESPONSES = {
    400: _error_example("INVALID_CONFIG", "指定的模型配置不存在或已停用"),
    401: _error_example("UNAUTHORIZED", "未提供认证凭证 / API 密钥无效"),
    403: _error_example("API_KEY_REVOKED", "密钥已吊销 / 已过期 / 账号被禁用"),
    422: _error_example("VALIDATION_ERROR", "参数错误：domain 非法值"),
    429: _error_example("RATE_LIMITED", "频率超限（每分钟12次）或配额用尽"),
    503: _error_example("MODEL_UNAVAILABLE", "审校服务暂时不可用，请稍后重试"),
}

DOC_ERROR_RESPONSES = {
    **ERROR_RESPONSES,
    400: _error_example("INVALID_FILE", "文件格式不支持 / 文件损坏 / 未提取到文本"),
    503: _error_example("TASK_QUEUE_UNAVAILABLE", "任务队列暂时不可用，请稍后重试"),
}

JOBS_ERROR_RESPONSES = {
    **ERROR_RESPONSES,
    404: _error_example("JOB_NOT_FOUND", "任务不存在"),
}


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    422 参数校验错误转换为本 API 的 code+message 契约
    （默认的 errors 数组格式对外部集成方不友好，且与其他错误格式不一致）
    """
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(part) for part in first.get("loc", []) if part not in ("body", "form"))
    msg = first.get("msg", "请求参数错误")
    message = f"参数错误：{loc} {msg}" if loc else f"参数错误：{msg}"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": {"code": "VALIDATION_ERROR", "message": message}},
    )


async def internal_exception_handler(request: Request, exc: Exception):
    """
    子应用兜底 500：未捕获异常也保持 code+message 契约
    （默认的 "Internal Server Error" 纯文本不符合对外 API 格式）
    """
    logger.error(f"[OpenAPI] 未捕获异常 {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": {"code": "INTERNAL_ERROR", "message": "服务器内部错误，请稍后重试"}},
    )


async def _check_user_quota_contract(user, db: AsyncSession, n: int = 1) -> None:
    """用户每日配额检查，429 转换为 code+message 契约（n>1 为多模型对比预检）"""
    try:
        if n > 1:
            await check_user_quota_n_times(user, db, n)
        else:
            await check_user_quota(user, db)
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "QUOTA_EXCEEDED", "message": str(e.detail)},
            )
        raise


def _parse_form_check_types(raw: Optional[str]) -> Optional[List[str]]:
    """已废弃参数：任何输入静默忽略（与「传入无效果」承诺自洽），仅保留签名兼容"""
    return None


def _validate_form_domain(raw: str) -> str:
    valid = {d.value for d in Domain}
    if raw not in valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": f"domain 非法: {raw}（可选: {', '.join(sorted(valid))}）"},
        )
    return raw


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


# ======================================================================
# 多模型并发对比
# ======================================================================

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


@router.get(
    "/usage",
    response_model=OpenUsageResponse,
    summary="用量统计",
    description=(
        "查询 API 调用用量（近 N 天，按日/按密钥聚合）。\n\n"
        "**统计口径**：成功调用次数（失败/退还额度的调用不计）。"
        "多模型对比一次请求按成功模型数计。日期按 Asia/Shanghai 业务时区切日。\n\n"
        "**范围**：API 密钥调用 → 该密钥的用量；JWT 登录 Token 调用 → 名下全部密钥的合计。\n\n"
        "```bash\n"
        'curl -H "Authorization: Bearer tm_..." ".../api/v1/open/usage?days=7"\n'
        "```"
    ),
    responses=ERROR_RESPONSES,
)
async def open_usage(
    days: int = Query(7, ge=1, le=90, description="统计周期（天），默认 7"),
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """开放 API 用量统计（只读，不计费不限流）"""
    user, api_key = auth
    tz = ZoneInfo("Asia/Shanghai")
    now_local = datetime.now(tz)
    start_local = (now_local - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    base_filters = [ProofreadRecord.api_key_id.is_not(None)]
    if api_key is not None:
        scope = "api_key"
        base_filters.append(ProofreadRecord.api_key_id == api_key.id)
    else:
        scope = "user"
        base_filters.append(ProofreadRecord.api_key_id.in_(
            select(ApiKey.id).where(ApiKey.user_id == user.id)
        ))

    # 按日聚合：日期边界在 Python 侧算好（Asia/Shanghai），SQL 只做范围计数——
    # 与配额检查同模式，兼容 PostgreSQL 与 SQLite，且每个查询都走 (api_key_id, created_at) 索引
    daily = []
    for i in range(days):
        day_start_local = start_local + timedelta(days=i)
        day_start_utc = day_start_local.astimezone(timezone.utc)
        day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc)
        count = (await db.execute(
            select(func.count()).select_from(ProofreadRecord).where(
                *base_filters,
                ProofreadRecord.created_at >= day_start_utc,
                ProofreadRecord.created_at < day_end_utc,
            )
        )).scalar() or 0
        daily.append(OpenUsageDailyItem(date=day_start_local.strftime("%Y-%m-%d"), count=count))

    key_rows = (await db.execute(
        select(
            ApiKey.id,
            ApiKey.name,
            ApiKey.key_prefix,
            ApiKey.key_suffix,
            func.count().label("count"),
        )
        .join(ProofreadRecord, ProofreadRecord.api_key_id == ApiKey.id)
        .where(*base_filters, ProofreadRecord.created_at >= start_local.astimezone(timezone.utc))
        .group_by(ApiKey.id, ApiKey.name, ApiKey.key_prefix, ApiKey.key_suffix)
        .order_by(func.count().desc())
    )).all()
    keys = [
        OpenUsageKeyItem(
            key_id=r.id,
            key_display=f"{r.key_prefix}...{r.key_suffix}",
            key_name=r.name,
            count=r.count,
        )
        for r in key_rows
    ]

    return OpenUsageResponse(
        days=days,
        total=sum(d.count for d in daily),
        daily=daily,
        keys=keys,
        scope=scope,
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

    record_audit_log(
        http_request, "api_proofread_compare", user=user,
        input_text=request.text,
        extra_params={**audit_extra, "issues_per_model": {str(i.config_id): i.total_issues for i in items}},
        duration_ms=timer.elapsed_ms(),
    )

    return OpenCompareResponse(results=items, consensus_originals=consensus, only_in=only_in)


# ======================================================================
# AI 润色
# ======================================================================

async def _polish_billing(user, api_key, db: AsyncSession) -> None:
    """润色与审校同一计费顺序：RPM → 用户配额（免费检查）→ 密钥日配额（weight=1，与 Web 端单记录口径一致）"""
    if api_key is not None:
        await check_api_key_rpm(api_key)
    await _check_user_quota_contract(user, db)
    if api_key is not None:
        await charge_api_key_daily(api_key)


def _build_polish_record(
    user_id: int,
    api_key_id: Optional[int],
    text: str,
    style: str,
    style_name: str,
    versions: list,
    usage,
) -> ProofreadRecord:
    modified_text = "\n\n---\n\n".join(
        f"【{v['label']}】\n{v['content']}" for v in versions
    )
    return ProofreadRecord(
        user_id=user_id,
        api_key_id=api_key_id,
        type="polish",
        original_text=text,
        check_types=json.dumps([style]),
        domain=style,
        result={"versions": versions, "style": style, "style_name": style_name},
        modified_text=modified_text,
        total_issues=0,
        token_usage=usage,
    )


@router.post(
    "/polish",
    response_model=OpenPolishResponse,
    summary="AI 文本润色",
    description=(
        "对文本进行 AI 润色，返回三个版本（轻量/标准/深度改动）。\n\n"
        "**快速开始**：只需传 `text`，`style` 默认 formal（正式规范）——\n"
        "```json\n"
        '{"text": "这段文字需要更加正式的表达方式来呈现。"}\n'
        "```\n\n"
        "**style 可选值**（10 种）：formal 正式规范 / friendly 亲和自然 / plain 通俗易懂 /"
        " concise 精炼简洁 / evidence 论证充分 / strategic 战略高度 / practical 落地实操 /"
        " firm 严谨有力 / gentle 委婉得体 / action 行动导向\n\n"
        "**单版本失败**：对应 version 的 content 为占位提示文本，不影响其他版本，额度不退"
        "（可重试）；服务整体不可用返回 503 并退还当日额度。\n\n"
        "**限制**：文本 10-5000 字；限流/配额与审校端点同一口径（每分钟 12 次、每日配额随账号）。"
    ),
    responses=ERROR_RESPONSES,
)
async def open_polish(
    request: OpenPolishRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """开放 AI 润色端点（复用 Web 端同一润色服务，三版本并发）"""
    user, api_key = auth
    await _polish_billing(user, api_key, db)

    timer = AuditTimer()
    timer.start()
    audit_extra = {"style": request.style}
    if api_key is not None:
        audit_extra["api_key_id"] = api_key.id

    try:
        result = await polish_text(text=request.text, style=request.style)
    except RuntimeError as e:
        import traceback
        logger.error(f"[OpenAPI] 润色服务异常: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "api_polish", user=user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "MODEL_UNAVAILABLE", "message": "润色服务暂时不可用，请稍后重试"},
        )
    except Exception as e:
        import traceback
        logger.error(f"[OpenAPI] 润色未知错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        record_audit_log(
            http_request, "api_polish", user=user,
            input_text=request.text, extra_params=audit_extra,
            status="failed", error_message=str(e), duration_ms=timer.elapsed_ms(),
        )
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": "润色过程发生错误，请稍后重试"},
        )

    versions = [
        PolishVersion(
            label=v["label"],
            level=v["level"],
            content=v["content"],
            **({"sensitive_words": v["sensitive_words"]} if v.get("sensitive_words") else {}),
        )
        for v in result["versions"]
    ]

    # 落库走请求作用域会话（与 Web 端同步润色同构）：API 调用必须入库——
    # 用户配额按记录计数 + 用量统计归属
    db.add(_build_polish_record(
        user_id=user.id,
        api_key_id=api_key.id if api_key is not None else None,
        text=request.text,
        style=request.style,
        style_name=result["style_name"],
        versions=result["versions"],
        usage=result.get("usage"),
    ))
    await db.flush()

    record_audit_log(
        http_request, "api_polish", user=user,
        input_text=request.text,
        output_text="; ".join(v["content"][:100] for v in result["versions"]),
        extra_params={**audit_extra, "style_name": result["style_name"]},
        token_usage=result.get("usage"),
        duration_ms=timer.elapsed_ms(),
    )

    return OpenPolishResponse(
        versions=versions,
        style=result["style"],
        style_name=result["style_name"],
        usage=result["usage"],
    )


@router.post(
    "/polish/stream",
    summary="AI 文本润色（流式 SSE）",
    description=(
        "同 `POST /polish`，但以 SSE 事件流逐字返回，适合前端实时渲染场景。\n\n"
        "**事件流**：`meta`（风格信息）→ `start`（版本开始：level=light/standard/deep）→"
        " `delta`（增量文本，三版本交错）→ `done`（版本完整文本）→ `end`（结束）。\n\n"
        "单版本失败发 `error` 事件（带 level），流正常结束；整体故障发 `fatal` 事件。\n\n"
        "**计费**：与同步版一致（开始流式前完成限流与扣额；一个版本都没产出即 fatal 时退还当日额度）。"
        "鉴权/参数错误在流开始前按统一错误契约返回（HTTP 状态码 + `detail.code`）。\n\n"
        "```bash\n"
        'curl -N -X POST .../api/v1/open/polish/stream -H "Authorization: Bearer tm_..." \\\n'
        '  -H "Content-Type: application/json" -d \'{"text": "...", "style": "formal"}\'\n'
        "```"
    ),
    responses=ERROR_RESPONSES,
)
async def open_polish_stream(
    request: OpenPolishRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """开放 AI 润色流式端点（SSE，事件流与 Web 端 /polish/text/stream 同构）"""
    user, api_key = auth

    # 限流/配额/扣额在流开始前完成（流式响应无法回传 HTTP 错误码）
    async with async_session_factory() as billing_db:
        await _polish_billing(user, api_key, billing_db)

    # 结束鉴权依赖遗留的读事务再开流：db 与鉴权依赖是同一缓存会话，
    # 流结束才随请求关闭，SQLite 下挂着读锁会让流内落库 database is locked（PG 无此问题）
    await db.commit()

    style = request.style
    timer = AuditTimer()
    timer.start()
    user_id = user.id
    api_key_id = api_key.id if api_key is not None else None
    audit_extra = {"style": style, "stream": True}
    if api_key is not None:
        audit_extra["api_key_id"] = api_key.id

    async def event_stream():
        versions: dict = {}
        style_name = ""
        fatal = False
        try:
            async for evt in polish_text_stream(text=request.text, style=style):
                if evt["event"] == "meta":
                    style_name = evt.get("style_name", "")
                if evt["event"] == "done":
                    versions[evt["level"]] = {
                        "label": VERSION_INSTRUCTIONS[evt["level"]]["label"],
                        "level": evt["level"],
                        "content": evt["content"],
                    }
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:
            import traceback
            logger.error(f"[OpenAPI] 润色流式异常: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            fatal = True
            yield f"data: {json.dumps({'event': 'fatal', 'message': '润色服务暂时不可用，请稍后重试'}, ensure_ascii=False)}\n\n"

        # 流结束后：零产出退还当日额度 → 落库 → 审计
        try:
            if fatal and not versions and api_key is not None:
                await refund_api_key_daily_usage(api_key)
            if versions:
                # 流式收尾在响应流内执行，用独立会话落库（请求会话此刻仍被流持有）
                async with async_session_factory() as db:
                    db.add(_build_polish_record(
                        user_id=user_id,
                        api_key_id=api_key_id,
                        text=request.text,
                        style=style,
                        style_name=style_name,
                        versions=list(versions.values()),
                        usage=estimate_tokens_by_chars(
                            len(request.text) + sum(len(v["content"]) for v in versions.values())
                        ),
                    ))
                    await db.commit()
            record_audit_log(
                http_request, "api_polish", user=user,
                input_text=request.text,
                output_text="; ".join(v["content"][:100] for v in versions.values()),
                extra_params=audit_extra,
                status="failed" if fatal else "success",
                error_message="stream fatal" if fatal else None,
                duration_ms=timer.elapsed_ms(),
            )
        except Exception as e:
            logger.warning(f"[OpenAPI] 润色流式收尾（额度退还/记录/审计）失败: {e}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ======================================================================
# 异步文档审校
# ======================================================================

ALLOWED_DOC_EXTENSIONS = {".doc", ".docx", ".pdf", ".txt"}


@router.post(
    "/documents",
    response_model=OpenDocumentSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="文档审校（异步）",
    description=(
        "上传文档并提交异步审校任务，立即返回 `job_id`。\n\n"
        "支持格式：`.doc` / `.docx` / `.pdf` / `.txt`，大小上限 20MB。\n\n"
        "**快速开始**（curl）：\n"
        "```bash\n"
        'curl -X POST .../api/v1/open/documents -H "Authorization: Bearer tm_..." \\\n'
        '  -F "file=@报告.docx"\n'
        "```\n\n"
        "**表单参数**（均可选）：`domain`（默认 general）、`config_id`"
        "（`check_types` 已废弃，传入无效果）。\n\n"
        "之后轮询 `status_url`（即 `GET /open/jobs/{job_id}`），"
        "`status=SUCCESS` 时 `result` 字段含审校结果与修订文档下载地址。"
    ),
    responses=DOC_ERROR_RESPONSES,
)
async def open_submit_document(
    http_request: Request,
    file: UploadFile = File(..., description="待审校文档：.doc/.docx/.pdf/.txt"),
    check_types: Optional[str] = Form(None, deprecated=True, description="（已废弃，传入无效果）历史参数：校对类型"),
    domain: str = Form("general", description="文本领域"),
    config_id: Optional[int] = Form(None, description="指定模型配置ID（可选）"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    开放文档异步审校：上传 → 提交 Celery 任务 → 返回 job_id
    """
    user, api_key = auth
    raw_idempotency_key = _normalize_idempotency_key(idempotency_key)
    owner_scope = f"open:api-key:{api_key.id}" if api_key else f"open:user:{user.id}"
    scoped_idempotency_key = (
        hash_scoped_idempotency_key(owner_scope, raw_idempotency_key)
        if raw_idempotency_key else None
    )

    from app.models.proofread_task import ProofreadTask

    if scoped_idempotency_key:
        existing = (await db.execute(
            select(ProofreadTask).where(ProofreadTask.idempotency_key == scoped_idempotency_key)
        )).scalar_one_or_none()
        if existing:
            if (
                existing.status == "FAILURE"
                and existing.error_code == "DISPATCH_FAILED"
                and existing.started_at is None
            ):
                await db.execute(
                    update(ProofreadTask)
                    .where(ProofreadTask.id == existing.id, ProofreadTask.status == "FAILURE")
                    .values(status="PENDING", error_code=None, message="任务重新排队中...")
                )
                await db.commit()
                try:
                    async_proofread_document.apply_async(
                        args=(existing.id,),
                        task_id=existing.task_id,
                    )
                except Exception as e:
                    logger.warning(f"[OpenAPI] 幂等重试投递失败: {e}")
            return await _existing_submit_response(db, existing)

    parsed_check_types = _parse_form_check_types(check_types)
    parsed_domain = _validate_form_domain(domain)

    if api_key is not None:
        await check_api_key_rpm(api_key)

    # 上传频率限制（JWT 调用此前不受任何频率限制）
    await check_upload_rate_limit(http_request, user)

    # ---- 文件校验 ----
    if not file.filename:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件名不能为空"})
    filename = sanitize_filename(file.filename)
    if not filename:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件名不合法"})
    _, file_ext = os.path.splitext(filename)
    file_ext = file_ext.lower()
    if file_ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_FILE", "message": f"不支持的文件格式: {file_ext}，仅支持 .doc / .docx / .pdf / .txt"},
        )

    # ---- 分块落盘并校验内容（不把整个文件读进内存）----
    file_id = str(uuid.uuid4())
    try:
        stored = await store_upload(file, file_id, filename, file_ext)
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    file_path = stored.file_path
    file_size = stored.file_size

    # 此后任何失败路径都不该在磁盘留下孤儿文件
    try:
        try:
            extracted_text = await asyncio.to_thread(extract_text_from_file, file_path, file_ext)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": str(e)})
        except Exception as e:
            logger.error(f"[OpenAPI] 文档文本提取失败: {e}")
            raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "文档文本提取失败，请检查文件是否损坏"})
        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件中未提取到有效文本内容"})

        # ---- 配额（用户输入校验完成后才计费）----
        try:
            await _check_user_quota_contract(user, db)
        except HTTPException:
            raise
        if api_key is not None:
            await charge_api_key_daily(api_key, 1)
    except HTTPException:
        remove_upload_silently(file_path)
        raise
    except Exception:
        remove_upload_silently(file_path)
        raise

    # ---- 在同一事务中保存上传记录与持久任务 ----
    doc_record = UploadedDocument(
        file_id=file_id,
        filename=filename,
        file_ext=file_ext,
        file_size=file_size,
        file_path=file_path,
        text_length=len(extracted_text),
        extracted_text=extracted_text,
        user_id=user.id,
        username=user.username,
        owner_kind="user",
        status="uploaded",
    )
    task_uuid = str(uuid.uuid4())
    db_task = ProofreadTask(
        task_id=task_uuid,
        document_id=file_id,
        owner_kind="api_key" if api_key else "user",
        owner_user_id=user.id,
        owner_api_key_id=api_key.id if api_key else None,
        idempotency_key=scoped_idempotency_key,
        status="PENDING",
        progress=0,
        message="任务排队中...",
        params_json={
            "domain": parsed_domain,
            "config_id": config_id,
            "check_types": parsed_check_types,
        },
    )
    db.add_all([doc_record, db_task])
    try:
        await db.flush()
        db_task_pk_id = db_task.id
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        remove_upload_silently(file_path)
        if scoped_idempotency_key:
            existing = (await db.execute(
                select(ProofreadTask).where(ProofreadTask.idempotency_key == scoped_idempotency_key)
            )).scalar_one_or_none()
            if existing:
                return await _existing_submit_response(db, existing)
        logger.exception("[OpenAPI] 幂等任务记录冲突但未找到既有任务")
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": "任务记录保存失败，请重新提交"},
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"[OpenAPI] 上传/任务记录保存失败: {e}")
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        remove_upload_silently(file_path)
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": "任务记录保存失败，请重新提交"},
        )

    # ---- 投递 Celery（只传 DB 主键，worker 自行加载全文） ----
    try:
        async_proofread_document.apply_async(
            args=(db_task_pk_id,),
            task_id=task_uuid,
        )
    except Exception as e:
        logger.error(f"[OpenAPI] 任务队列不可用: {e}")
        await db.execute(
            update(ProofreadTask)
            .where(ProofreadTask.id == db_task_pk_id, ProofreadTask.status == "PENDING")
            .values(status="FAILURE", error_code="DISPATCH_FAILED", message="任务投递失败，请重试")
        )
        await db.commit()
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "TASK_QUEUE_UNAVAILABLE", "message": "任务队列暂时不可用，请稍后重试"},
        )

    record_audit_log(
        http_request, "api_proofread_doc", user=user,
        input_text=extracted_text[:200],
        extra_params={
            "action": "submit",
            "text_length": len(extracted_text),
            "domain": parsed_domain,
            "check_types": parsed_check_types,
            "job_id": task_uuid,
        },
        file_id=file_id,
        file_name=filename,
        file_path=file_path,
        file_size=file_size,
    )

    return OpenDocumentSubmitResponse(
        job_id=task_uuid,
        filename=filename,
        text_length=len(extracted_text),
        status="queued",
        status_url=f"/api/v1/open/jobs/{task_uuid}",
    )


async def _load_db_task(job_id: str, db: AsyncSession):
    """从 proofread_tasks 加载任务记录"""
    from app.models.proofread_task import ProofreadTask
    result = await db.execute(
        select(ProofreadTask).where(ProofreadTask.task_id == job_id)
    )
    return result.scalar_one_or_none()


def _build_job_payload_from_db(job_id: str, db_task) -> OpenJobStatusResponse:
    """从 proofread_tasks 记录构造任务状态响应"""
    if db_task is None:
        return OpenJobStatusResponse(
            job_id=job_id, status="PENDING", progress=0, message="任务排队中...",
        )
    status = db_task.status
    if status in ("STARTED", "PROGRESS"):
        display_status = "PROGRESS"
    elif status == "SUCCESS":
        display_status = "SUCCESS"
    elif status in ("FAILURE", "CANCELLED", "REVOKED"):
        display_status = "FAILURE"
    else:
        display_status = status

    payload = OpenJobStatusResponse(
        job_id=job_id,
        status=display_status,
        progress=db_task.progress or 0,
        message=db_task.message or "",
    )
    if display_status == "SUCCESS" and db_task.result_json:
        data = dict(db_task.result_json)
        data.pop("user_id", None)
        payload.result = data or None
    elif display_status == "FAILURE":
        payload.error = db_task.error_code or "TASK_FAILED"
    return payload


@router.get(
    "/jobs/{job_id}",
    response_model=OpenJobStatusResponse,
    summary="查询任务状态",
    description=(
        "轮询异步文档审校任务：\n\n"
        "- `PENDING`：排队中（提交后立即轮询会出现）\n"
        "- `PROGRESS`：处理中，`progress` 为百分比\n"
        "- `SUCCESS`：完成，`result` 含 issues / total_issues / corrected_download_url（修订文档下载地址，相对路径，带签名有时效）\n"
        "- `FAILURE`：失败（`error=TASK_FAILED`），失败任务不消耗密钥当日配额\n\n"
        "建议轮询间隔 2-5 秒。"
    ),
    responses=JOBS_ERROR_RESPONSES,
)
async def open_get_job(
    job_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    查询异步任务状态（仅任务提交者可查看，归属从 proofread_tasks 表校验）
    """
    user, api_key = auth

    db_task = await _load_db_task(job_id, db)
    if db_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
        )

    # 归属校验
    if db_task.owner_kind == "api_key":
        if api_key is None or db_task.owner_api_key_id != api_key.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
            )
    elif db_task.owner_kind == "user":
        if user.id != db_task.owner_user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
        )

    return _build_job_payload_from_db(job_id, db_task)
