import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.core.secret_crypto import encrypt_secret
from app.models.fact_check import FactCheckConfig, FactCheckRun
from app.models.llm_config import LLMConfig
from app.models.user import User
from app.schemas.fact_check import (
    DAILY_LIMIT,
    MAX_TEXT_CHARS,
    FactCheckCreate,
    FactCheckOptions,
    FactCheckRunResponse,
    FactCheckSettingsResponse,
    FactCheckSettingsUpdate,
)
from app.services.fact_check_search import model_search_unavailable_reason
from app.services.review import load_review_record


class _SanitizedValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                detail = [{field: error[field] for field in ("type", "loc", "msg")} for error in exc.errors()]
                raise HTTPException(status_code=422, detail=detail) from None

        return sanitized_handler


router = APIRouter(prefix="/fact-check", tags=["事实核查"])
admin_router = APIRouter(prefix="/fact-check", tags=["事实核查配置"], route_class=_SanitizedValidationRoute)
ACTIVE = ("PENDING", "RUNNING")


async def _active_model(db: AsyncSession) -> LLMConfig | None:
    return await db.scalar(select(LLMConfig).where(LLMConfig.is_enabled.is_(True), LLMConfig.is_active.is_(True)))


def _model_name(model: LLMConfig | None) -> str:
    return f"{model.name} ({model.model})" if model else ""


def _model_search_reason(model: LLMConfig | None) -> str:
    if model is None:
        return "尚未配置可用的大模型，请联系管理员"
    return model_search_unavailable_reason(model.provider, model.api_base, model.model)


def _native_unavailable_reason(model: LLMConfig | None) -> str:
    reason = _model_search_reason(model)
    if reason:
        return f"{reason}；不会自动切换至 Tavily"
    if not model.api_key.strip():
        return "尚未配置当前大模型的 API 密钥，请联系管理员；不会自动切换至 Tavily"
    return ""


def _unavailable_reason(config: FactCheckConfig | None, model: LLMConfig | None) -> str:
    if not config or not config.enabled:
        return "事实核查尚未启用，请联系管理员在系统设置中配置"
    if config.search_provider == "model":
        return _native_unavailable_reason(model)
    if model is None:
        return "尚未配置可用的大模型，请联系管理员"
    if not model.api_key.strip():
        return "尚未配置当前大模型的 API 密钥，请联系管理员"
    if not config.api_key.strip():
        return "尚未配置 Tavily 搜索密钥，请联系管理员；不会自动切换至模型原生联网"
    return ""


def _settings_response(config: FactCheckConfig | None, model: LLMConfig | None) -> FactCheckSettingsResponse:
    reason = _model_search_reason(model)
    return FactCheckSettingsResponse(
        enabled=bool(config and config.enabled),
        provider=config.search_provider if config else "model",
        api_key_configured=bool(config and config.api_key.strip()),
        model_name=_model_name(model), model_search_supported=not reason, model_search_reason=reason,
        max_claims=config.max_claims if config else 10,
        sources=config.sources if config else [],
    )


def _response(run: FactCheckRun) -> FactCheckRunResponse:
    return FactCheckRunResponse(
        id=run.id, record_id=run.record_id, mode=run.mode, provider=run.search_provider, status=run.status,
        progress=run.progress, message=run.message, error_code=run.error_code,
        result=run.result_json, source_hash=run.source_hash,
        source_ids=[source["id"] for source in run.sources],
        created_at=run.created_at, finished_at=run.finished_at,
    )


async def _expire_stale(db: AsyncSession, user_id: int) -> None:
    now = datetime.now(timezone.utc)
    await db.execute(update(FactCheckRun).where(
        FactCheckRun.user_id == user_id,
        ((FactCheckRun.status == "PENDING") & (FactCheckRun.created_at < now - timedelta(minutes=15)))
        | ((FactCheckRun.status == "RUNNING") & (FactCheckRun.started_at < now - timedelta(minutes=6))),
    ).values(status="FAILURE", error_code="TASK_EXPIRED", message="任务未能按时完成，可重新发起核查", finished_at=now))


async def _owned_run(db: AsyncSession, run_id: int, user_id: int) -> FactCheckRun:
    await _expire_stale(db, user_id)
    run = await db.scalar(select(FactCheckRun).where(FactCheckRun.id == run_id, FactCheckRun.user_id == user_id))
    if run is None:
        raise HTTPException(404, "核查任务不存在")
    return run


