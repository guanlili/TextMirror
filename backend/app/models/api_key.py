"""
TextMirror 开放平台 API 密钥模型
对外 API 认证凭证：仅存哈希（SHA-256），明文只在创建时返回一次
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class ApiKey(BaseModel):
    """API 密钥表"""
    __tablename__ = "api_keys"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True, comment="归属用户ID"
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="密钥名称（用途备注）"
    )
    key_prefix: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="密钥前缀（明文展示用）"
    )
    key_suffix: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="密钥后4位（明文展示用）"
    )
    key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True, comment="密钥SHA-256哈希"
    )
    daily_quota: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, default=None, comment="密钥每日调用上限(null=跟随用户配额)"
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="过期时间(null=永不过期)"
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近使用时间"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="是否有效（吊销后为False）"
    )
    remark: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="备注"
    )

    def __repr__(self):
        return f"<ApiKey(id={self.id}, name={self.name}, prefix={self.key_prefix}..., active={self.is_active})>"
