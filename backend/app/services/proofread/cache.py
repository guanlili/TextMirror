import json
from typing import Any, Dict, List, Optional

from loguru import logger

from .constants import _CHUNK_CACHE_TTL


class ChunkCache:
    """Redis -backed 分片结果缓存，支持断点续传。

    以 text_hash + config_id + depth 为维度，每个分片按 chunk_index 存储。
    读取时校验 chunk_text_hash 防止文本变更导致命中脏数据。
    """

    def __init__(self, redis_client, text_hash: str, config_id: int, depth: str):
        self._redis = redis_client
        self._key = f"textmirror:chunk_cache:{text_hash}:{config_id}:{depth}"

    def get_chunk(self, chunk_index: int, chunk_text_hash: str) -> Optional[Dict[str, Any]]:
        try:
            data = self._redis.hget(self._key, str(chunk_index))
            if not data:
                return None
            cached = json.loads(data)
            if cached.get("text_hash") != chunk_text_hash:
                return None
            return cached
        except Exception:
            return None

    def set_chunk(self, chunk_index: int, chunk_text_hash: str,
                  issues: List[Dict[str, Any]], usage: Dict[str, int]):
        try:
            payload = json.dumps({
                "text_hash": chunk_text_hash,
                "issues": issues,
                "usage": usage,
            }, ensure_ascii=False)
            self._redis.hset(self._key, str(chunk_index), payload)
            self._redis.expire(self._key, _CHUNK_CACHE_TTL)
        except Exception as e:
            logger.warning(f"[分片缓存] 写入失败 chunk={chunk_index}: {e}")


def _make_chunk_cache(
    redis_client, text: str, config_id: int, depth: str,
) -> Optional[ChunkCache]:
    """构建分片缓存（Redis 不可用时返回 None，降级为无缓存）"""
    import hashlib
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    return ChunkCache(redis_client, text_hash, config_id or 0, depth)
