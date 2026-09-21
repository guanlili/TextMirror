"""
TextMirror 统一分页基础设施
提供 PageParams 依赖 + paginate_query 辅助函数

用法：
    @router.get("/items")
    async def list_items(
        page: PageParams = Depends(),
        db: AsyncSession = Depends(get_db),
    ):
        query = select(Item).where(...)
        total, items = await paginate_query(db, query, page)
        return {"items": items, "total": total, "page": page.page, "page_size": page.page_size}
"""
from dataclasses import dataclass

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class PageParams:
    """分页参数（作为 Depends() 注入到路由函数）"""
    page: int = Query(1, ge=1, description="页码")
    page_size: int = Query(20, ge=1, le=200, description="每页条数")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


async def paginate_query(
    db: AsyncSession,
    query,
    page: PageParams,
    *,
    count_query=None,
) -> tuple[int, list]:
    """
    执行分页查询，返回 (total, items)。
    count_query 可覆盖默认的 COUNT 查询（复杂查询优化用）。
    """
    if count_query is None:
        count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    items_query = query.offset(page.offset).limit(page.page_size)
    result = await db.execute(items_query)
    items = list(result.scalars().all())

    return total, items
