"""全局词库缓存：命中、TTL 与失效行为。"""
import json

from app.core.redis import get_redis
from app.services.proofread.words import (
    _GLOBAL_WORDS_CACHE_KEY,
    _GLOBAL_WORDS_CACHE_TTL,
    invalidate_global_words_cache,
    load_global_words,
)

SENTINEL = {
    "sensitive": [{"word": "缓存哨兵", "type": "sensitive"}],
    "banned": [],
    "correction": [],
    "whitelist": [],
}


async def test_global_words_cache_hit_ttl_and_invalidate(client):
    redis = get_redis()
    await redis.delete(_GLOBAL_WORDS_CACHE_KEY)

    first = await load_global_words()
    assert first == {"sensitive": [], "banned": [], "correction": [], "whitelist": []}
    assert await redis.exists(_GLOBAL_WORDS_CACHE_KEY) == 1
    ttl = await redis.ttl(_GLOBAL_WORDS_CACHE_KEY)
    assert 0 < ttl <= _GLOBAL_WORDS_CACHE_TTL

    # 直接改写缓存，若仍命中缓存则不再查库
    await redis.set(_GLOBAL_WORDS_CACHE_KEY, json.dumps(SENTINEL, ensure_ascii=False), ex=60)
    assert await load_global_words() == SENTINEL

    await invalidate_global_words_cache()
    assert await redis.exists(_GLOBAL_WORDS_CACHE_KEY) == 0
