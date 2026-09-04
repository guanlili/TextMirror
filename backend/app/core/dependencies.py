"""
TextMirror 全局依赖注入
提供数据库Session、Redis客户端、当前用户等通用依赖
"""
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.redis import get_redis
from app.core.security import decode_token, hash_api_key
from app.core.config import settings

# HTTP Bearer Token 提取器
security_scheme = HTTPBearer(auto_error=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """兼容 naive datetime（如 SQLite/部分驱动）：视为 UTC"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    获取当前登录用户（必须登录）
    从 Authorization Header 提取 Bearer Token 并解析用户信息
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token类型无效",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token载荷无效",
        )

    # 延迟导入避免循环依赖
    from app.models.user import User

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    return user


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    获取当前用户（可选，游客也可访问）
    未登录时返回 None
    """
    if credentials is None:
        return None

    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


def require_permission(permission_code: str):
    """
    权限校验依赖注入工厂
    用法：Depends(require_permission("proofread:export"))

    :param permission_code: 权限编码，如 "proofread:export"
    """
    async def _check_permission(
        current_user=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        # 延迟导入避免循环依赖
        from app.models.role import Role, RolePermission, Permission

        # 超级管理员拥有所有权限
        result = await db.execute(
            select(Role).where(Role.id == current_user.role_id)
        )
        role = result.scalar_one_or_none()
        if role and role.code == "super_admin":
            return current_user

        # 查询用户角色关联的权限
        result = await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == current_user.role_id)
        )
        user_permissions = {row[0] for row in result.fetchall()}

        if permission_code not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"无权执行此操作，需要权限：{permission_code}",
            )

        return current_user

    return _check_permission


async def get_current_user_or_apikey(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> Tuple:
    """
    开放 API 认证依赖：Bearer tm_* 走 API Key，其余走 JWT
    :return: (user, api_key)，api_key 为 None 表示 JWT 登录态调用
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "未提供认证凭证，请携带 API 密钥或登录 Token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    if token.startswith(settings.API_KEY_PREFIX):
        # ---- API Key 认证 ----
        from app.models.api_key import ApiKey
        from app.models.user import User

        result = await db.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(token))
        )
        api_key = result.scalar_one_or_none()
        if api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_API_KEY", "message": "API 密钥无效"},
            )
        if not api_key.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "API_KEY_REVOKED", "message": "API 密钥已被吊销"},
            )
        if api_key.expires_at and _as_utc(api_key.expires_at) < _utcnow():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "API_KEY_EXPIRED", "message": "API 密钥已过期"},
            )

        result = await db.execute(select(User).where(User.id == api_key.user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "API_KEY_REVOKED", "message": "密钥归属账号已删除"},
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "ACCOUNT_DISABLED", "message": "密钥归属账号已被禁用"},
            )

        # last_used_at 节流更新（距上次记录超过 60s 才写库）
        now = _utcnow()
        if api_key.last_used_at is None or (now - _as_utc(api_key.last_used_at)).total_seconds() >= 60:
            api_key.last_used_at = now
            await db.flush()

        return user, api_key

    # ---- JWT 认证 ----
    user = await get_current_user(credentials, db)
    return user, None
