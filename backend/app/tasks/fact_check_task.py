import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.core.secret_crypto import decrypt_secret
from app.models.fact_check import FactCheckConfig, FactCheckReview, FactCheckRun
from app.models.llm_config import LLMConfig
from app.schemas.fact_check import FactCheckReport
from app.services.fact_check_search import model_search_unavailable_reason
from app.tasks import proofread_task


@celery_app.task(name="fact_check.clean_expired")
def clean_expired_fact_checks():
    from app.core.config import settings

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.FACT_CHECK_RETENTION_DAYS)
    with Session(proofread_task._get_sync_engine()) as db:
        ids = list(db.scalars(select(FactCheckRun.id).where(
            FactCheckRun.created_at < cutoff, FactCheckRun.source_text != "",
            FactCheckRun.status.in_(("SUCCESS", "FAILURE", "CANCELLED", "WAITING_CONFIRMATION")),
        ).order_by(FactCheckRun.id).limit(1000).with_for_update(skip_locked=True)))
        if not ids:
            return 0
        db.execute(delete(FactCheckReview).where(FactCheckReview.run_id.in_(ids)))
        db.execute(update(FactCheckRun).where(FactCheckRun.id.in_(ids)).values(
            source_text="", result_json=None, title="已过保留期限的核查记录", status="CANCELLED", stage="complete",
            message="原文、证据与复核已到期清理；请求计数保留", error_code="MATERIAL_EXPIRED",
            record_id=None, file_id=None, supplemental_urls=[], finished_at=datetime.now(timezone.utc),
        ))
        db.commit()
        return len(ids)


class FactCheckCancelled(Exception):
    pass


def _validated_report(report: dict, text: str) -> dict:
    parsed = FactCheckReport.model_validate(report)
    if any(text[claim.start:claim.end] != claim.original for claim in parsed.claims):
        raise ValueError("核查事实项与原文快照不匹配")
    return parsed.model_dump(mode="json")


def _finish(run_id: int, *, status: str, message: str, error_code: str | None = None, result: dict | None = None):
    with Session(proofread_task._get_sync_engine()) as db:
        values = dict(status=status, message=message, error_code=error_code,
                      stage="check" if status == "WAITING_CONFIRMATION" else "complete",
                      finished_at=None if status == "WAITING_CONFIRMATION" else datetime.now(timezone.utc))
        if result is not None:
            values["result_json"] = result
        if status == "SUCCESS":
            values["progress"] = 100
        db.execute(update(FactCheckRun).where(FactCheckRun.id == run_id, FactCheckRun.status == "RUNNING").values(**values))
        db.commit()


