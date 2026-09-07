"""
TextMirror 策略管理 API（管理后台）
配置游客使用策略；登录用户的额度走「用户管理」逐用户 daily_quota，功能开关走 RBAC 权限
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.dependencies import require_permission
from app.services.guest_policy import get_guest_policy, update_guest_policy

router = APIRouter(prefix="/policy", tags=["策略管理"])


class GuestPolicyConfig(BaseModel):
    """游客策略配置"""
    daily_limit: int = Field(..., ge=0, le=100000, description="每日校对次数上限")
    max_text_length: int = Field(..., ge=100, le=500000, description="单次最大字数")
    allow_upload: bool = Field(..., description="是否允许上传文档")


@router.get("/guest", response_model=GuestPolicyConfig, summary='获取游客策略配置（未配置时返回 .env 生效值）')
async def get_guest_policy_config(
    _user=Depends(require_permission("admin:policy:edit")),
):
    """获取游客策略配置"""
    return GuestPolicyConfig(**await get_guest_policy())


@router.put("/guest", response_model=GuestPolicyConfig, summary='更新游客策略配置（即时生效）')
async def update_guest_policy_config(
    config: GuestPolicyConfig,
    _user=Depends(require_permission("admin:policy:edit")),
):
    """更新游客策略配置"""
    updated = await update_guest_policy(
        daily_limit=config.daily_limit,
        max_text_length=config.max_text_length,
        allow_upload=config.allow_upload,
    )
    return GuestPolicyConfig(**updated)
