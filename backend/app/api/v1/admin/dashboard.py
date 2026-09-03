"""
TextMirror 管理后台仪表盘 API
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date, text
from loguru import logger

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

    # 今日校对次数
    today_count_result = await db.execute(
        select(func.count()).select_from(ProofreadRecord)
        .where(_today_expr(ProofreadRecord.created_at) == today)
    )
    today_proofread_count = today_count_result.scalar() or 0

    # 总校对次数
    total_count_result = await db.execute(
        select(func.count()).select_from(ProofreadRecord)
    )
    total_proofread_count = total_count_result.scalar() or 0

    # 总用户数
    total_users_result = await db.execute(
        select(func.count()).select_from(User)
    )
    total_users = total_users_result.scalar() or 0

    # 今日活跃用户（有校对记录的独立用户数）
    active_today_result = await db.execute(
        select(func.count(func.distinct(ProofreadRecord.user_id)))
        .where(_today_expr(ProofreadRecord.created_at) == today)
        .where(ProofreadRecord.user_id.isnot(None))
    )
    active_users_today = active_today_result.scalar() or 0

    # 总 token 用量（从 JSON 字段 token_usage->>'total_tokens' 聚合）
    total_token_usage = 0
    try:
        token_result = await db.execute(
            text("SELECT COALESCE(SUM((token_usage->>'total_tokens')::int), 0) FROM proofread_records WHERE token_usage IS NOT NULL")
        )
        total_token_usage = token_result.scalar() or 0
    except Exception as e:
        logger.warning(f"统计 token 用量失败: {e}")

    # 今日上传文档数
    today_doc_result = await db.execute(
        select(func.count()).select_from(UploadedDocument)
        .where(_today_expr(UploadedDocument.created_at) == today)
        .where(UploadedDocument.status != "deleted")
    )
    today_document_count = today_doc_result.scalar() or 0

    # 总上传文档数
    total_doc_result = await db.execute(
        select(func.count()).select_from(UploadedDocument)
        .where(UploadedDocument.status != "deleted")
    )
    total_document_count = total_doc_result.scalar() or 0

    return {
        "today_proofread_count": today_proofread_count,
        "total_proofread_count": total_proofread_count,
        "total_users": total_users,
        "active_users_today": active_users_today,
        "total_token_usage": total_token_usage,
        "today_document_count": today_document_count,
        "total_document_count": total_document_count,
    }
