import asyncio
import json
from typing import Dict, List, Optional

from loguru import logger
from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.global_word import GlobalWord

_GLOBAL_WORDS_CACHE_KEY = "textmirror:global_words_cache"
_GLOBAL_WORDS_CACHE_TTL = 120  # 秒


async def load_global_words() -> Dict[str, List[Dict]]:
    """
    从数据库加载全局词库, 按类型分组返回
    返回: {"sensitive": [...], "banned": [...], "correction": [...], "whitelist": [...]}

    优先读 Redis 缓存（TTL 120s），缓存未命中再查库并回填。
    """
    # 1. 尝试读缓存
    try:
        from app.core.redis import get_redis
        redis = get_redis()
        cached = await redis.get(_GLOBAL_WORDS_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass  # Redis 异常不阻塞，降级查库

    # 2. 查库
    result = {"sensitive": [], "banned": [], "correction": [], "whitelist": []}
    try:
        async with async_session_factory() as session:
            rows = await session.execute(
                select(GlobalWord).where(GlobalWord.is_active.is_(True))
            )
            for word in rows.scalars().all():
                item = {"word": word.word, "type": word.type}
                if word.replacement:
                    item["replacement"] = word.replacement
                if word.type in result:
                    result[word.type].append(item)
    except Exception as e:
        logger.warning(f"加载全局词库失败,跳过: {e}")
        return result

    # 3. 回填缓存（fire-and-forget，不阻塞主流程）
    try:
        from app.core.redis import get_redis
        redis = get_redis()
        await redis.set(_GLOBAL_WORDS_CACHE_KEY, json.dumps(result, ensure_ascii=False), ex=_GLOBAL_WORDS_CACHE_TTL)
    except Exception:
        pass

    return result


async def invalidate_global_words_cache():
    """全局词库变更时清除缓存，由管理端增删改接口调用"""
    try:
        from app.core.redis import get_redis
        await get_redis().delete(_GLOBAL_WORDS_CACHE_KEY)
    except Exception:
        pass


async def load_user_words(user_id: Optional[int]) -> Dict[str, List[Dict]]:
    """
    加载用户级词料：个性化词库词条（错→对）+ 有效放行词
    返回: {"correction": [...], "whitelist": [...]}
    user_id 为空（游客/无归属）返回空集
    """
    result = {"correction": [], "whitelist": []}
    if user_id is None:
        return result
    try:
        from app.models.dictionary import Dictionary, DictionaryEntry, WhitelistWord

        async with async_session_factory() as session:
            # 启用词库的词条
            rows = await session.execute(
                select(DictionaryEntry)
                .join(Dictionary, Dictionary.id == DictionaryEntry.dictionary_id)
                .where(Dictionary.user_id == user_id, Dictionary.is_active.is_(True))
            )
            for entry in rows.scalars().all():
                if entry.wrong_word and entry.correct_word:
                    result["correction"].append(
                        {"word": entry.wrong_word, "replacement": entry.correct_word}
                    )

            # 放行词：永久 + 未过期的临时
            # expire_at 是 naive 列（历史 schema），用 SQL 侧 now() 比较避免 aware/naive 传参冲突
            wl_rows = await session.execute(
                select(WhitelistWord.word).where(
                    WhitelistWord.user_id == user_id,
                    (WhitelistWord.type == "permanent")
                    | (WhitelistWord.expire_at > func.now()),
                )
            )
            for (word,) in wl_rows.all():
                if word:
                    result["whitelist"].append({"word": word})
    except Exception as e:
        logger.warning(f"加载用户词库失败,跳过: {e}")
    return result


async def _load_all_words(user_id: Optional[int]):
    """并发加载全局词库与用户词库（供 asyncio.gather 使用）"""
    return await asyncio.gather(load_global_words(), load_user_words(user_id))
