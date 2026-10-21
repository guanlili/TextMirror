"""后台游客语料：统计、筛选与只读详情。"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.exceptions import NotFoundError
from app.models.proofread import ProofreadRecord

router = APIRouter(prefix="/guest-data", tags=["后台-游客数据"])


def _filters(days: int, record_type: Optional[str], domain: Optional[str], keyword: Optional[str]):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    filters = [ProofreadRecord.owner_kind == "guest", ProofreadRecord.created_at >= start]
    if record_type:
        filters.append(ProofreadRecord.type == record_type)
    if domain:
        filters.append(ProofreadRecord.domain == domain)
    if keyword:
        escaped = keyword.replace("%", "\\%").replace("_", "\\_")
        filters.append(ProofreadRecord.original_text.ilike(f"%{escaped}%", escape="\\"))
    return filters


@router.get("/stats", summary="游客语料统计（按时间范围）")
async def guest_data_stats(
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:access")),
):
    filters = _filters(days, None, None, None)
    row = (await db.execute(
        select(
            func.count().label("records"),
            func.coalesce(func.sum(ProofreadRecord.quota_weight), 0).label("proofreads"),
            func.count(func.distinct(ProofreadRecord.guest_id)).label("guests"),
            func.coalesce(func.sum(func.length(ProofreadRecord.original_text)), 0).label("chars"),
            func.coalesce(func.sum(ProofreadRecord.total_issues), 0).label("issues"),
            func.count().filter(ProofreadRecord.type == "text").label("texts"),
            func.count().filter(ProofreadRecord.type == "document").label("documents"),
            func.count().filter(ProofreadRecord.type == "polish").label("polishes"),
        ).where(*filters)
    )).one()
    domain_rows = (await db.execute(
        select(ProofreadRecord.domain, func.count().label("count"))
        .where(*filters)
        .group_by(ProofreadRecord.domain)
        .order_by(func.count().desc())
        .limit(10)
    )).all()

    record_count = int(row.records or 0)
    issue_count = int(row.issues or 0)
    return {
        "days": days,
        "records": record_count,
        "proofreads": int(row.proofreads or 0),
        "guests": int(row.guests or 0),
        "chars": int(row.chars or 0),
        "issues": issue_count,
        "avg_issues": round(issue_count / record_count, 1) if record_count else 0,
        "texts": int(row.texts or 0),
        "documents": int(row.documents or 0),
        "polishes": int(row.polishes or 0),
        "domains": [{"domain": r.domain, "count": int(r.count)} for r in domain_rows],
    }


@router.get("/records", summary="游客语料列表（分页）")
async def list_guest_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    days: int = Query(30, ge=1, le=365),
    record_type: Optional[str] = Query(None, alias="type", pattern="^(text|document|polish)$"),
    domain: Optional[str] = Query(None, max_length=50),
    keyword: Optional[str] = Query(None, max_length=100),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:access")),
):
    filters = _filters(days, record_type, domain, keyword)
    total = await db.scalar(
        select(func.count()).select_from(ProofreadRecord).where(*filters)
    ) or 0
    rows = (await db.execute(
        select(
            ProofreadRecord.id,
            ProofreadRecord.type,
            ProofreadRecord.domain,
            ProofreadRecord.total_issues,
            ProofreadRecord.quota_weight,
            func.length(ProofreadRecord.original_text).label("chars"),
            func.substr(ProofreadRecord.original_text, 1, 121).label("preview"),
            ProofreadRecord.guest_id,
            ProofreadRecord.created_at,
        )
        .where(*filters)
        .order_by(desc(ProofreadRecord.created_at), desc(ProofreadRecord.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all()

    items = []
    for row in rows:
        chars = int(row.chars or 0)
        preview = row.preview or ""
        items.append({
            "id": row.id,
            "type": row.type,
            "domain": row.domain,
            "total_issues": row.total_issues,
            "quota_weight": row.quota_weight,
            "chars": chars,
            "preview": preview + ("..." if chars > 120 else ""),
            "guest_label": (row.guest_id or "")[:8],
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else None,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/records/{record_id}", summary="游客语料详情（只读）")
async def get_guest_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:access")),
):
    record = await db.scalar(
        select(ProofreadRecord).where(
            ProofreadRecord.id == record_id,
            ProofreadRecord.owner_kind == "guest",
        )
    )
    if record is None:
        raise NotFoundError(code="RECORD_NOT_FOUND", message="游客记录不存在")

    result = record.result or {}
    return {
        "id": record.id,
        "type": record.type,
        "domain": record.domain,
        "total_issues": record.total_issues,
        "quota_weight": record.quota_weight,
        "guest_label": (record.guest_id or "")[:8],
        "original_text": record.original_text,
        "modified_text": record.modified_text,
        "issues": result.get("issues") or [],
        "created_at": record.created_at.strftime("%Y-%m-%d %H:%M:%S") if record.created_at else None,
    }
