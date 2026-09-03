"""
TextMirror 站点信息公开接口
无需登录即可获取平台名称、图标等配置
"""
import glob
import mimetypes
import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.site_config import get_site_config, ICON_UPLOAD_DIR, ALLOWED_ICON_EXTENSIONS

router = APIRouter(prefix="/site", tags=["站点信息"])

# 图标 URL 标识（favicon_<stem> 的 stem 部分），不含扩展名：
# 带图片后缀的 URL 会被前端 Nginx 的静态资源缓存规则劫持，导致 404
_ICON_ID_RE = re.compile(r"^[a-f0-9]{8}$")


@router.get("/info", summary='获取站点公开配置（平台名称、副标题、图标）')
async def get_site_info():
    """获取站点公开配置（平台名称、副标题、图标）"""
    config = await get_site_config()
    return config


@router.get("/icon/{icon_id}", summary='获取平台图标（公开，favicon/img 标签无法携带鉴权头）')
async def get_site_icon(icon_id: str):
    """
    获取平台图标（公开，favicon/img 标签无法携带鉴权头）
    按 favicon_<id>.<ext> 规则在上传目录中查找
    """
    if not _ICON_ID_RE.match(icon_id):
        raise HTTPException(status_code=404, detail="图标不存在")

    icon_dir = os.path.realpath(ICON_UPLOAD_DIR)
    matches = [
        p for p in glob.glob(os.path.join(ICON_UPLOAD_DIR, f"favicon_{icon_id}.*"))
        if os.path.splitext(p)[1].lower() in ALLOWED_ICON_EXTENSIONS
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="图标不存在")

    filepath = os.path.realpath(matches[0])
    if not filepath.startswith(icon_dir + os.sep) or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="图标不存在")

    media_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    return FileResponse(
        filepath,
        media_type=media_type,
        # SVG 可内嵌脚本，禁止其执行（图片场景无影响，防直接导航）
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"},
    )
