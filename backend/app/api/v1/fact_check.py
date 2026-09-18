import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from loguru import logger
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.core.secret_crypto import encrypt_secret
from app.models.fact_check import FactCheckConfig, FactCheckReview, FactCheckRun
from app.models.llm_config import LLMConfig
from app.models.uploaded_document import UploadedDocument
from app.models.user import User
from app.schemas.fact_check import (
    DAILY_LIMIT,
    MAX_TEXT_CHARS,
    FactCheckCreate,
    FactCheckDeepen,
    FactCheckExecute,
    FactCheckHistory,
    FactCheckOptions,
    FactCheckReport,
    FactCheckRunResponse,
    FactCheckSettingsResponse,
    FactCheckSettingsUpdate,
    FactCheckStatus,
    FactReviewCreate,
    FactReviewResponse,
    FactSearchRound,
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
TERMINAL = ("SUCCESS", "FAILURE", "CANCELLED")


async def _active_model(db: AsyncSession, config_id: int | None = None) -> LLMConfig | None:
    query = select(LLMConfig).where(LLMConfig.is_enabled.is_(True))
    return await db.scalar(query.where(LLMConfig.id == config_id) if config_id else query.where(LLMConfig.is_active.is_(True)))


async def _configuration(db):
    config = await db.get(FactCheckConfig, 1)
    return config, await _active_model(db, config.model_config_id if config else None)


def _model_name(model):
    return f"{model.name} ({model.model})" if model else ""


def _model_search_reason(model):
    if model is None:
        return "尚未配置可用的大模型，请联系管理员"
    return model_search_unavailable_reason(model.provider, model.api_base, model.model)


def _native_unavailable_reason(model):
    reason = _model_search_reason(model)
    if reason:
        return f"{reason}；不会自动切换至 Tavily"
    if not model.api_key.strip():
        return "尚未配置当前大模型的 API 密钥，请联系管理员；不会自动切换至 Tavily"
    return ""


def _unavailable_reason(config, model):
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


def _settings_response(config, model):
    reason = _model_search_reason(model)
    return FactCheckSettingsResponse(
        enabled=bool(config and config.enabled), model_config_id=config.model_config_id if config else None,
        provider=config.search_provider if config else "model", api_key_configured=bool(config and config.api_key.strip()),
        model_name=_model_name(model), model_search_supported=not reason, model_search_reason=reason,
        max_claims=config.max_claims if config else 10, sources=config.sources if config else [],
    )


def _response(run, *, summary=False):
    return FactCheckRunResponse(
        id=run.id, record_id=run.record_id, title=run.title, source_kind=run.source_kind, file_id=run.file_id,
        parent_run_id=run.parent_run_id, confirm_claims=run.confirm_claims, stage=run.stage, depth=run.depth,
        max_claims=run.max_claims, mode=run.mode, provider=run.search_provider, status=run.status,
        progress=run.progress, message=run.message, error_code=run.error_code,
        result=None if summary else run.result_json, source_hash=run.source_hash,
        source_ids=[source["id"] for source in run.sources], created_at=run.created_at, finished_at=run.finished_at,
    )


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _model_snapshot(model):
    from app.services.fact_check import EXTRACTION_PROMPT, JUDGMENT_PROMPT

    return {key: getattr(model, key) for key in ("name", "provider", "model", "api_base", "timeout", "max_retries")} | {
        "engine_version": "fact-check-workbench-v1", "prompt_sha256": _hash([EXTRACTION_PROMPT, JUDGMENT_PROMPT]),
    }


async def _expire_stale(db, user_id):
    now = datetime.now(timezone.utc)
    await db.execute(update(FactCheckRun).where(
        FactCheckRun.user_id == user_id,
        ((FactCheckRun.status == "PENDING") & (func.coalesce(FactCheckRun.queued_at, FactCheckRun.created_at) < now - timedelta(minutes=15)))
        | ((FactCheckRun.status == "RUNNING") & (FactCheckRun.started_at < now - timedelta(minutes=6))),
    ).values(status="FAILURE", stage="complete", error_code="TASK_EXPIRED", message="任务未能按时完成，可重新发起核查", finished_at=now))


async def _owned_run(db, run_id, user, permission="fact-check:run"):
    await _expire_stale(db, user.id)
    run = await db.scalar(select(FactCheckRun).where(FactCheckRun.id == run_id, FactCheckRun.user_id == user.id))
    if run is None:
        raise HTTPException(404, "核查任务不存在")
    await require_permission(permission)(current_user=user, db=db)
    return run


async def _lock_user(db, user_id):
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    await _expire_stale(db, user_id)


async def _budget(db, user_id, *, new_run=True):
    if await db.scalar(select(FactCheckRun.id).where(FactCheckRun.user_id == user_id, FactCheckRun.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(409, "已有事实核查任务正在执行，请等待完成或取消")
    if new_run:
        midnight = datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0)
        used = await db.scalar(select(func.count()).select_from(FactCheckRun).where(
            FactCheckRun.user_id == user_id, FactCheckRun.created_at >= midnight.astimezone(timezone.utc),
        ))
        if used >= DAILY_LIMIT:
            raise HTTPException(429, f"每日最多提交 {DAILY_LIMIT} 个核查任务（含提取、取消和失败任务），请明日再试")


async def _duplicate(db, user_id, request_id, request_hash):
    existing = await db.scalar(select(FactCheckRun).where(FactCheckRun.user_id == user_id, FactCheckRun.request_id == str(request_id)))
    if existing and existing.request_hash != request_hash:
        raise HTTPException(409, "此请求 ID 已用于不同的核查参数，请生成新的请求 ID")
    return existing


async def _dispatch(db, run):
    from app.tasks.fact_check_task import async_fact_check

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "核查请求已提交，请刷新任务列表") from None
    await db.refresh(run)
    try:
        await run_in_threadpool(async_fact_check.apply_async, args=[run.id], task_id=run.task_id, retry=False)
    except Exception as exc:
        logger.warning("事实核查投递失败 run={} error={}", run.id, type(exc).__name__)
        await db.execute(update(FactCheckRun).where(FactCheckRun.id == run.id, FactCheckRun.status == "PENDING").values(
            status="FAILURE", stage="complete", error_code="QUEUE_UNAVAILABLE", message="任务队列暂时不可用，请稍后重试",
            finished_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    await db.refresh(run)
    return _response(run)


@router.get("/options", response_model=FactCheckOptions, summary="获取核查能力与可用可信信源")
async def options(db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    config, model = await _configuration(db)
    reason = _unavailable_reason(config, model)
    return FactCheckOptions(
        available=not reason, unavailable_reason=reason, provider=config.search_provider if config else "model",
        model_name=_model_name(model), max_claims=config.max_claims if config else 10, retention_days=settings.FACT_CHECK_RETENTION_DAYS,
        sources=[source for source in config.sources if source.get("is_enabled", True)] if config else [],
    )


@router.post("/runs", response_model=FactCheckRunResponse, status_code=202, summary="独立核查文本、文档或审校记录")
async def create_run(data: FactCheckCreate, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    source_text, source_kind, title = data.text, "text", data.title.strip()
    if data.record_id:
        record = await load_review_record(db, data.record_id, user.id)
        await require_permission(f"proofread:{record.type}")(current_user=user, db=db)
        source_text, source_kind = record.original_text, "record"
    elif data.file_id:
        doc = await db.scalar(select(UploadedDocument).where(
            UploadedDocument.file_id == data.file_id, UploadedDocument.user_id == user.id,
            UploadedDocument.owner_kind == "user", UploadedDocument.deleted_at.is_(None), UploadedDocument.status != "deleted",
        ))
        if doc is None:
            raise HTTPException(404, "上传文档不存在")
        source_text, source_kind, title = doc.extracted_text, "document", title or doc.filename[:200]
    await require_permission("fact-check:run")(current_user=user, db=db)
    if not source_text or not source_text.strip() or len(source_text) > MAX_TEXT_CHARS:
        raise HTTPException(422, f"事实核查支持 1–{MAX_TEXT_CHARS} 个 Unicode 字符，不会截断后提交")
    request_hash = _hash(data.model_dump(mode="json", exclude={"request_id"}) | {"source_ids": sorted(data.source_ids)})
    await _lock_user(db, user.id)
    existing = await _duplicate(db, user.id, data.request_id, request_hash)
    if existing:
        return _response(existing)
    config, model = await _configuration(db)
    if reason := _unavailable_reason(config, model):
        raise HTTPException(503, reason)
    sources = []
    if data.mode == "trusted":
        enabled = {source["id"]: source for source in config.sources if source.get("is_enabled", True)}
        selected_ids = data.source_ids or list(enabled)
        if not selected_ids or any(source_id not in enabled for source_id in selected_ids):
            raise HTTPException(422, "请选择至少一个当前可用的可信信源")
        sources = [enabled[source_id] for source_id in selected_ids]
    await _budget(db, user.id)
    run = FactCheckRun(
        record_id=data.record_id, file_id=data.file_id, source_kind=source_kind, title=title or source_text.strip()[:60],
        user_id=user.id, request_id=str(data.request_id), request_hash=request_hash,
        source_hash=hashlib.sha256(source_text.encode()).hexdigest(), source_text=source_text,
        mode=data.mode, sources=sources, config_id=model.id, config_snapshot=_model_snapshot(model),
        search_provider=config.search_provider, max_claims=config.max_claims, task_id=str(uuid4()),
        confirm_claims=data.confirm_claims, queued_at=datetime.now(timezone.utc),
    )
    db.add(run)
    return await _dispatch(db, run)


@router.get("/runs", response_model=list[FactCheckRunResponse], summary="获取来源记录的核查历史")
async def list_runs(record_id: int = Query(gt=0), db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    await load_review_record(db, record_id, user.id)
    await require_permission("fact-check:run")(current_user=user, db=db)
    await _expire_stale(db, user.id)
    rows = (await db.scalars(select(FactCheckRun).where(
        FactCheckRun.record_id == record_id, FactCheckRun.user_id == user.id,
    ).order_by(FactCheckRun.id.desc()).limit(20))).all()
    return [_response(row) for row in rows]


@router.get("/history", response_model=FactCheckHistory, summary="分页查看独立核查任务")
async def history(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
                  status: FactCheckStatus | None = None, q: str = Query("", max_length=200),
                  db: AsyncSession = Depends(get_db), user=Depends(require_permission("fact-check:run"))):
    await _expire_stale(db, user.id)
    conditions = [FactCheckRun.user_id == user.id]
    if status:
        conditions.append(FactCheckRun.status == status)
    if q.strip():
        conditions.append(FactCheckRun.title.contains(q.strip(), autoescape=True))
    total = await db.scalar(select(func.count()).select_from(FactCheckRun).where(*conditions))
    rows = (await db.scalars(select(FactCheckRun).options(defer(FactCheckRun.result_json), defer(FactCheckRun.source_text)).where(
        *conditions).order_by(FactCheckRun.id.desc()).offset(offset).limit(limit))).all()
    return FactCheckHistory(items=[_response(row, summary=True) for row in rows], total=total)


@router.get("/runs/{run_id}", response_model=FactCheckRunResponse, summary="获取核查进度及证据报告")
async def get_run(run_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return _response(await _owned_run(db, run_id, user))


@router.get("/runs/{run_id}/source", summary="读取不可变原文快照")
async def get_source(run_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    run = await _owned_run(db, run_id, user)
    return {"text": run.source_text, "source_hash": run.source_hash}


@router.post("/runs/{run_id}/execute", response_model=FactCheckRunResponse, status_code=202)
async def execute_run(run_id: int, data: FactCheckExecute, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    await _lock_user(db, user.id)
    run = await _owned_run(db, run_id, user)
    request_hash = _hash(data.model_dump(mode="json", exclude={"request_id"}))
    if run.execute_request_id == str(data.request_id):
        if run.execute_request_hash != request_hash:
            raise HTTPException(409, "此确认请求 ID 的内容已改变")
        return _response(run)
    if run.status != "WAITING_CONFIRMATION" or not run.result_json:
        raise HTTPException(409, "任务不处于等待确认状态")
    selected = {item.id: item.statement for item in data.claims}
    known = {item["id"] for item in run.result_json["claims"]}
    if len(selected) != len(data.claims) or not set(selected) <= known or len(selected) > run.max_claims:
        raise HTTPException(422, "事实选择重复、超出范围或超过核查预算")
    await _budget(db, user.id, new_run=False)
    report = copy.deepcopy(run.result_json)
    for claim in report["claims"]:
        claim["selected"] = claim["id"] in selected
        if claim["selected"]:
            claim["original_statement"] = claim.get("original_statement") or claim["statement"]
            claim["statement"] = selected[claim["id"]]
        query = " ".join(claim["statement"].split())[:350]
        claim.update(checked=False, verdict="insufficient", evidence=[], suggestion=None, reason="等待重新检索与核查。",
                     search_rounds=[FactSearchRound(kind=kind, query=value, status="pending", sources=[]).model_dump()
                                    for kind, value in (("initial", query), ("counter", query + " 更正 修订 原始公告"))])
    report["coverage"].update(checked=0, unverified=len(report["claims"]), status="partial")
    report = FactCheckReport.model_validate(report).model_dump(mode="json")
    updated = await db.execute(update(FactCheckRun).where(FactCheckRun.id == run.id, FactCheckRun.status == "WAITING_CONFIRMATION").values(
        result_json=report, selected_claim_ids=list(selected), stage="check", status="PENDING", progress=0,
        message="事实已确认，等待核查", queued_at=datetime.now(timezone.utc), started_at=None, finished_at=None,
        execute_request_id=str(data.request_id), execute_request_hash=request_hash, task_id=str(uuid4()),
    ))
    if not updated.rowcount:
        raise HTTPException(409, "任务状态已改变，请刷新")
    await db.refresh(run)
    return await _dispatch(db, run)


@router.post("/runs/{run_id}/deepen", response_model=FactCheckRunResponse, status_code=202)
async def deepen_run(run_id: int, data: FactCheckDeepen, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    await _lock_user(db, user.id)
    parent = await _owned_run(db, run_id, user)
    request_hash = _hash(data.model_dump(mode="json", exclude={"request_id"}) | {"parent_run_id": run_id})
    existing = await _duplicate(db, user.id, data.request_id, request_hash)
    if existing:
        return _response(existing)
    if parent.status not in TERMINAL:
        raise HTTPException(409, "请等待本次核查结束后再深入核查")
    claim = next((item for item in (parent.result_json or {}).get("claims", []) if item["id"] == data.claim_id), None)
    if claim is None:
        raise HTTPException(422, "事实项不存在")
    from app.services.fact_check import FactCheckError, _validate_url
    try:
        for url in data.supplemental_urls:
            _validate_url(url, parent.sources if parent.mode == "trusted" else None)
    except FactCheckError as exc:
        raise HTTPException(422, exc.message) from None
    config, model = await _configuration(db)
    if reason := _unavailable_reason(config, model):
        raise HTTPException(503, reason)
    await _budget(db, user.id)
    selected = copy.deepcopy(claim)
    selected.update(checked=False, selected=True, verdict="insufficient", evidence=[], search_rounds=[], suggestion=None, reason="等待单条深入核查")
    report = {"claims": [selected], "coverage": {"extracted": 1, "checked": 0, "unverified": 1, "status": "partial", "reason": "仅核查选中的一条事实，其他结论未重新核查"},
              "usage": {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens", "search_queries", "pages_fetched")},
              "checked_at": datetime.now(timezone.utc).isoformat()}
    run = FactCheckRun(
        user_id=user.id, record_id=parent.record_id, file_id=parent.file_id, source_kind=parent.source_kind,
        parent_run_id=parent.id, title=(parent.title[:180] + " · 单条深查"), source_text=parent.source_text, source_hash=parent.source_hash,
        request_id=str(data.request_id), request_hash=request_hash, mode=parent.mode, sources=copy.deepcopy(parent.sources),
        config_id=model.id, config_snapshot=_model_snapshot(model), search_provider=config.search_provider, max_claims=1,
        depth="deep", stage="check", selected_claim_ids=[selected["id"]], supplemental_urls=data.supplemental_urls,
        result_json=FactCheckReport.model_validate(report).model_dump(mode="json"), task_id=str(uuid4()), queued_at=datetime.now(timezone.utc),
    )
    db.add(run)
    return await _dispatch(db, run)


@router.post("/runs/{run_id}/cancel", response_model=FactCheckRunResponse, summary="取消核查并保留已完成事实项")
async def cancel_run(run_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    run = await _owned_run(db, run_id, user)
    await db.execute(update(FactCheckRun).where(FactCheckRun.id == run.id, FactCheckRun.status.in_((*ACTIVE, "WAITING_CONFIRMATION"))).values(
        status="CANCELLED", stage="complete", error_code="USER_CANCELLED", message="核查已取消；已完成的结果保留，当前外部请求可能仍在结束中",
        finished_at=datetime.now(timezone.utc),
    ))
    await db.flush()
    await db.refresh(run)
    return _response(run)


@router.get("/runs/{run_id}/reviews", response_model=list[FactReviewResponse])
async def list_reviews(run_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    await _owned_run(db, run_id, user)
    return (await db.scalars(select(FactCheckReview).where(FactCheckReview.run_id == run_id).order_by(FactCheckReview.id))).all()


@router.post("/runs/{run_id}/reviews", response_model=FactReviewResponse, status_code=201)
async def add_review(run_id: int, data: FactReviewCreate, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    await _lock_user(db, user.id)
    run = await _owned_run(db, run_id, user, "fact-check:review")
    existing = await db.scalar(select(FactCheckReview).where(FactCheckReview.run_id == run_id, FactCheckReview.request_id == str(data.request_id)))
    if existing:
        if any(getattr(existing, key) != getattr(data, key) for key in ("claim_id", "decision", "note")):
            raise HTTPException(409, "复核请求 ID 已用于不同内容")
        return existing
    claim = next((item for item in (run.result_json or {}).get("claims", []) if item["id"] == data.claim_id), None)
    if run.status not in TERMINAL or not claim or not claim["checked"]:
        raise HTTPException(409, "仅可复核已结束任务中已检查的事实项")
    review = FactCheckReview(run_id=run_id, user_id=user.id, **data.model_dump(exclude={"request_id"}), request_id=str(data.request_id))
    db.add(review)
    await db.flush()
    await db.refresh(review)
    return review


@router.get("/runs/{run_id}/export", summary="下载核查快照与人工复核报告")
async def export_run(run_id: int, format: str = Query("json", pattern="^(json|html)$"),
                     db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    run = await _owned_run(db, run_id, user, "fact-check:export")
    if run.status not in TERMINAL:
        raise HTTPException(409, "请等待任务结束后导出报告")
    reviews = (await db.scalars(select(FactCheckReview).where(FactCheckReview.run_id == run_id).order_by(FactCheckReview.id))).all()
    data = _response(run).model_dump(mode="json") | {"source_text": run.source_text,
        "reviews": [FactReviewResponse.model_validate(item).model_dump(mode="json") for item in reviews],
        "model": {key: value for key, value in run.config_snapshot.items() if key in {"name", "provider", "model"}},
        "limitations": "覆盖仅针对已提取陈述；已检查不代表已证实。语义判断来自模型，程序校验逐字引文与结构约束。人工意见不覆盖机器结论。"}
    headers = {"Content-Disposition": f'attachment; filename="fact-check-{run_id}.{format}"', "Cache-Control": "no-store"}
    if format == "json":
        return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json", headers=headers)
    from app.services.fact_check_report import render_report
    return Response(render_report(data), media_type="text/html", headers=headers | {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'"})


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(run_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    await _lock_user(db, user.id)
    run = await _owned_run(db, run_id, user)
    if run.status in ACTIVE:
        raise HTTPException(409, "请先取消正在执行的核查")
    # Preserve the request ledger so deletion cannot reset daily limits or replay paid calls.
    await db.execute(delete(FactCheckReview).where(FactCheckReview.run_id == run_id))
    await db.execute(update(FactCheckRun).where(FactCheckRun.id == run_id, FactCheckRun.status.not_in(ACTIVE)).values(
        source_text="", result_json=None, title="已清理的核查记录", message="原文、证据和复核记录已清理；保留请求计数",
        status="CANCELLED", stage="complete", record_id=None, file_id=None, supplemental_urls=[],
    ))
    return Response(status_code=204)


@admin_router.get("/settings", response_model=FactCheckSettingsResponse, summary="获取事实核查配置（不返回密钥）")
async def get_settings(db: AsyncSession = Depends(get_db), _user=Depends(require_permission("admin:settings:edit"))):
    return _settings_response(*(await _configuration(db)))


@admin_router.put("/settings", response_model=FactCheckSettingsResponse, summary="配置检索服务及可信信源（不自动回退）")
async def update_settings(data: FactCheckSettingsUpdate, db: AsyncSession = Depends(get_db), _user=Depends(require_permission("admin:settings:edit"))):
    config = await db.get(FactCheckConfig, 1)
    model = await _active_model(db, data.model_config_id)
    if data.model_config_id and model is None:
        raise HTTPException(422, "所选模型不存在或已停用")
    key = data.api_key.get_secret_value().strip() if data.api_key else ""
    if data.provider == "model":
        if key:
            raise HTTPException(422, "模型原生联网复用已有大模型配置，不能在此填写搜索密钥；如需 Tavily，请先选择 Tavily")
        if data.enabled and (reason := _native_unavailable_reason(model)):
            raise HTTPException(422, reason)
    elif data.enabled and not (key or (config and config.api_key.strip())):
        raise HTTPException(422, "启用 Tavily 事实核查前请先填写 Tavily 搜索密钥；不会自动切换至模型原生联网")
    if data.enabled and (model is None or not model.api_key.strip()):
        raise HTTPException(422, "尚未配置可用的大模型或 API 密钥")
    if config is None:
        config = FactCheckConfig(id=1, sources=[])
        db.add(config)
    config.enabled, config.model_config_id = data.enabled, data.model_config_id
    config.search_provider, config.max_claims = data.provider, data.max_claims
    config.sources = [source.model_dump() for source in data.sources]
    if data.provider == "tavily" and key:
        config.api_key = encrypt_secret(key)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "配置已被其他管理员创建，请刷新后重试") from None
    return _settings_response(config, model)
