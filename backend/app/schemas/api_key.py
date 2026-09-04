"""
TextMirror API 密钥 Schema
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ApiKeyCreateRequest(BaseModel):
    """创建 API 密钥请求"""
    name: Optional[str] = Field(None, max_length=100, description="密钥名称（可空，自动生成）")
    daily_quota: Optional[int] = Field(None, ge=1, le=100000, description="密钥每日调用上限（空=跟随用户配额）")
    expires_at: Optional[datetime] = Field(None, description="过期时间（空=永不过期）")
    remark: Optional[str] = Field(None, max_length=500, description="备注")


class ApiKeyCreateResponse(BaseModel):
    """创建成功响应：完整密钥仅此一次返回"""
    id: int
    name: str
    key: str = Field(..., description="完整密钥明文（仅此一次展示，请立即保存）")
    key_display: str = Field(..., description="脱敏展示形式")
    daily_quota: Optional[int] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class ApiKeyItem(BaseModel):
    """密钥列表项（不含任何可还原密钥的信息）"""
    id: int
    name: str
    key_display: str = Field(..., description="脱钥展示：前缀...后4位")
    daily_quota: Optional[int] = None
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    is_active: bool = True
    status: str = Field(..., description="active/revoked/expired")
    used_today: Optional[int] = Field(None, description="今日调用次数（Redis 不可用时为 null）")
    remark: Optional[str] = Field(None, description="备注")


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyItem] = []
    total: int = 0
