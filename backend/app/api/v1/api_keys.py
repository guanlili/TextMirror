"""
TextMirror API 密钥自助管理
创建/列表/吊销，仅支持 JWT 登录态（API 密钥本身不能管理密钥，防止泄漏后自我复制）
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.rate_limit import get_api_key_daily_usage
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.user import User
from app.schemas.api_key import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyItem,
    ApiKeyListResponse,
)
from app.services.audit_log import record_audit_log

router = APIRouter(prefix="/api-keys", tags=["API密钥"])


def _key_display(api_key: ApiKey) -> str:
    return f"{api_key.key_prefix}...{api_key.key_suffix}"


def _key_status(api_key: ApiKey) -> str:
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
            ApiKey.is_active == True,
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
            status=_key_status(k),
            used_today=used_today,
            remark=k.remark,
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
