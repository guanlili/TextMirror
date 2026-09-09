"""
TextMirror 管理后台仪表盘 API
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_permission
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
    """获取仪表盘统计数据"""
    today = local_today().isoformat()
    today_expr_proofread = _today_expr(ProofreadRecord.created_at) == today
    today_expr_doc = _today_expr(UploadedDocument.created_at) == today

    # 校对统计：今日次数 + 总次数 + 今日活跃用户（1 条 SQL）
    proofread_stats = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(today_expr_proofread).label("today"),
            func.count(func.distinct(ProofreadRecord.user_id))
            .filter(today_expr_proofread, ProofreadRecord.user_id.isnot(None))
            .label("active_today"),
        ).select_from(ProofreadRecord)
    ).one()
    total_proofread_count = proofread_stats.total or 0
    today_proofread_count = proofread_stats.today or 0
    active_users_today = proofread_stats.active_today or 0

    # 总用户数
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0

    # 总 token 用量（从 JSON 字段 token_usage->>'total_tokens' 聚合）
    total_token_usage = 0
    try:
        token_result = await db.execute(
            text("SELECT COALESCE(SUM((token_usage->>'total_tokens')::int), 0) FROM proofread_records WHERE token_usage IS NOT NULL")
        )
        total_token_usage = token_result.scalar() or 0
    except Exception as e:
        logger.warning(f"统计 token 用量失败: {e}")

    # 文档统计：今日上传 + 总上传（1 条 SQL）
    doc_stats = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(today_expr_doc).label("today"),
        ).select_from(UploadedDocument).where(UploadedDocument.status != "deleted")
    ).one()
    total_document_count = doc_stats.total or 0
    today_document_count = doc_stats.today or 0

    return {
        "today_proofread_count": today_proofread_count,
        "total_proofread_count": total_proofread_count,
        "total_users": total_users,
        "active_users_today": active_users_today,
        "total_token_usage": total_token_usage,
        "today_document_count": today_document_count,
        "total_document_count": total_document_count,
    }
