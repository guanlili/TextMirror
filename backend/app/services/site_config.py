"""
TextMirror 站点配置服务
使用 Redis 存储站点级配置（平台名称、副标题、图标等）
"""
import os
from typing import Dict, Any
from loguru import logger

from app.core.config import settings
from app.core.redis import get_redis

# Redis key 前缀
SITE_CONFIG_PREFIX = "site:config:"

# 平台图标存储（上传目录的 icons 子目录）
ICON_UPLOAD_DIR = os.path.join(settings.UPLOAD_DIR, "icons")
ALLOWED_ICON_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp", ".gif"}

# 默认配置
DEFAULT_SITE_CONFIG: Dict[str, str] = {
    "platform_name": "TextMirror",
    "platform_subtitle": "智能文档审校平台",
    "favicon_url": "",
    # 登录页主标语（空=显示平台副标题）
    "login_slogan": "",
    # 页脚文案（公司名/备案号等，空=不显示页脚）
    "footer_text": "",
    # 游客模式总开关：off 时所有游客入口关闭，需登录使用
    "guest_mode_enabled": "on",
}


async def is_guest_mode_enabled() -> bool:
    """游客模式是否开启（默认开，配置异常时保持开）"""
    config = await get_site_config()
    return config.get("guest_mode_enabled", "on") == "on"


async def get_site_config() -> Dict[str, str]:
    """
    获取全部站点配置
    优先从 Redis 读取，未设置的使用默认值
    """
    redis = get_redis()
    config = dict(DEFAULT_SITE_CONFIG)

    try:
        for key in DEFAULT_SITE_CONFIG:
            val = await redis.get(f"{SITE_CONFIG_PREFIX}{key}")
            if val is not None:
                config[key] = val
    except Exception as e:
        logger.warning(f"[站点配置] Redis 读取失败，使用默认值: {e}")

    return config


async def update_site_config(updates: Dict[str, str]) -> Dict[str, str]:
    """
    批量更新站点配置
    :param updates: 要更新的配置键值对
    :return: 更新后的完整配置
    """
    redis = get_redis()
    allowed_keys = set(DEFAULT_SITE_CONFIG.keys())

    try:
        for key, value in updates.items():
            if key in allowed_keys:
                await redis.set(f"{SITE_CONFIG_PREFIX}{key}", value)
                logger.info(f"[站点配置] 已更新 {key} = {value}")
    except Exception as e:
        logger.error(f"[站点配置] Redis 写入失败: {e}")
        raise

    return await get_site_config()
