"""游客策略测试：Redis 配置优先、未配置回落 .env、更新生效。"""
from app.core import redis as redis_module
from app.services.guest_policy import get_guest_policy, update_guest_policy


async def test_defaults_fall_back_to_env(client):
    policy = await get_guest_policy()
    from app.core.config import settings
    assert policy["daily_limit"] == settings.GUEST_DAILY_LIMIT
    assert policy["max_text_length"] == settings.GUEST_TEXT_MAX_LENGTH
    assert policy["allow_upload"] is True


async def test_redis_overrides_env(client):
    await redis_module.redis_client.hset(
        "system:policy:guest",
        mapping={"daily_limit": "5", "max_text_length": "888", "allow_upload": "0"},
    )
    policy = await get_guest_policy()
    assert policy["daily_limit"] == 5
    assert policy["max_text_length"] == 888
    assert policy["allow_upload"] is False


async def test_partial_override_keeps_other_defaults(client):
    # 只配 daily_limit：其余项保持默认（避免部分配置导致整页策略回落）
    await redis_module.redis_client.hset("system:policy:guest", mapping={"daily_limit": "3"})
    policy = await get_guest_policy()
    assert policy["daily_limit"] == 3
    assert policy["allow_upload"] is True


async def test_update_then_read_roundtrip(client):
    updated = await update_guest_policy(daily_limit=7, max_text_length=1200, allow_upload=False)
    assert updated == {"daily_limit": 7, "max_text_length": 1200, "allow_upload": False}
    # 再次读取仍是新值（策略持久化在 Redis）
    assert await get_guest_policy() == updated
