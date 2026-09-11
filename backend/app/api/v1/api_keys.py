"""
TextMirror API 密钥自助管理
创建/列表/吊销，仅支持 JWT 登录态（API 密钥本身不能管理密钥，防止泄漏后自我复制）
"""
from datetime import datetime, timedelta, timezone
from typing import List
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.rate_limit import get_api_key_daily_usage
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.proofread import ProofreadRecord
from app.models.user import User
from app.schemas.api_key import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyItem,
    ApiKeyListResponse,
    ApiKeyWebhookSetRequest,
    ApiKeyWebhookSetResponse,
)
from app.services.audit_log import record_audit_log

router = APIRouter(prefix="/api-keys", tags=["API密钥"])


def _key_display(api_key: ApiKey) -> str:
    return f"{api_key.key_prefix}...{api_key.key_suffix}"


def compute_key_status(api_key: ApiKey) -> str:
    if not api_key.is_active:
        return "revoked"
    if api_key.expires_at:
        expires_at = api_key.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            return "expired"
    return "active"


@router.post("", response_model=ApiKeyCreateResponse, summary="创建 API 密钥")
async def create_api_key(
    body: ApiKeyCreateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    创建 API 密钥（完整明文仅本次响应返回一次，请立即保存）
    单用户最多创建 settings.API_KEY_MAX_PER_USER 个（仅统计活跃密钥，吊销的不占名额）
    """
    result = await db.execute(
        select(func.count()).select_from(ApiKey).where(
            ApiKey.user_id == current_user.id,
            ApiKey.is_active.is_(True),
        )
    )
    count = result.scalar() or 0
    if count >= settings.API_KEY_MAX_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"最多创建 {settings.API_KEY_MAX_PER_USER} 个密钥，请先吊销不用的密钥",
        )

    if body.expires_at:
        expires_at = body.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="过期时间必须晚于当前时间")

    plaintext, key_prefix, key_suffix, key_hash = generate_api_key()
    name = (body.name or "").strip() or f"密钥-{datetime.now().strftime('%m%d')}-{key_suffix}"

    api_key = ApiKey(
        user_id=current_user.id,
        name=name,
        key_prefix=key_prefix,
        key_suffix=key_suffix,
        key_hash=key_hash,
        daily_quota=body.daily_quota,
        expires_at=body.expires_at,
        remark=body.remark,
    )
    db.add(api_key)
    await db.flush()

    record_audit_log(
        http_request, "apikey_create", user=current_user,
        extra_params={"key_id": api_key.id, "key_name": name, "key_prefix": key_prefix},
    )

    return ApiKeyCreateResponse(
        id=api_key.id,
        name=name,
        key=plaintext,
        key_display=f"{key_prefix}...{key_suffix}",
        daily_quota=api_key.daily_quota,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
    )


@router.get("", response_model=ApiKeyListResponse, summary="我的 API 密钥列表")
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户的所有密钥（脱敏展示，含今日用量）"""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.id.desc())
    )
    keys: List[ApiKey] = list(result.scalars().all())

    # 近 7 天（Asia/Shanghai 自然日）各密钥成功调用量，一次聚合查完
    tz = ZoneInfo("Asia/Shanghai")
    start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
    start_utc = start_local.astimezone(timezone.utc)
    usage_rows = (await db.execute(
        select(ProofreadRecord.api_key_id, func.count())
        .where(
            ProofreadRecord.api_key_id.in_([k.id for k in keys]),
            ProofreadRecord.created_at >= start_utc,
        )
        .group_by(ProofreadRecord.api_key_id)
    )).all()
    used_7d_map = {r[0]: r[1] for r in usage_rows}

    items = []
    for k in keys:
        used_today = await get_api_key_daily_usage(k)
        items.append(ApiKeyItem(
            id=k.id,
            name=k.name,
            key_display=_key_display(k),
            daily_quota=k.daily_quota,
            expires_at=k.expires_at,
            last_used_at=k.last_used_at,
            created_at=k.created_at,
            is_active=k.is_active,
            status=compute_key_status(k),
            used_today=used_today,
            used_7d=used_7d_map.get(k.id, 0),
            remark=k.remark,
            webhook_url=k.webhook_url,
        ))

    return ApiKeyListResponse(items=items, total=len(items))


