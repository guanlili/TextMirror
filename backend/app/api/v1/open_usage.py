"""
TextMirror 开放 API——用量统计模块
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.open_common import ERROR_RESPONSES
from app.core.database import get_db
from app.core.dependencies import get_current_user_or_apikey
from app.models.api_key import ApiKey
from app.models.proofread import ProofreadRecord
from app.models.user import User
from app.schemas.open import OpenUsageDailyItem, OpenUsageKeyItem, OpenUsageResponse

router = APIRouter(tags=["开放API"])


@router.get(
    "/usage",
    response_model=OpenUsageResponse,
    summary="用量统计",
    description=(
        "查询 API 调用用量（近 N 天，按日/按密钥聚合）。\n\n"
        "**统计口径**：成功调用次数（失败/退还额度的调用不计）。"
        "多模型对比一次请求按成功模型数计。日期按 Asia/Shanghai 业务时区切日。\n\n"
        "**范围**：API 密钥调用 → 该密钥的用量；JWT 登录 Token 调用 → 名下全部密钥的合计。\n\n"
        "```bash\n"
        'curl -H "Authorization: Bearer tm_..." ".../api/v1/open/usage?days=7"\n'
        "```"
    ),
    responses=ERROR_RESPONSES,
)
async def open_usage(
    days: int = Query(7, ge=1, le=90, description="统计周期（天），默认 7"),
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """开放 API 用量统计（只读，不计费不限流）"""
    user, api_key = auth
    tz = ZoneInfo("Asia/Shanghai")
    now_local = datetime.now(tz)
    start_local = (now_local - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    base_filters = [ProofreadRecord.api_key_id.is_not(None)]
    if api_key is not None:
        scope = "api_key"
        base_filters.append(ProofreadRecord.api_key_id == api_key.id)
    else:
        scope = "user"
        base_filters.append(ProofreadRecord.api_key_id.in_(
            select(ApiKey.id).where(ApiKey.user_id == user.id)
        ))

    # 按日聚合：日期边界在 Python 侧算好（Asia/Shanghai），SQL 只做范围计数——
    # 与配额检查同模式，兼容 PostgreSQL 与 SQLite，且每个子查询都走 (api_key_id, created_at) 索引。
    # N 天合并为一条 UNION ALL（此前逐日循环 = N 次串行往返）
    day_stmts = []
    for i in range(days):
        day_start_local = start_local + timedelta(days=i)
        day_start_utc = day_start_local.astimezone(timezone.utc)
        day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc)
        day_stmts.append(
            select(func.coalesce(func.sum(ProofreadRecord.quota_weight), 0))
            .select_from(ProofreadRecord)
            .where(
                *base_filters,
                ProofreadRecord.created_at >= day_start_utc,
                ProofreadRecord.created_at < day_end_utc,
            )
        )
    day_counts = [int(c or 0) for c in (await db.execute(union_all(*day_stmts))).scalars()]
    daily = [
        OpenUsageDailyItem(date=(start_local + timedelta(days=i)).strftime("%Y-%m-%d"), count=day_counts[i])
        for i in range(days)
    ]

    key_rows = (await db.execute(
        select(
            ApiKey.id,
            ApiKey.name,
            ApiKey.key_prefix,
            ApiKey.key_suffix,
            func.coalesce(func.sum(ProofreadRecord.quota_weight), 0).label("count"),
        )
        .join(ProofreadRecord, ProofreadRecord.api_key_id == ApiKey.id)
        .where(*base_filters, ProofreadRecord.created_at >= start_local.astimezone(timezone.utc))
        .group_by(ApiKey.id, ApiKey.name, ApiKey.key_prefix, ApiKey.key_suffix)
        .order_by(func.sum(ProofreadRecord.quota_weight).desc())
    )).all()
    keys = [
        OpenUsageKeyItem(
            key_id=r.id,
            key_display=f"{r.key_prefix}...{r.key_suffix}",
            key_name=r.name,
            count=r.count,
        )
        for r in key_rows
    ]

    return OpenUsageResponse(
        days=days,
        total=sum(d.count for d in daily),
        daily=daily,
        keys=keys,
        scope=scope,
    )


