"""
TextMirror 游客策略服务
后台「策略管理」可编辑的游客限制：Redis 配置优先，未配置项回落到 .env
"""
from typing import Dict

from loguru import logger

from app.core.config import settings
from app.core.redis import get_redis

POLICY_KEY = "system:policy:guest"


async def get_guest_policy() -> Dict:
    """获取生效的游客策略（后台未配置时以 .env 为准，避免页面展示值与实际拦截值不一致）"""
    policy = {
        "daily_limit": settings.GUEST_DAILY_LIMIT,
        "max_text_length": settings.GUEST_TEXT_MAX_LENGTH,
        "allow_upload": True,
    }

    try:
        data = await get_redis().hgetall(POLICY_KEY)
        if data.get("daily_limit"):
            policy["daily_limit"] = int(data["daily_limit"])
        if data.get("max_text_length"):
            policy["max_text_length"] = int(data["max_text_length"])
        if data.get("allow_upload") is not None:
            policy["allow_upload"] = data["allow_upload"] == "1"
    except Exception as e:
        logger.warning(f"[游客策略] 读取失败，回落到 .env 配置: {e}")

    return policy


async def update_guest_policy(daily_limit: int, max_text_length: int, allow_upload: bool) -> Dict:
    """更新游客策略并返回更新后的生效值"""
    await get_redis().hset(
        POLICY_KEY,
        mapping={
            "daily_limit": daily_limit,
            "max_text_length": max_text_length,
            "allow_upload": "1" if allow_upload else "0",
        },
    )
    logger.info(
        f"[游客策略] 已更新: daily_limit={daily_limit}, "
        f"max_text_length={max_text_length}, allow_upload={allow_upload}"
    )
    return await get_guest_policy()
