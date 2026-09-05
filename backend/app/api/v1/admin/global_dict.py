"""
TextMirror 全局词库管理 API（管理后台）
仅管理员可操作
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from loguru import logger

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.models.global_word import GlobalWord
from app.schemas.global_word import (
    GlobalWordCreate, GlobalWordUpdate, GlobalWordResponse,
    GlobalWordBatchCreate, GlobalWordStats,
)

router = APIRouter(prefix="/global-dict", tags=["全局词库管理"])


@router.get("/stats", response_model=GlobalWordStats, summary='获取全局词库统计')
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:global_dict:edit")),
):
    """获取全局词库统计"""
    total = (await db.execute(select(func.count(GlobalWord.id)))).scalar() or 0
    sensitive = (await db.execute(
        select(func.count(GlobalWord.id)).where(GlobalWord.type == "sensitive")
    )).scalar() or 0
    banned = (await db.execute(
        select(func.count(GlobalWord.id)).where(GlobalWord.type == "banned")
    )).scalar() or 0
    whitelist = (await db.execute(
        select(func.count(GlobalWord.id)).where(GlobalWord.type == "whitelist")
    )).scalar() or 0
    correction = (await db.execute(
        select(func.count(GlobalWord.id)).where(GlobalWord.type == "correction")
    )).scalar() or 0

    return GlobalWordStats(
        total=total,
        sensitive_count=sensitive,
        banned_count=banned,
        whitelist_count=whitelist,
        correction_count=correction,
    )


@router.get("", response_model=list[GlobalWordResponse], summary='获取全局词库列表')
async def list_global_words(
    type: Optional[str] = Query(None, description="类型过滤: sensitive/banned/whitelist/correction"),
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:global_dict:edit")),
):
    """获取全局词库列表"""
    query = select(GlobalWord)
    if type:
        query = query.where(GlobalWord.type == type)
    if keyword:
        query = query.where(GlobalWord.word.contains(keyword))
    query = query.order_by(GlobalWord.type, GlobalWord.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=GlobalWordResponse, status_code=201, summary='添加全局词条')
async def create_global_word(
    data: GlobalWordCreate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:global_dict:edit")),
):
    """添加全局词条"""
    # 检查重复
    exists = await db.execute(
        select(GlobalWord).where(
            GlobalWord.word == data.word,
            GlobalWord.type == data.type,
        )
    )
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该词条已存在")

    word = GlobalWord(
        word=data.word,
        type=data.type,
        replacement=data.replacement,
        category=data.category,
        severity=data.severity,
        remark=data.remark,
    )
    db.add(word)
    await db.flush()
    await db.refresh(word)
    logger.info(f"全局词条已添加: {data.word} ({data.type})")
    return word


@router.post("/batch", status_code=201, summary='批量添加全局词条')
async def batch_create_global_words(
    data: GlobalWordBatchCreate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:global_dict:edit")),
):
    """批量添加全局词条"""
    added = 0
    skipped = 0
    for entry in data.entries:
        exists = await db.execute(
            select(GlobalWord).where(
                GlobalWord.word == entry.word,
                GlobalWord.type == entry.type,
            )
        )
        if exists.scalar_one_or_none():
            skipped += 1
            continue
        db.add(GlobalWord(
            word=entry.word,
            type=entry.type,
            replacement=entry.replacement,
            category=entry.category,
            severity=entry.severity,
            remark=entry.remark,
        ))
        added += 1

    await db.flush()
    logger.info(f"批量添加全局词条: 成功={added}, 跳过={skipped}")
    return {"message": f"成功添加 {added} 条，跳过 {skipped} 条重复", "added": added, "skipped": skipped}


@router.put("/{word_id}", response_model=GlobalWordResponse, summary='更新全局词条')
async def update_global_word(
    word_id: int,
    data: GlobalWordUpdate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:global_dict:edit")),
):
    """更新全局词条"""
    result = await db.execute(select(GlobalWord).where(GlobalWord.id == word_id))
    word = result.scalar_one_or_none()
    if not word:
        raise HTTPException(status_code=404, detail="词条不存在")

    for field in ["word", "type", "replacement", "category", "severity", "remark", "is_active"]:
        val = getattr(data, field, None)
        if val is not None:
            setattr(word, field, val)

    await db.flush()
    await db.refresh(word)
    return word


@router.delete("/{word_id}", status_code=204, summary='删除全局词条')
async def delete_global_word(
    word_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:global_dict:edit")),
):
    """删除全局词条"""
    result = await db.execute(select(GlobalWord).where(GlobalWord.id == word_id))
    word = result.scalar_one_or_none()
    if not word:
        raise HTTPException(status_code=404, detail="词条不存在")
    await db.delete(word)
    logger.info(f"全局词条已删除: {word.word} ({word.type})")


# ======================================================================
# 词库优化建议（反馈数据飞轮：聚合接受/忽略行为 → 推荐词库动作）
# ======================================================================

from pydantic import BaseModel, Field

class WhitelistSuggestion(BaseModel):
    """放行词候选：高忽略且零接受"""
    word: str = Field(..., description="问题原文")
    ignore_count: int = Field(..., description="被忽略次数")
    user_count: int = Field(..., description="独立用户数")
    last_ignored_at: Optional[str] = Field(None, description="最近忽略时间")


class CorrectionSuggestion(BaseModel):
    """纠错词候选：高频接受且尚不在词库"""
    word: str = Field(..., description="错误写法")
    suggestion: str = Field(..., description="被接受的建议写法")
    accept_count: int = Field(..., description="被接受次数")
    user_count: int = Field(..., description="独立用户数")


class DictSuggestions(BaseModel):
    """词库优化建议"""
    whitelist: list[WhitelistSuggestion] = []
    correction: list[CorrectionSuggestion] = []


@router.get("/suggestions", response_model=DictSuggestions, summary='词库优化建议（基于用户反馈）')
async def get_dict_suggestions(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:global_dict:edit")),
    min_count: int = Query(3, ge=1, le=50, description="最小出现次数阈值"),
):
    """
    聚合用户对审校建议的接受/忽略行为，产出两类词库优化建议：
    - whitelist：被忽略 ≥min_count 次且从未被接受的词 → 建议加入放行词
    - correction：typo 类被接受 ≥min_count 次 → 建议沉淀为全局纠错词
    已存在于全局词库/放行词中的自动排除。
    """
    from app.models.issue_feedback import IssueFeedback
    from datetime import datetime

    # ---- 放行词候选：高忽略 + 零接受 ----
    rows = await db.execute(
        select(
            IssueFeedback.original,
            func.count().label("cnt"),
            func.count(func.distinct(IssueFeedback.user_id)).label("users"),
            func.max(IssueFeedback.created_at).label("last_at"),
        )
        .where(IssueFeedback.action == "ignore")
        .group_by(IssueFeedback.original)
        .having(func.count() >= min_count)
    )
    ignore_stats = {r.original: r for r in rows.all()}

    # 排除：有任何接受记录的词（说明并非纯误报）
    accept_words = await db.execute(
        select(IssueFeedback.original).where(IssueFeedback.action == "accept")
    )
    accepted_set = {r[0] for r in accept_words.all()}

    # 排除：已在全局放行词或全局词库
    existing_words = await db.execute(
        select(GlobalWord.word).where(GlobalWord.is_active == True, GlobalWord.type == "whitelist")
    )
    existing_set = {r[0] for r in existing_words.all()}

    whitelist_suggestions = []
    for word, stat in ignore_stats.items():
        if word in accepted_set or word in existing_set:
            continue
        last_at = stat.last_at.strftime("%Y-%m-%d %H:%M") if stat.last_at else None
        whitelist_suggestions.append(WhitelistSuggestion(
            word=word, ignore_count=stat.cnt, user_count=stat.users, last_ignored_at=last_at,
        ))
    whitelist_suggestions.sort(key=lambda s: s.ignore_count, reverse=True)

    # ---- 纠错词候选：typo 高频接受 ----
    rows = await db.execute(
        select(
            IssueFeedback.original,
            IssueFeedback.suggestion,
            func.count().label("cnt"),
            func.count(func.distinct(IssueFeedback.user_id)).label("users"),
        )
        .where(
            IssueFeedback.action == "accept",
            IssueFeedback.issue_type == "typo",
            IssueFeedback.suggestion.isnot(None),
            IssueFeedback.suggestion != "",
        )
        .group_by(IssueFeedback.original, IssueFeedback.suggestion)
        .having(func.count() >= min_count)
    )
    # 排除已在全局纠错词（word 相同即视为已有）
    correction_existing = await db.execute(
        select(GlobalWord.word).where(GlobalWord.is_active == True, GlobalWord.type == "correction")
    )
    correction_existing_set = {r[0] for r in correction_existing.all()}

    correction_suggestions = []
    seen_words = set()
    for r in rows.all():
        if r.original in correction_existing_set or r.original in seen_words:
            continue
        seen_words.add(r.original)
        correction_suggestions.append(CorrectionSuggestion(
            word=r.original, suggestion=r.suggestion, accept_count=r.cnt, user_count=r.users,
        ))
    correction_suggestions.sort(key=lambda s: s.accept_count, reverse=True)

    return DictSuggestions(whitelist=whitelist_suggestions[:20], correction=correction_suggestions[:20])
