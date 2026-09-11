"""
TextMirror 管理后台 - API 密钥全量管理
管理员查看所有用户的开放 API 密钥，支持吊销/恢复、调配额、改备注
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.api_keys import compute_key_status
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.rate_limit import get_api_key_daily_usage
from app.models.api_key import ApiKey
from app.models.user import User
from app.schemas.api_key import ApiKeyAdminUpdateRequest
from app.services.audit_log import record_audit_log

router = APIRouter(prefix="/api-keys", tags=["管理后台-API密钥"])


@router.get("", summary="全量密钥列表（分页 + 筛选）")
async def list_all_api_keys(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    keyword: Optional[str] = Query(None, description="关键词（密钥名称/用户名/工号）"),
    key_status: Optional[str] = Query(None, description="状态筛选: active/revoked/expired"),
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_permission("admin:access")),
):
    """跨用户列出全部密钥（脱敏展示，含归属用户与今日用量）"""
    base_query = select(ApiKey, User).join(User, ApiKey.user_id == User.id)

    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        kw = f"%{escaped}%"
        base_query = base_query.where(
            or_(
                ApiKey.name.ilike(kw, escape="\\"),
                User.username.ilike(kw, escape="\\"),
                User.employee_id.ilike(kw, escape="\\"),
            )
        )

    now = func.now()
    if key_status == "revoked":
        base_query = base_query.where(ApiKey.is_active.is_(False))
    elif key_status == "expired":
        base_query = base_query.where(
            ApiKey.is_active.is_(True),
            ApiKey.expires_at.is_not(None),
            ApiKey.expires_at <= now,
        )
    elif key_status == "active":
        base_query = base_query.where(
            ApiKey.is_active.is_(True),
            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        )

    total_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        base_query.order_by(desc(ApiKey.id)).offset((page - 1) * page_size).limit(page_size)
    )
    rows = result.all()

    # 近 7 天各密钥成功调用量（SUM(quota_weight)，与配额/用量口径一致），一次聚合查完
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from app.models.proofread import ProofreadRecord

    tz = ZoneInfo("Asia/Shanghai")
    start_utc = (datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)).astimezone(timezone.utc)
    key_ids = [k.id for k, _ in rows]
    usage_rows = (await db.execute(
        select(ProofreadRecord.api_key_id, func.coalesce(func.sum(ProofreadRecord.quota_weight), 0))
        .where(ProofreadRecord.api_key_id.in_(key_ids), ProofreadRecord.created_at >= start_utc)
        .group_by(ProofreadRecord.api_key_id)
    )).all() if key_ids else []
    used_7d_map = {r[0]: int(r[1]) for r in usage_rows}

    # 各密钥最近一次回调投递状态（Redis 不可用时静默为空）
    webhook_last_map: dict = {}
    try:
        from app.core.redis import get_redis

        if key_ids:
            redis = get_redis()
            pipe = redis.pipeline()
            for kid in key_ids:
                pipe.hgetall(f"textmirror:webhook_status:{kid}")
            for kid, raw in zip(key_ids, await pipe.execute()):
                webhook_last_map[kid] = raw or None
    except Exception as e:
        logger.warning(f"读取回调投递状态失败（不影响列表）: {e}")

    items = []
    for k, u in rows:
        used_today = await get_api_key_daily_usage(k)
        items.append({
            "id": k.id,
            "user_id": u.id,
            "username": u.username,
            "employee_id": u.employee_id,
            "name": k.name,
            "key_display": f"{k.key_prefix}...{k.key_suffix}",
            "daily_quota": k.daily_quota,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "is_active": k.is_active,
            "status": compute_key_status(k),
            "used_today": used_today,
            "used_7d": used_7d_map.get(k.id, 0),
            "remark": k.remark,
            "webhook_url": k.webhook_url,
            "webhook_last": webhook_last_map.get(k.id),
        })

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.patch("/{key_id}", summary="更新密钥（吊销/恢复/调配额/改备注）")
async def update_api_key(
    key_id: int,
    body: ApiKeyAdminUpdateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_permission("admin:access")),
):
    """仅修改显式传入的字段；恢复密钥时校验该用户的活跃密钥上限"""
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id)
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=404, detail="密钥不存在")

    changes: dict = {}
    fields_set = body.model_fields_set

    if "is_active" in fields_set and body.is_active is not None and body.is_active != api_key.is_active:
        if body.is_active:
            count_result = await db.execute(
                select(func.count()).select_from(ApiKey).where(
                    ApiKey.user_id == api_key.user_id,
                    ApiKey.is_active.is_(True),
                )
            )
            active_count = count_result.scalar() or 0
            if active_count >= settings.API_KEY_MAX_PER_USER:
                raise HTTPException(
                    status_code=400,
                    detail=f"该用户活跃密钥已达上限（{settings.API_KEY_MAX_PER_USER}），无法恢复",
                )
        api_key.is_active = body.is_active
        changes["is_active"] = body.is_active

    if "daily_quota" in fields_set and body.daily_quota != api_key.daily_quota:
        api_key.daily_quota = body.daily_quota
        changes["daily_quota"] = body.daily_quota

    if "remark" in fields_set and body.remark != api_key.remark:
        api_key.remark = body.remark
        changes["remark"] = body.remark

    if not changes:
        return {"message": "无变更", "changes": {}}

    record_audit_log(
        http_request, "apikey_admin_update", user=_admin,
        extra_params={"key_id": api_key.id, "key_name": api_key.name, "changes": changes},
    )

    return {
        "message": "已更新",
        "changes": changes,
        "status": compute_key_status(api_key),
    }
