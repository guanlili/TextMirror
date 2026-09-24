"""普通文档的用户预扣退款；API/worker 共用原日 key 和任务级幂等标记。"""
import re

from loguru import logger

from app.core.redis import get_redis

# 标记至少保留两天，且不早于被退计数过期；无 TTL 的计数使用持久标记。
# 计数已过期时只记退款，不重建计数，也绝不转退到今天。
REFUND_TASK_QUOTA_LUA = (
    "if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end "
    "local ttl = redis.call('PTTL', KEYS[1]) "
    "local c = tonumber(redis.call('GET', KEYS[1]) or '0') "
    "if c > 0 then redis.call('DECRBY', KEYS[1], 1) end "
    "if ttl == -1 then redis.call('SET', KEYS[2], '1') "
    "else redis.call('SET', KEYS[2], '1', 'PX', math.max(ttl, 172800000)) end "
    "return 1"
)


def document_quota_key(task) -> str | None:
    """只退有实际预扣凭据的普通文档，旧任务不能凭创建日或当前额度推断。"""
    params = task.params_json or {}
    if not task.document_id or params.get("kind") is not None:
        return None
    if task.owner_kind not in ("user", "api_key") or not task.owner_user_id:
        return None
    if "quota_key" not in params:
        logger.warning("文档任务缺少预扣凭据，跳过自动退款 task={}", task.task_id)
        return None
    key = params["quota_key"]
    if key is None:
        return None
    if not isinstance(key, str) or not re.fullmatch(
        rf"textmirror:user_daily:{task.owner_user_id}:[0-9]{{8}}", key,
    ):
        logger.warning("文档任务预扣凭据不匹配，跳过退款 task={}", task.task_id)
        return None
    return key


def document_api_key_quota_key(task) -> str | None:
    """密钥退款只接受与任务归属匹配的原日凭据，旧任务不猜测扣费日期。"""
    params = task.params_json or {}
    if task.owner_kind != "api_key" or not task.owner_api_key_id:
        return None
    if "api_key_quota_key" not in params:
        logger.warning("文档任务缺少密钥预扣凭据，跳过退款 task={}", task.task_id)
        return None
    key = params["api_key_quota_key"]
    if key is None:
        return None
    if not isinstance(key, str) or not re.fullmatch(
        rf"textmirror:apikey_daily:{task.owner_api_key_id}:[0-9]{{8}}", key,
    ):
        logger.warning("文档任务密钥预扣凭据不匹配，跳过退款 task={}", task.task_id)
        return None
    return key


async def refund_document_api_key_quota(task_id: str, quota_key: str | None) -> None:
    if not quota_key:
        return
    try:
        await get_redis().eval(
            REFUND_TASK_QUOTA_LUA, 2, quota_key, f"textmirror:document_key_refund:{task_id}",
        )
    except Exception as exc:
        logger.warning("文档密钥额度退还失败 task={} error={}", task_id, type(exc).__name__)


async def refund_document_quota(task_id: str, quota_key: str | None) -> None:
    if not quota_key:
        return
    try:
        await get_redis().eval(
            REFUND_TASK_QUOTA_LUA, 2, quota_key, f"textmirror:document_refund:{task_id}",
        )
    except Exception as exc:
        logger.warning("文档用户额度退还失败 task={} error={}", task_id, type(exc).__name__)


def refund_document_quota_sync(task_id: str, quota_key: str | None) -> None:
    if not quota_key:
        return
    from app.tasks.proofread_task import _get_sync_redis

    try:
        with _get_sync_redis() as redis:
            redis.eval(
                REFUND_TASK_QUOTA_LUA, 2, quota_key, f"textmirror:document_refund:{task_id}",
            )
    except Exception as exc:
        logger.warning("文档用户额度退还失败 task={} error={}", task_id, type(exc).__name__)