@celery_app.task(name="fact_check.run", soft_time_limit=270, time_limit=300)
def async_fact_check(run_id: int):
    with Session(proofread_task._get_sync_engine()) as db:
        claimed = db.execute(update(FactCheckRun).where(
            FactCheckRun.id == run_id, FactCheckRun.status == "PENDING",
        ).values(status="RUNNING", progress=1, message="正在提取可核查事实", started_at=datetime.now(timezone.utc)))
        db.commit()
        if claimed.rowcount != 1:
            return
        run = db.get(FactCheckRun, run_id)
        source_text, mode, sources = run.source_text, run.mode, run.sources
        config_id, max_claims = run.config_id, run.max_claims
        search_provider = run.search_provider
        extraction_only = run.confirm_claims and run.stage == "extract"
        prepared_report = run.result_json if run.stage == "check" else None
        selected_claim_ids, depth = run.selected_claim_ids, run.depth
        supplemental_urls, config_snapshot = run.supplemental_urls, run.config_snapshot

    async def on_progress(percent: int, message: str, report: dict | None = None):
        result = _validated_report(report, source_text) if report is not None else None
        with Session(proofread_task._get_sync_engine()) as db:
            status = db.scalar(select(FactCheckRun.status).where(FactCheckRun.id == run_id))
            if status != "RUNNING":
                raise FactCheckCancelled()
            values = dict(progress=max(1, min(99, percent)), message=message[:500])
            if result is not None:
                values["result_json"] = result
            updated = db.execute(update(FactCheckRun).where(
                FactCheckRun.id == run_id, FactCheckRun.status == "RUNNING",
            ).values(**values))
            db.commit()
            if not updated.rowcount:
                raise FactCheckCancelled()

    async def execute():
        from app.services.fact_check import FactCheckError, run_fact_check
        from app.services.proofread import get_llm_provider

        await on_progress(1, "正在准备检索服务")
        api_key = ""
        with Session(proofread_task._get_sync_engine()) as db:
            config = db.get(FactCheckConfig, 1)
            if not config or not config.enabled:
                raise FactCheckError("FACT_CHECK_DISABLED", "事实核查配置已停用，请联系管理员")
            # Global settings may disable runs or rotate keys, but cannot change their selected service.
            if search_provider == "tavily":
                if not config.api_key.strip():
                    raise FactCheckError("TAVILY_API_KEY_MISSING", "Tavily 搜索密钥缺失，请联系管理员；不会自动切换至模型原生联网")
                api_key = decrypt_secret(config.api_key)
                if not api_key.strip():
                    raise FactCheckError("TAVILY_API_KEY_MISSING", "Tavily 搜索密钥为空，请联系管理员；不会自动切换至模型原生联网")
            elif search_provider == "model":
                model = db.get(LLMConfig, config_id)
                if not model or not model.is_enabled:
                    raise FactCheckError("MODEL_CONFIG_UNAVAILABLE", "任务使用的大模型配置不存在或已停用；不会自动切换模型或检索服务")
                reason = model_search_unavailable_reason(model.provider, model.api_base, model.model)
                if reason:
                    raise FactCheckError("MODEL_SEARCH_UNSUPPORTED", f"{reason}；不会自动切换至 Tavily")
                if not model.api_key.strip():
                    raise FactCheckError("MODEL_API_KEY_MISSING", "任务使用的大模型 API 密钥缺失，请联系管理员；不会自动切换至 Tavily")
            else:
                raise FactCheckError("SEARCH_PROVIDER_UNSUPPORTED", "任务检索服务无效，请重新发起核查；不会自动切换检索服务")
            model = db.get(LLMConfig, config_id)
            if not model or not model.is_enabled or not model.api_key.strip():
                raise FactCheckError("MODEL_CONFIG_UNAVAILABLE", "任务模型不存在、已停用或缺少密钥；不会自动切换模型或检索服务")
            if config_snapshot and any(getattr(model, key) != config_snapshot.get(key) for key in ("provider", "model", "api_base", "timeout", "max_retries")):
                raise FactCheckError("MODEL_CONFIG_CHANGED", "任务排队期间模型配置已改变，请新建核查，避免混用不同配置")
        provider = await get_llm_provider(config_id)
        provider.usage_business = "fact_check"
        try:
            return await run_fact_check(
                source_text, mode=mode, sources=sources, api_key=api_key,
                search_provider=search_provider, provider=provider, max_claims=max_claims, on_progress=on_progress,
                extraction_only=extraction_only, prepared_report=prepared_report, selected_claim_ids=selected_claim_ids,
                depth=depth, supplemental_urls=supplemental_urls,
            )
        finally:
            await provider.close()

    async def bounded_execute():
        return await asyncio.wait_for(execute(), timeout=240)

    try:
        result = _validated_report(proofread_task._run_async(bounded_execute()), source_text)
        waiting = extraction_only and bool(result["claims"])
        if not result["claims"] and result["coverage"]["status"] == "partial":
            _finish(run_id, status="FAILURE", error_code="EXTRACTION_LOCATION_FAILED",
                    message="事实提取未完成，未取得有效事实；未执行搜索，请重新发起核查。", result=result)
            return
        message = "请选择并确认需要核查的事实" if waiting else "核查完成，请结合证据人工确认"
        if not result["claims"]:
            message = "未识别到可核查事实，未执行搜索；不代表全文事实正确。"
        elif not waiting and result["coverage"]["status"] == "partial":
            message = "核查不完整，请查看保留的事实项及未完成原因。"
        _finish(run_id, status="WAITING_CONFIRMATION" if waiting else "SUCCESS", message=message, result=result)
    except FactCheckCancelled:
        return
    except TimeoutError:
        _finish(run_id, status="FAILURE", error_code="TIMEOUT", message="核查超过时间预算，已完成的事实项保留")
    except Exception as exc:
        from app.services.fact_check import FactCheckError

        code, message = "FACT_CHECK_FAILED", "事实核查失败，已完成的事实项保留；请稍后重试或联系管理员"
        if isinstance(exc, FactCheckError):
            code, message = exc.code, str(exc)
        logger.warning("事实核查失败 run={} code={} error={}", run_id, code, type(exc).__name__)
        _finish(run_id, status="FAILURE", error_code=code[:64], message=message[:500])
