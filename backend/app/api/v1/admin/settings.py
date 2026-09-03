"""
TextMirror 管理后台 - 系统设置接口
管理员可配置平台名称、图标等
"""
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.dependencies import require_permission
from app.services.site_config import (
    get_site_config, update_site_config,
    ICON_UPLOAD_DIR, ALLOWED_ICON_EXTENSIONS,
)

router = APIRouter(prefix="/settings", tags=["管理后台-系统设置"])


class SiteConfigUpdate(BaseModel):
    """站点配置更新请求体"""
    platform_name: Optional[str] = None
    platform_subtitle: Optional[str] = None
    favicon_url: Optional[str] = None


@router.get("/site", summary='获取站点配置（管理员）')
async def admin_get_site_config(_user=Depends(require_permission("admin:settings:edit"))):
    """获取站点配置（管理员）"""
    return await get_site_config()


@router.put("/site", summary='更新站点配置（管理员）')
async def admin_update_site_config(
    body: SiteConfigUpdate,
    _user=Depends(require_permission("admin:settings:edit")),
):
    """更新站点配置（管理员）"""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return await get_site_config()
    return await update_site_config(updates)


@router.post("/upload-icon", summary='上传平台图标文件')
async def admin_upload_icon(
    file: UploadFile = File(...),
    _user=Depends(require_permission("admin:settings:edit")),
):
    """
    上传平台图标文件
    返回图标的访问 URL，管理员可在保存品牌设置时引用该 URL
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择文件")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_ICON_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式，允许：{', '.join(ALLOWED_ICON_EXTENSIONS)}"
        )

    # 限制文件大小（500KB）
    content = await file.read()
    if len(content) > 512 * 1024:
        raise HTTPException(status_code=400, detail="图标文件不能超过 500KB")

    # 保存文件
    os.makedirs(ICON_UPLOAD_DIR, exist_ok=True)
    filename = f"favicon_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(ICON_UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    # 返回可访问的 URL（公开图标端点；不带扩展名，避免被前端 Nginx 静态资源规则劫持）
    icon_id = filename[len("favicon_"):-len(ext)]
    icon_url = f"{settings.API_PREFIX}/site/icon/{icon_id}"
    return {"url": icon_url, "filename": filename}
