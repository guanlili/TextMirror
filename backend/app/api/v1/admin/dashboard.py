"""
TextMirror 管理后台仪表盘 API
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_permission
from app.models.llm_usage import LLMUsage
from app.models.proofread import ProofreadRecord
from app.models.uploaded_document import UploadedDocument
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["仪表盘"])

# 业务时区：「今日」按北京时间切日（与用户配额共用同一口径）
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def local_today() -> datetime.date:
    """当前业务时区的日期"""
    return datetime.now(LOCAL_TZ).date()


def _today_expr(col):
    """将 timestamptz 列转换为业务时区日期（PostgreSQL: AT TIME ZONE）"""
    return func.to_char(col.op("AT TIME ZONE")("Asia/Shanghai"), "YYYY-MM-DD")


@router.get("/stats", summary='获取仪表盘统计数据')
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:access")),
):
    """获取仪表盘统计数据（Redis 缓存 30 秒，避免运营页反复全表聚合）"""
    from app.core.redis import get_redis

    cache_key = "textmirror:dashboard_stats"
    try:
        cached = await get_redis().get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"仪表盘缓存读取失败（降级查库）: {e}")

    # 今日用 UTC 时间范围比较而非 to_char(AT TIME ZONE)：可走 created_at 索引，且 SQLite 测试可用
    from app.core.rate_limit import _shanghai_today_range

    today_start, today_end = _shanghai_today_range()
    today_expr_proofread = (ProofreadRecord.created_at >= today_start) & (ProofreadRecord.created_at < today_end)
    today_expr_doc = (UploadedDocument.created_at >= today_start) & (UploadedDocument.created_at < today_end)

    # 校对统计：今日/总量按登录用户与游客拆分（1 条 SQL）
    proofread_stats = (await db.execute(
        select(
            func.coalesce(func.sum(ProofreadRecord.quota_weight), 0).label("total"),
            func.coalesce(func.sum(ProofreadRecord.quota_weight).filter(today_expr_proofread), 0).label("today"),
            func.coalesce(
                func.sum(ProofreadRecord.quota_weight).filter(ProofreadRecord.owner_kind == "guest"), 0
            ).label("total_guest"),
            func.coalesce(
                func.sum(ProofreadRecord.quota_weight)
                .filter(today_expr_proofread, ProofreadRecord.owner_kind == "guest"), 0
            ).label("today_guest"),
            func.coalesce(
                func.sum(ProofreadRecord.quota_weight).filter(ProofreadRecord.owner_kind == "user"), 0
            ).label("total_user"),
            func.coalesce(
                func.sum(ProofreadRecord.quota_weight)
                .filter(today_expr_proofread, ProofreadRecord.owner_kind == "user"), 0
            ).label("today_user"),
            func.count(func.distinct(ProofreadRecord.user_id))
            .filter(today_expr_proofread, ProofreadRecord.owner_kind == "user")
            .label("active_today"),
            func.count(func.distinct(ProofreadRecord.guest_id))
            .filter(today_expr_proofread, ProofreadRecord.owner_kind == "guest")
            .label("active_guests_today"),
        ).select_from(ProofreadRecord)
    )).one()
    total_proofread_count = int(proofread_stats.total or 0)
    today_proofread_count = int(proofread_stats.today or 0)
    total_guest_count = int(proofread_stats.total_guest or 0)
    today_guest_count = int(proofread_stats.today_guest or 0)
    total_user_count = int(proofread_stats.total_user or 0)
    today_user_count = int(proofread_stats.today_user or 0)
    active_users_today = int(proofread_stats.active_today or 0)
    active_guests_today = int(proofread_stats.active_guests_today or 0)

    # 总用户数
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0

    total_token_usage = (await db.execute(
        select(func.coalesce(func.sum(LLMUsage.total_tokens), 0))
    )).scalar_one()

    # 文档统计：今日上传 + 总上传（1 条 SQL）
    doc_stats = (await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(today_expr_doc).label("today"),
        ).select_from(UploadedDocument).where(UploadedDocument.status != "deleted")
    )).one()
    total_document_count = doc_stats.total or 0
    today_document_count = doc_stats.today or 0

    payload = {
        "today_proofread_count": today_proofread_count,
        "total_proofread_count": total_proofread_count,
        "today_guest_proofread_count": today_guest_count,
        "total_guest_proofread_count": total_guest_count,
        "today_user_proofread_count": today_user_count,
        "total_user_proofread_count": total_user_count,
        "total_users": total_users,
        "active_users_today": active_users_today,
        "active_guests_today": active_guests_today,
        "total_token_usage": total_token_usage,
        "today_document_count": today_document_count,
        "total_document_count": total_document_count,
    }
    try:
        # SUM() 在 PostgreSQL 返回 Decimal，default=int 保持与正常响应一致的数值类型
        await get_redis().set(cache_key, json.dumps(payload, ensure_ascii=False, default=int), ex=30)
    except Exception as e:
        logger.warning(f"仪表盘缓存写入失败（不影响响应）: {e}")
    return payload


@router.get("/model-usage", summary="按业务和模型查看实际调用用量（非费用账单）")
async def get_model_usage(
    days: int = Query(30, ge=1, le=90),
    limit: int = Query(200, ge=1, le=1000, description="最大返回分组数"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:access")),
):
    now = datetime.now(LOCAL_TZ)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    columns = [LLMUsage.business, LLMUsage.operation, LLMUsage.config_id, LLMUsage.config_name, LLMUsage.model]
    rows = (await db.execute(
        select(
            *columns,
            func.count().label("calls"),
            func.count().filter(LLMUsage.outcome == "error").label("errors"),
            func.count().filter(LLMUsage.outcome == "incomplete").label("incomplete"),
            func.count().filter(LLMUsage.outcome == "cancelled").label("cancelled"),
            func.count().filter(LLMUsage.total_tokens.is_(None)).label("unknown_usage_calls"),
            func.coalesce(func.sum(LLMUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(LLMUsage.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(LLMUsage.search_queries), 0).label("search_queries"),
            func.avg(LLMUsage.elapsed_ms).label("average_ms"),
        ).where(LLMUsage.created_at >= start.astimezone(timezone.utc), LLMUsage.created_at <= now.astimezone(timezone.utc))
        .group_by(*columns).order_by(func.sum(LLMUsage.total_tokens).desc(), LLMUsage.business, LLMUsage.model)
        .limit(limit)
    )).mappings().all()
    items = [{**row, "average_ms": round(float(row["average_ms"] or 0))} for row in rows]
    tracked_since = (await db.execute(select(func.min(LLMUsage.created_at)))).scalar_one()
    if tracked_since is not None and tracked_since.tzinfo is None:
        tracked_since = tracked_since.replace(tzinfo=timezone.utc)
    return {
        "days": days,
        "tracked_since": tracked_since,
        "items": items,
        "calls": sum(row["calls"] for row in items),
        "total_tokens": sum(row["total_tokens"] for row in items),
        "unknown_usage_calls": sum(row["unknown_usage_calls"] for row in items),
    }


@router.get("/trend", summary='近 N 日校对趋势（按日量+活跃用户+token）')
async def get_usage_trend(
    days: int = Query(30, ge=7, le=90, description="统计天数（7-90）"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:access")),
):
    """
    近 N 日校对趋势：按日校对次数（SUM(quota_weight) 口径）、活跃用户数。
    日期按业务时区在 SQL 内分组（AT TIME ZONE，与 /stats 的 _today_expr 同模式），
    此前逐日循环查库（90 天 = 90 次串行往返）。
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    now_local = datetime.now(tz)
    start_local = (now_local - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc)

    day_expr = _today_expr(ProofreadRecord.created_at)
    rows = (await db.execute(
        select(
            day_expr.label("day"),
            func.coalesce(func.sum(ProofreadRecord.quota_weight), 0).label("count"),
            func.coalesce(
                func.sum(ProofreadRecord.quota_weight)
                .filter(ProofreadRecord.owner_kind == "guest"), 0
            ).label("guest_count"),
            func.coalesce(
                func.sum(ProofreadRecord.quota_weight)
                .filter(ProofreadRecord.owner_kind == "user"), 0
            ).label("user_count"),
            func.count(func.distinct(ProofreadRecord.user_id))
            .filter(ProofreadRecord.owner_kind == "user").label("users"),
            func.count(func.distinct(ProofreadRecord.guest_id))
            .filter(ProofreadRecord.owner_kind == "guest").label("guest_users"),
        )
        .where(ProofreadRecord.created_at >= start_utc)
        .group_by(day_expr)
    )).all()
    by_day = {
        r.day: (int(r.count or 0), int(r.guest_count or 0), int(r.user_count or 0),
                int(r.users or 0), int(r.guest_users or 0))
        for r in rows
    }

    daily = []
    for i in range(days):
        day = start_local + timedelta(days=i)
        count, guest_count, user_count, users, guest_users = by_day.get(
            day.strftime("%Y-%m-%d"), (0, 0, 0, 0, 0)
        )
        daily.append({
            "date": day.strftime("%m-%d"),
            "count": count,
            "guest_count": guest_count,
            "user_count": user_count,
            "users": users,
            "guest_users": guest_users,
        })

    return {"days": days, "daily": daily}


@router.get("/top-users", summary='校对量 Top 用户榜（近 N 日）')
async def get_top_users(
    days: int = Query(30, ge=7, le=90, description="统计天数（7-90）"),
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:access")),
):
    """按 SUM(quota_weight) 排名的活跃用户榜（近 N 日）。"""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    start_utc = (datetime.now(tz) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)

    rows = (await db.execute(
        select(
            User.id,
            User.username,
            User.employee_id,
            func.coalesce(func.sum(ProofreadRecord.quota_weight), 0).label("count"),
        )
        .join(ProofreadRecord, ProofreadRecord.user_id == User.id)
        .where(ProofreadRecord.created_at >= start_utc)
        .group_by(User.id, User.username, User.employee_id)
        .order_by(func.sum(ProofreadRecord.quota_weight).desc())
        .limit(limit)
    )).all()

    return {
        "days": days,
        "items": [
            {"user_id": r.id, "username": r.username, "employee_id": r.employee_id, "count": int(r.count)}
            for r in rows
        ],
    }
