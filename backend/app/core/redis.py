"""
TextMirror Redis 连接管理
用于缓存、限流计数、会话管理等
"""
import redis.asyncio as aioredis
from loguru import logger
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

from app.core.config import settings

# Redis 连接池（全局单例）
redis_client: aioredis.Redis = None


async def init_redis() -> aioredis.Redis:
    """初始化 Redis 连接"""
    global redis_client
    try:
        redis_client = aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            username=settings.REDIS_USERNAME or None,
            password=settings.REDIS_PASSWORD or None,
            db=settings.REDIS_DB,
            decode_responses=True,
            max_connections=50,
            socket_timeout=5,
            socket_connect_timeout=5,
            # 瞬断自动重试：指数退避，最多重试 2 次（共 3 次尝试）
            retry=Retry(backoff=ExponentialBackoff(base=0.3, cap=2), retries=2),
            retry_on_error=[ConnectionError, TimeoutError, aioredis.ConnectionError],
            # 连接池定期探活，剔除已断开的连接
            health_check_interval=30,
        )
        # 测试连接
        await redis_client.ping()
        logger.info(f"Redis 连接成功: {settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}")
        return redis_client
    except Exception as e:
        logger.error(f"Redis 连接失败: {e}")
        raise


async def close_redis():
    """关闭 Redis 连接"""
    global redis_client
    if redis_client:
        await redis_client.close()
        logger.info("Redis 连接已关闭")


def get_redis() -> aioredis.Redis:
    """获取 Redis 客户端的依赖注入函数"""
    if redis_client is None:
        raise RuntimeError("Redis 尚未初始化，请先调用 init_redis()")
    return redis_client
