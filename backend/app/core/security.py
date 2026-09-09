"""
TextMirror 安全模块
JWT Token 签发与校验、密码加密、API Key 生成与哈希
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

import jwt
from passlib.context import CryptContext

from app.core.config import settings

# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def generate_api_key() -> Tuple[str, str, str, str]:
    """
    生成 API Key
    :return: (明文key, 前缀, 后4位, SHA-256哈希)
    """
    plaintext = f"{settings.API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, plaintext[:13], plaintext[-4:], hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    """计算 API Key 的 SHA-256 哈希（十六进制）"""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def hash_scoped_idempotency_key(scope: str, raw_key: str) -> str:
    """对调用方作用域与原始幂等键一起哈希，避免跨主体冲突和明文落库。"""
    return hashlib.sha256(f"{scope}\x00{raw_key}".encode()).hexdigest()


def derive_guest_task_access_token(task_id: str) -> str:
    """基于服务端密钥派生可重建的游客任务访问令牌。"""
    return hmac.new(
        settings.SECRET_KEY.encode(), f"proofread-task:{task_id}".encode(), hashlib.sha256
    ).hexdigest()


def hash_password(password: str) -> str:
    """对明文密码进行哈希"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码与哈希密码是否匹配"""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    subject: Any,
    extra_data: Optional[dict] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    创建 JWT Access Token
    :param subject: Token主体（通常是用户ID）
    :param extra_data: 额外载荷数据
    :param expires_delta: 过期时间差
    :return: JWT Token 字符串
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {"sub": str(subject), "exp": expire, "iat": now, "type": "access"}
    if extra_data:
        to_encode.update(extra_data)

    return jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def create_refresh_token(
    subject: Any,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    创建 JWT Refresh Token
    :param subject: Token主体（通常是用户ID）
    :param expires_delta: 过期时间差
    :return: JWT Token 字符串
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode = {"sub": str(subject), "exp": expire, "iat": now, "type": "refresh"}

    return jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def decode_token(token: str) -> Optional[dict]:
    """
    解码 JWT Token
    :param token: JWT Token 字符串
    :return: 解码后的载荷字典，失败返回 None
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except jwt.PyJWTError:
        return None
