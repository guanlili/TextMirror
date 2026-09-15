import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.core.secret_crypto import decrypt_secret
from app.models.fact_check import FactCheckConfig, FactCheckRun
from app.models.llm_config import LLMConfig
from app.schemas.fact_check import FactCheckReport
from app.services.fact_check_search import model_search_unavailable_reason
from app.tasks import proofread_task


class FactCheckCancelled(Exception):
    pass


def _validated_report(report: dict, text: str) -> dict:
    parsed = FactCheckReport.model_validate(report)
    if any(text[claim.start:claim.end] != claim.original for claim in parsed.claims):
        raise ValueError("核查事实项与原文快照不匹配")
    return parsed.model_dump(mode="json")


def _finish(run_id: int, *, status: str, message: str, error_code: str | None = None, result: dict | None = None):
    with Session(proofread_task._get_sync_engine()) as db:
        values = dict(status=status, message=message, error_code=error_code, finished_at=datetime.now(timezone.utc))
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
        provider = await get_llm_provider(config_id)
        provider.usage_business = "fact_check"
        try:
            return await run_fact_check(
                source_text, mode=mode, sources=sources, api_key=api_key,
                search_provider=search_provider, provider=provider, max_claims=max_claims, on_progress=on_progress,
            )
        finally:
            await provider.close()

    async def bounded_execute():
        return await asyncio.wait_for(execute(), timeout=240)

    try:
        result = proofread_task._run_async(bounded_execute())
        _finish(run_id, status="SUCCESS", message="核查完成，请结合证据人工确认", result=_validated_report(result, source_text))
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
