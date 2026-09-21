"""
TextMirror 开放 API 公共设施
错误契约（422/500 处理器）、错误响应示例、配额检查、幂等键工具。
被 open / open_polish / open_usage / open_documents 各模块共用。
"""
import json
from typing import Optional

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import (
    charge_api_key_daily,
    charge_user_daily_quota,
    check_api_key_rpm,
    refund_user_daily_quota,
)
from app.models.uploaded_document import UploadedDocument
from app.schemas.open import OpenDocumentSubmitResponse

_IDEMPOTENCY_TTL = 86400


def _sync_idempotency_cache_key(scope: str, hashed_key: str) -> str:
    return f"open:idemp:{scope}:{hashed_key}"


async def check_sync_idempotency(scope: str, raw_key: str | None) -> dict | None:
    """查询同步端点的幂等缓存（24h TTL）。raw_key 为 None 时直接返回 None。"""
    if not raw_key:
        return None
    from app.core.redis import get_redis
    from app.core.security import hash_scoped_idempotency_key

    hashed = hash_scoped_idempotency_key(scope, raw_key)
    cache_key = _sync_idempotency_cache_key(scope, hashed)
    try:
        cached = await get_redis().get(cache_key)
    except Exception as e:
        logger.warning(f"[OpenAPI] 幂等缓存读取失败: {e}")
        return None
    if cached:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


async def store_sync_idempotency(scope: str, raw_key: str | None, response_body: dict) -> None:
    """将同步端点响应写入幂等缓存（24h TTL）。raw_key 为 None 时跳过。"""
    if not raw_key:
        return
    from app.core.redis import get_redis
    from app.core.security import hash_scoped_idempotency_key

    hashed = hash_scoped_idempotency_key(scope, raw_key)
    cache_key = _sync_idempotency_cache_key(scope, hashed)
    try:
        await get_redis().set(cache_key, json.dumps(response_body, ensure_ascii=False), ex=_IDEMPOTENCY_TTL)
    except Exception as e:
        logger.warning(f"[OpenAPI] 幂等缓存写入失败: {e}")


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


async def _charge_user_quota_contract(user, n: int = 1) -> str | None:
    """返回实际预扣 key，429 转换为 code+message 契约（n>1 为多模型对比权重）。"""
    try:
        return await charge_user_daily_quota(user, n)
    except HTTPException as e:
        if e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "QUOTA_EXCEEDED", "message": str(e.detail)},
            )
        raise


async def _open_billing(user, api_key, weight: int = 1) -> None:
    """开放 API 统一计费顺序：RPM → 用户配额预扣 → 密钥日配额预扣（weight=本次消耗额度数）。

    密钥配额拒绝时退还用户预扣——本次请求未获得服务，用户额度不应消耗。
    """
    if api_key is not None:
        await check_api_key_rpm(api_key)
    await _charge_user_quota_contract(user, weight)
    if api_key is not None:
        try:
            await charge_api_key_daily(api_key, weight)
        except HTTPException:
            await refund_user_daily_quota(user, weight)
            raise


