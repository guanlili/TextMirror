"""
TextMirror 限流与配额
游客：基于 IP + Redis 的每日计数器
登录用户：基于 ProofreadRecord 当日记录数的每日配额
"""
import ipaddress
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Request, HTTPException, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis
from app.models.proofread import ProofreadRecord


def _is_trusted_proxy(host: str) -> bool:
    """直连客户端是否为本机/内网（即我们自己的 Nginx 反向代理）"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _get_client_ip(request: Request) -> str:
    """
    获取真实客户端 IP
    仅当直连方是可信代理（本机/内网 Nginx）时才信任 X-Forwarded-For，
    避免直连场景下伪造 XFF 头绕过限流
    """
    direct = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for and _is_trusted_proxy(direct):
        return forwarded_for.split(",")[0].strip()
    return direct


async def check_guest_rate_limit(request: Request):
    """
    游客限流检查
    对未携带 Token 的请求进行 IP 维度的每日限流
    每日限制次数由 settings.GUEST_DAILY_LIMIT 控制

    注意：本函数只在请求未通过认证（get_current_user_optional 返回 None）时调用，
    不再依据 Authorization 头是否存在放行——否则伪造任意 Bearer 头即可绕过限流。
    """
    client_ip = _get_client_ip(request)

    # Redis 计数器 key
    redis_key = f"textmirror:guest_limit:{client_ip}"

    try:
        redis = get_redis()
        current_count = await redis.get(redis_key)

        if current_count is not None and int(current_count) >= settings.GUEST_DAILY_LIMIT:
            logger.warning(f"游客限流触发: IP={client_ip}, count={current_count}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"游客每日最多使用 {settings.GUEST_DAILY_LIMIT} 次，请登录后继续使用",
            )

        # 计数器自增
        pipe = redis.pipeline()
        pipe.incr(redis_key)
        # 设置24小时过期（首次设置）
        if current_count is None:
            pipe.expire(redis_key, 86400)
        await pipe.execute()

    except HTTPException:
        raise
    except Exception as e:
        # Redis 异常不阻塞请求，仅记录日志
        logger.error(f"游客限流 Redis 异常: {e}")


async def check_user_quota(user, db: AsyncSession) -> None:
    """
    登录用户每日使用配额检查
    统计当日（业务时区 Asia/Shanghai）ProofreadRecord 记录数（text/document/polish）
    与 user.daily_quota 比较，daily_quota 为 None 表示不限。
    与仪表盘「今日校对次数」同一口径。
    """
    if user is None or user.daily_quota is None:
        return

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    local_date = func.to_char(ProofreadRecord.created_at.op("AT TIME ZONE")("Asia/Shanghai"), "YYYY-MM-DD")
    result = await db.execute(
        select(func.count()).select_from(ProofreadRecord).where(
            ProofreadRecord.user_id == user.id,
            local_date == today,
        )
    )
    used = result.scalar() or 0
    if used >= user.daily_quota:
        logger.warning(f"用户配额触发: user_id={user.id}, used={used}, quota={user.daily_quota}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"已达今日使用配额（{user.daily_quota} 次/天），请联系管理员调整",
        )


async def check_user_quota_n_times(user, db: AsyncSession, n: int) -> None:
    """
    多模型对比场景的配额预检：一次请求消耗 n 倍额度（n 个模型并发调用）。
    剩余额度不足以覆盖 n 次时拒绝，避免对比请求半途超额。
    """
    if user is None or user.daily_quota is None or n <= 1:
        return check_user_quota(user, db) if n > 0 else None

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    local_date = func.to_char(ProofreadRecord.created_at.op("AT TIME ZONE")("Asia/Shanghai"), "YYYY-MM-DD")
    result = await db.execute(
        select(func.count()).select_from(ProofreadRecord).where(
            ProofreadRecord.user_id == user.id,
            local_date == today,
        )
    )
    used = result.scalar() or 0
    remaining = user.daily_quota - used
    if remaining < n:
        logger.warning(f"用户配额不足（对比模式）: user_id={user.id}, used={used}, quota={user.daily_quota}, need={n}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"今日剩余额度 {max(remaining, 0)} 次，多模型对比需 {n} 次（每个模型计一次），请减少模型数量或明天再试",
        )