@router.delete("/{key_id}", summary="吊销 API 密钥")
async def revoke_api_key(
    key_id: int,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """吊销密钥（立即失效，不可恢复）"""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=404, detail="密钥不存在")

    api_key.is_active = False
    await db.flush()

    record_audit_log(
        http_request, "apikey_revoke", user=current_user,
        extra_params={"key_id": api_key.id, "key_name": api_key.name},
    )

    return {"message": "密钥已吊销"}


@router.put("/{key_id}/webhook", response_model=ApiKeyWebhookSetResponse, summary="设置任务回调地址")
async def set_api_key_webhook(
    key_id: int,
    body: ApiKeyWebhookSetRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    设置回调地址：异步文档审校任务完成/失败时向该地址 POST 事件通知。
    每次设置都**轮换签名密钥**（明文仅本次响应返回一次，用后请妥善保存）。
    重新设置同一地址也会轮换密钥。
    """
    from secrets import token_hex

    from app.core.config import settings
    from app.core.secret_crypto import encrypt_secret
    from app.services.webhook import validate_webhook_url

    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=404, detail="密钥不存在")

    url = body.url.strip()
    try:
        validate_webhook_url(url, allow_private=settings.DEBUG)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    secret = f"whsec_{token_hex(24)}"
    api_key.webhook_url = url
    api_key.webhook_secret = encrypt_secret(secret)
    await db.flush()

    record_audit_log(
        http_request, "apikey_webhook_set", user=current_user,
        extra_params={"key_id": api_key.id, "webhook_url": url},
    )

    return ApiKeyWebhookSetResponse(url=url, secret=secret)


@router.delete("/{key_id}/webhook", summary="清除任务回调")
async def clear_api_key_webhook(
    key_id: int,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """清除回调地址与签名密钥（任务完成不再通知）"""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=404, detail="密钥不存在")

    api_key.webhook_url = None
    api_key.webhook_secret = None
    await db.flush()

    record_audit_log(
        http_request, "apikey_webhook_clear", user=current_user,
        extra_params={"key_id": api_key.id},
    )

    return {"message": "回调已清除"}


@router.post("/{key_id}/webhook/test", summary="发送测试回调")
async def test_api_key_webhook(
    key_id: int,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    立即向已配置的回调地址发送一条测试事件（同步执行，直接返回送达结果）。
    事件体结构与真实通知一致（event=webhook.test），可用于联调验签。
    """
    import json as _json

    import httpx

    from app.core.secret_crypto import decrypt_secret
    from app.services.webhook import build_event, sign_payload

    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=404, detail="密钥不存在")
    if not api_key.webhook_url:
        raise HTTPException(status_code=400, detail="该密钥未配置回调地址，请先设置")

    event = build_event("webhook.test", "test", {"message": "这是一条测试回调，收到即表示回调链路正常"})
    body = _json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TextMirror-Webhook/1.0",
        "X-TextMirror-Event": event["event"],
        "X-TextMirror-Delivery": f"test-{uuid4().hex}",
    }
    secret = decrypt_secret(api_key.webhook_secret) if api_key.webhook_secret else ""
    if secret:
        headers["X-TextMirror-Signature"] = sign_payload(secret, body)

    try:
        resp = httpx.post(api_key.webhook_url, content=body, headers=headers, timeout=10, follow_redirects=False)
        delivered = 200 <= resp.status_code < 300
        record_audit_log(
            http_request, "apikey_webhook_test", user=current_user,
            extra_params={"key_id": api_key.id, "status_code": resp.status_code, "delivered": delivered},
        )
        if delivered:
            return {"message": f"测试事件已送达（HTTP {resp.status_code}）", "status_code": resp.status_code, "event": event}
        return {"message": f"目标返回 HTTP {resp.status_code}（非 2xx 视为未送达，请检查接收端）", "status_code": resp.status_code, "event": event}
    except httpx.HTTPError as e:
        record_audit_log(
            http_request, "apikey_webhook_test", user=current_user,
            extra_params={"key_id": api_key.id, "delivered": False, "error": str(e)},
            status="failed", error_message=str(e),
        )
        raise HTTPException(
            status_code=502,
            detail=f"测试事件发送失败：{type(e).__name__}（请确认地址可达且不拦截外网请求）",
        )
