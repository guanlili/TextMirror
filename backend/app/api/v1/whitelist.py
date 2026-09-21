"""
TextMirror 放行词（白名单）API
仅登录用户可使用
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.pagination import PageParams, paginate_query
from app.models.dictionary import WhitelistWord
from app.schemas.dictionary import WhitelistCreate, WhitelistResponse, WhitelistUpdate

router = APIRouter(prefix="/whitelist", tags=["放行词"])


@router.get("", summary='获取当前用户的放行词列表')
async def list_whitelist(
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    page: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """获取当前用户的放行词列表"""
    query = select(WhitelistWord).where(WhitelistWord.user_id == current_user.id)
    if keyword:
        escaped = keyword.replace("%", "\\%").replace("_", "\\_")
        query = query.where(WhitelistWord.word.contains(escaped))
    query = query.order_by(WhitelistWord.created_at.desc())
    total, items = await paginate_query(db, query, page)
    return {"items": [WhitelistResponse.model_validate(w) for w in items], "total": total, "page": page.page, "page_size": page.page_size}


@router.post("", response_model=WhitelistResponse, status_code=201, summary='添加放行词')
async def create_whitelist_word(
    data: WhitelistCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """添加放行词"""
    # 检查是否已存在
    exists = await db.execute(
        select(WhitelistWord).where(
            WhitelistWord.user_id == current_user.id,
            WhitelistWord.word == data.word,
        )
    )
    if exists.scalar_one_or_none():
        raise ConflictError(code="WHITELIST_WORD_EXISTS", message="该放行词已存在")

    word = WhitelistWord(
        user_id=current_user.id,
        word=data.word,
        type=data.type,
        remark=data.remark,
        expire_at=data.expire_at,
    )
    db.add(word)
    await db.flush()
    await db.refresh(word)
    return word


@router.put("/{word_id}", response_model=WhitelistResponse, summary='更新放行词')
async def update_whitelist_word(
    word_id: int,
    data: WhitelistUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """更新放行词"""
    result = await db.execute(
        select(WhitelistWord).where(
            WhitelistWord.id == word_id,
            WhitelistWord.user_id == current_user.id,
        )
    )
    word = result.scalar_one_or_none()
    if not word:
        raise NotFoundError(code="WHITELIST_WORD_NOT_FOUND", message="放行词不存在")

    if data.word is not None:
        word.word = data.word
    if data.type is not None:
        word.type = data.type
    if data.remark is not None:
        word.remark = data.remark
    if data.expire_at is not None:
        word.expire_at = data.expire_at

    await db.flush()
    await db.refresh(word)
    return word


@router.delete("/{word_id}", status_code=204, summary='删除放行词')
async def delete_whitelist_word(
    word_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """删除放行词"""
    result = await db.execute(
        select(WhitelistWord).where(
            WhitelistWord.id == word_id,
            WhitelistWord.user_id == current_user.id,
        )
    )
    word = result.scalar_one_or_none()
    if not word:
        raise NotFoundError(code="WHITELIST_WORD_NOT_FOUND", message="放行词不存在")

    await db.delete(word)


@router.post("/batch", status_code=201, summary='批量添加放行词')
async def batch_create_whitelist(
    words: list[WhitelistCreate],
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """批量添加放行词（单次上限 1000 条）"""
    if len(words) > 1000:
        raise BadRequestError(code="BATCH_TOO_LARGE", message="单次最多添加 1000 个放行词")

    # 一次查回已存在的词（此前逐词一条 SELECT，千条请求 = 千次往返的长事务）
    existing = set((await db.execute(
        select(WhitelistWord.word).where(
            WhitelistWord.user_id == current_user.id,
            WhitelistWord.word.in_([w.word for w in words]),
        )
    )).scalars())

    added = 0
    for item in words:
        # 请求内重复词也只加一条
        if item.word in existing:
            continue
        existing.add(item.word)
        db.add(WhitelistWord(
            user_id=current_user.id,
            word=item.word,
            type=item.type,
            remark=item.remark,
            expire_at=item.expire_at,
        ))
        added += 1

    await db.flush()
    return {"message": f"成功添加 {added} 个放行词", "count": added}
