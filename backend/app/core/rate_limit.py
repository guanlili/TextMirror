"""
TextMirror 限流与配额
游客：基于 IP + Redis 的每日计数器
登录用户：基于 ProofreadRecord 当日记录数的每日配额
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.models.proofread import ProofreadRecord
from app.utils.ip import get_client_ip


async def check_guest_rate_limit(request: Request, daily_limit: int | None = None):
    """
    游客限流检查（IP 维度，Asia/Shanghai 自然日窗口，与登录用户/密钥配额同口径）
    每日限制次数取后台「策略管理」配置，未配置时回落到 settings.GUEST_DAILY_LIMIT

    计数器 key 带当日日期后缀，先 INCR 后判断（并发请求不会同时越过上限）；
    被拒请求随即 DECR 抵消，计数器始终只记实际放行的次数。
    Redis 异常不阻塞请求（与用户配额、密钥限流一致的降级策略）。

    注意：本函数只在请求未通过认证（get_current_user_optional 返回 None）时调用，
    不再依据 Authorization 头是否存在放行——否则伪造任意 Bearer 头即可绕过限流。
    """
    from app.services.guest_policy import get_guest_policy

    client_ip = get_client_ip(request)
    if daily_limit is None:
        daily_limit = (await get_guest_policy())["daily_limit"]

    redis_key = _daily_key("guest", f"ip:{client_ip}")

    try:
        redis = get_redis()
        count = await redis.incr(redis_key)
        if count == 1:
            await redis.expire(redis_key, 172800)

        if count > daily_limit:
            # 抵消被拒请求的自增：计数器只反映实际放行次数，
            # 管理员当日上调限额后可立即生效
            await redis.decr(redis_key)
            logger.warning(f"游客限流触发: IP={client_ip}, count={count}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"游客每日最多使用 {daily_limit} 次，请登录后继续使用",
            )

    except HTTPException:
        raise
    except Exception as e:
        # Redis 异常不阻塞请求，仅记录日志
        logger.error(f"游客限流 Redis 异常: {e}")


def _daily_key(prefix: str, subject: str) -> str:
    """日计数 Redis key（业务时区 Asia/Shanghai 自然日，次日自动换 key）"""
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    return f"textmirror:{prefix}:{subject}:{today}"


def _shanghai_today_range() -> tuple:
    """当日（Asia/Shanghai）对应的 UTC 时间范围，用于与 timestamptz 列做范围比较（可走索引）"""
    tz = ZoneInfo("Asia/Shanghai")
    now_local = datetime.now(tz)
    local_midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc), (local_midnight + timedelta(days=1)).astimezone(timezone.utc)


async def _count_today_records(user_id: int, db: AsyncSession) -> int:
    """统计用户当日（Asia/Shanghai）配额消耗：SUM(quota_weight)——多模型对比一条记录按成功模型数计"""
    day_start, day_end = _shanghai_today_range()
    result = await db.execute(
        select(func.coalesce(func.sum(ProofreadRecord.quota_weight), 0)).select_from(ProofreadRecord).where(
            ProofreadRecord.user_id == user_id,
            ProofreadRecord.created_at >= day_start,
            ProofreadRecord.created_at < day_end,
        )
    )
    return int(result.scalar() or 0)


async def charge_user_daily_quota(user, weight: int = 1) -> str | None:
    """
    登录用户每日使用配额：原子预扣（先 INCRBY 后判断，Asia/Shanghai 自然日）。

    旧实现是 check-then-write——检查读 DB 当日记录数，而 ProofreadRecord 要等
    校对完成才落库，竞态窗口=整个 LLM 调用（秒级），并发请求可全部通过预检。
    现改为 Redis 预扣计数（与密钥日配额同一模式），DB 记录仍是展示口径
    （仪表盘/历史），两侧一致的前提是失败路径调用 refund_user_daily_quota 退还
    （用户没拿到结果不消耗额度，与「失败不落库=不计消耗」的旧口径等价）。

    daily_quota 为 None 表示不限额（也不计数）。Redis 异常时放行本次
    （与密钥限流一致的降级策略，弱保证但不阻断服务）。
    """
    if user is None or user.daily_quota is None or weight <= 0:
        return
    try:
        redis = get_redis()
        key = _daily_key("user_daily", str(user.id))
        count = await redis.eval(_CHARGE_DAILY_LUA, 1, key, weight, user.daily_quota)
        if count == -1:
            logger.warning(
                f"用户配额触发: user_id={user.id}, quota={user.daily_quota}, need={weight}"
            )
            detail = (
                f"已达今日使用配额（{user.daily_quota} 次/天），请联系管理员调整"
                if weight == 1
                else f"今日配额 {user.daily_quota} 次已用完，本次需 {weight} 次（每个模型计一次），请减少模型数量或明天再试"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=detail,
            )
        return key
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"用户配额 Redis 异常（本次放行，配额暂不生效）: {e}")


async def refund_user_daily_quota(user, weight: int = 1) -> None:
    """
    退还用户日配额预扣（服务端失败 / 对比场景部分模型失败）：
    用户没拿到结果的部分不消耗当日额度。
    未配置配额（daily_quota=None）的用户从未预扣，键不存在时 Lua 直接返回 0。
    """
    if user is None or weight <= 0:
        return
    try:
        redis = get_redis()
        await redis.eval(_REFUND_DAILY_LUA, 1, _daily_key("user_daily", str(user.id)), weight)
    except Exception as e:
        logger.error(f"退还用户日配额 Redis 异常: {e}")


def _api_key_daily_redis_key(api_key) -> str:
    """密钥日计数 Redis key（业务时区 Asia/Shanghai 自然日，与用户配额口径一致）"""
    return _daily_key("apikey_daily", str(api_key.id))


async def check_upload_rate_limit(request: Request, user=None) -> None:
    """
    上传频率限制（固定分钟窗口）。
    上传本身此前不受任何限制，可反复上传只触发解析与落盘，从而绕开按校对次数
    计费的配额，并放大磁盘与 CPU 消耗。登录用户按 id 计，游客按 IP 计。
    Redis 异常不阻塞请求（与游客限流一致的降级策略）。
    """
    limit = settings.UPLOAD_MAX_PER_MINUTE
    if limit <= 0:
        return

    subject = f"user:{user.id}" if user is not None else f"ip:{get_client_ip(request)}"
    try:
        redis = get_redis()
        minute = datetime.now().strftime("%Y%m%d%H%M")
        key = f"textmirror:upload_rpm:{subject}:{minute}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 120)
        if count > limit:
            logger.warning(f"上传频率超限: {subject}, count={count}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"上传过于频繁：每分钟最多 {limit} 次，请稍后重试",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传限流 Redis 异常: {e}")


async def check_api_key_rpm(api_key) -> None:
    """
    API Key 维度 RPM 限流（固定分钟窗口，settings.API_KEY_RPM_LIMIT）
    Redis 异常不阻塞请求（与游客限流降级策略一致）
    """
    try:
        redis = get_redis()
        minute = datetime.now().strftime("%Y%m%d%H%M")
        rpm_key = f"textmirror:apikey_rpm:{api_key.id}:{minute}"
        rpm_count = await redis.incr(rpm_key)
        if rpm_count == 1:
            await redis.expire(rpm_key, 120)
        if rpm_count > settings.API_KEY_RPM_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "RATE_LIMITED",
                    "message": f"请求频率超限：每分钟最多 {settings.API_KEY_RPM_LIMIT} 次，请稍后重试",
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API Key RPM 限流 Redis 异常: {e}")


async def charge_api_key_daily(api_key, weight: int = 1) -> None:
    """
    密钥日配额计数与检查（自然日，无论是否配置配额都计数以供展示）。
    weight：本次请求消耗的额度数（多模型对比 = 模型数）。
    """
    try:
        redis = get_redis()
        daily_key = _api_key_daily_redis_key(api_key)
        quota = api_key.daily_quota if api_key.daily_quota is not None else 0
        count = await redis.eval(_CHARGE_DAILY_LUA, 1, daily_key, weight, quota)
        if count == -1:
            logger.warning(
                f"密钥日配额触发: key_id={api_key.id}, quota={api_key.daily_quota}, need={weight}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "KEY_QUOTA_EXCEEDED",
                    "message": f"该密钥已达每日调用上限（{api_key.daily_quota} 次/天），明天恢复或联系管理员调整",
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API Key 限流 Redis 异常: {e}")


async def check_api_key_rate_limit(api_key) -> None:
    """文本单模型场景：RPM + 日配额（1 请求 = 1 次额度）"""
    await check_api_key_rpm(api_key)
    await charge_api_key_daily(api_key)


async def get_api_key_daily_usage(api_key) -> int | None:
    """读取密钥今日调用次数（Redis 不可用时返回 None）"""
    try:
        redis = get_redis()
        count = await redis.get(_api_key_daily_redis_key(api_key))
        return int(count) if count is not None else 0
    except Exception as e:
        logger.error(f"读取密钥日用量 Redis 异常: {e}")
        return None


# 原子预扣 Lua：INCRBY → 检查限额 → 超额则 DECRBY 回滚并返回 -1，未超额则设 TTL 并返回当前计数
# 消除 INCRBY/check/DECRBY 三步非原子操作的竞态窗口
_CHARGE_DAILY_LUA = (
    "local cur = redis.call('INCRBY', KEYS[1], ARGV[1]) "
    "local quota = tonumber(ARGV[2]) "
    "if quota > 0 and cur > quota then "
    "  redis.call('DECRBY', KEYS[1], ARGV[1]) "
    "  return -1 "
    "end "
    "if redis.call('TTL', KEYS[1]) < 0 then "
    "  redis.call('EXPIRE', KEYS[1], 172800) "
    "end "
    "return cur "
)

# 仅当计数 >0 时 DECR（下限为 0），避免键过期/多次退还制造负值
_REFUND_DAILY_LUA = (
    "local c = redis.call('GET', KEYS[1]) "
    "if c and tonumber(c) > 0 then "
    "  local refund = tonumber(ARGV[1]) "
    "  if tonumber(c) < refund then refund = tonumber(c) end "
    "  return redis.call('DECRBY', KEYS[1], refund) "
    "end "
    "return 0"
)


async def refund_api_key_daily_usage(api_key, weight: int = 1) -> None:
    """
    退还密钥日配额计数（服务端失败 / 对比场景部分模型失败）：
    用户没拿到结果的部分不消耗当日额度（用户配额预扣的退还走 refund_user_daily_quota）。
    """
    if weight <= 0:
        return
    try:
        redis = get_redis()
        await redis.eval(_REFUND_DAILY_LUA, 1, _api_key_daily_redis_key(api_key), weight)
    except Exception as e:
        logger.error(f"退还密钥日配额 Redis 异常: {e}")


async def reject_guest_if_disabled(http_request: Request) -> None:
    """
    游客模式关闭时拒绝游客请求（站点配置 guest_mode_enabled=off）。
    在各游客可用端点的游客分支开头调用。
    """
    from app.services.site_config import is_guest_mode_enabled
    if not await is_guest_mode_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="游客模式已关闭，请登录后使用",
        )