@router.get("/options", response_model=FactCheckOptions, summary="获取核查能力与可用可信信源")
async def options(db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    config = await db.get(FactCheckConfig, 1)
    model = await _active_model(db)
    reason = _unavailable_reason(config, model)
    return FactCheckOptions(
        available=not reason, unavailable_reason=reason,
        provider=config.search_provider if config else "model", model_name=_model_name(model),
        max_claims=config.max_claims if config else 10,
        sources=[source for source in config.sources if source.get("is_enabled", True)] if config else [],
    )


@router.post("/runs", response_model=FactCheckRunResponse, status_code=202, summary="核查已保存审校记录的原文")
async def create_run(data: FactCheckCreate, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    record = await load_review_record(db, data.record_id, user.id)
    await require_permission(f"proofread:{record.type}")(current_user=user, db=db)
    request_hash = hashlib.sha256(json.dumps(
        data.model_dump(mode="json", exclude={"request_id"}) | {"source_ids": sorted(data.source_ids)},
        sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    # Serialize per-account submissions so the active-job and daily-budget checks remain atomic.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    await _expire_stale(db, user.id)
    existing = await db.scalar(select(FactCheckRun).where(
        FactCheckRun.user_id == user.id, FactCheckRun.request_id == str(data.request_id),
    ))
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(409, "此请求 ID 已用于不同的核查参数，请生成新的请求 ID")
        return _response(existing)
    config = await db.get(FactCheckConfig, 1)
    model = await _active_model(db)
    reason = _unavailable_reason(config, model)
    if reason:
        raise HTTPException(503, reason)
    source_text = record.original_text
    if not source_text.strip() or len(source_text) > MAX_TEXT_CHARS:
        raise HTTPException(422, f"首版事实核查支持 1–{MAX_TEXT_CHARS} 个 Unicode 字符的原文")
    sources = []
    if data.mode == "trusted":
        enabled = {source["id"]: source for source in config.sources if source.get("is_enabled", True)}
        selected_ids = data.source_ids or list(enabled)
        if not selected_ids or any(source_id not in enabled for source_id in selected_ids):
            raise HTTPException(422, "请选择至少一个当前可用的可信信源")
        sources = [enabled[source_id] for source_id in selected_ids]
    if await db.scalar(select(FactCheckRun.id).where(FactCheckRun.user_id == user.id, FactCheckRun.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(409, "已有事实核查任务正在执行，请等待完成或取消")
    midnight = datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0)
    used = await db.scalar(select(func.count()).select_from(FactCheckRun).where(
        FactCheckRun.user_id == user.id, FactCheckRun.created_at >= midnight.astimezone(timezone.utc),
    ))
    if used >= DAILY_LIMIT:
        raise HTTPException(429, f"每日最多提交 {DAILY_LIMIT} 个核查任务（含取消和失败任务），请明日再试")
    run = FactCheckRun(
        record_id=record.id, user_id=user.id, request_id=str(data.request_id), request_hash=request_hash,
        source_hash=hashlib.sha256(source_text.encode()).hexdigest(), source_text=source_text,
        mode=data.mode, sources=sources, config_id=model.id, search_provider=config.search_provider,
        max_claims=config.max_claims, task_id=str(uuid4()),
    )
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "核查请求已提交，请刷新任务列表")
    await db.refresh(run)
    from app.tasks.fact_check_task import async_fact_check

    try:
        await run_in_threadpool(async_fact_check.apply_async, args=[run.id], task_id=run.task_id, retry=False)
    except Exception as exc:
        logger.warning("事实核查投递失败 run={} error={}", run.id, type(exc).__name__)
        await db.execute(update(FactCheckRun).where(FactCheckRun.id == run.id, FactCheckRun.status == "PENDING").values(
            status="FAILURE", error_code="QUEUE_UNAVAILABLE", message="任务队列暂时不可用，请稍后重试",
            finished_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    await db.refresh(run)
    return _response(run)


@router.get("/runs", response_model=list[FactCheckRunResponse], summary="获取记录最近 20 次事实核查")
async def list_runs(record_id: int = Query(gt=0), db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    await load_review_record(db, record_id, user.id)
    await _expire_stale(db, user.id)
    rows = (await db.scalars(select(FactCheckRun).where(
        FactCheckRun.record_id == record_id, FactCheckRun.user_id == user.id,
    ).order_by(FactCheckRun.id.desc()).limit(20))).all()
    return [_response(row) for row in rows]


@router.get("/runs/{run_id}", response_model=FactCheckRunResponse, summary="获取核查进度及证据报告")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return _response(await _owned_run(db, run_id, user.id))


@router.post("/runs/{run_id}/cancel", response_model=FactCheckRunResponse, summary="取消核查并保留已完成的事实项")
async def cancel_run(run_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    run = await _owned_run(db, run_id, user.id)
    await db.execute(update(FactCheckRun).where(FactCheckRun.id == run.id, FactCheckRun.status.in_(ACTIVE)).values(
        status="CANCELLED", error_code="USER_CANCELLED", message="核查已取消；已完成的结果保留，当前外部请求可能仍在结束中",
        finished_at=datetime.now(timezone.utc),
    ))
    await db.flush()
    await db.refresh(run)
    return _response(run)


@admin_router.get("/settings", response_model=FactCheckSettingsResponse, summary="获取事实核查配置（不返回密钥）")
async def get_settings(db: AsyncSession = Depends(get_db), _user=Depends(require_permission("admin:settings:edit"))):
    return _settings_response(await db.get(FactCheckConfig, 1), await _active_model(db))


@admin_router.put("/settings", response_model=FactCheckSettingsResponse, summary="配置检索服务及可信信源（不自动回退）")
async def update_settings(data: FactCheckSettingsUpdate, db: AsyncSession = Depends(get_db), _user=Depends(require_permission("admin:settings:edit"))):
    config = await db.get(FactCheckConfig, 1)
    model = await _active_model(db)
    key = data.api_key.get_secret_value().strip() if data.api_key else ""
    if data.provider == "model":
        if key:
            raise HTTPException(422, "模型原生联网复用当前大模型配置，不能在此填写搜索密钥；如需 Tavily，请先选择 Tavily")
        if data.enabled and (reason := _native_unavailable_reason(model)):
            raise HTTPException(422, reason)
    elif data.enabled and not (key or (config and config.api_key.strip())):
        raise HTTPException(422, "启用 Tavily 事实核查前请先填写 Tavily 搜索密钥；不会自动切换至模型原生联网")
    if config is None:
        config = FactCheckConfig(id=1, sources=[])
        db.add(config)
    config.enabled = data.enabled
    config.search_provider = data.provider
    config.max_claims = data.max_claims
    config.sources = [source.model_dump() for source in data.sources]
    if data.provider == "tavily" and key:
        config.api_key = encrypt_secret(key)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "配置已被其他管理员创建，请刷新后重试")
    return _settings_response(config, model)
