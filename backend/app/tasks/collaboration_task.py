import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.models.proofread import ProofreadRecord
from app.models.proofread_task import ProofreadTask
from app.models.uploaded_document import UploadedDocument  # noqa: F401 — registers ProofreadRecord's foreign key target
from app.schemas.collaboration import CollaborationReport, CollaborationResult
from app.tasks import proofread_task

ACTIVE = ("PENDING", "STARTED", "PROGRESS")
REFUND_ONCE_LUA = (
    "if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end "
    "local c = tonumber(redis.call('GET', KEYS[1]) or '0') "
    "if c > 0 then redis.call('DECRBY', KEYS[1], 1) end "
    "redis.call('SET', KEYS[2], '1', 'EX', 172800) return 1"
)


class CollaborationCancelled(Exception):
    pass


def refund_collaboration_quota(task_id: str, quota_key: str | None) -> None:
    if not quota_key:
        return
    try:
        with proofread_task._get_sync_redis() as redis:
            redis.eval(REFUND_ONCE_LUA, 2, quota_key, f"textmirror:collaboration_refund:{task_id}")
    except Exception as exc:
        logger.warning("协作额度退还失败 task={} error={}", task_id, type(exc).__name__)


def terminal_report(report: dict, status: str, message: str) -> dict:
    parsed = CollaborationReport.model_validate(report)
    parsed.status = "partial"
    for role in parsed.roles:
        if role.status in ("pending", "running"):
            role.status = "cancelled" if status == "CANCELLED" else "failed"
            role.message = message
    return parsed.model_dump(mode="json")


def finish_failure(db_task_id: int, code: str, message: str, *, expired_only: bool = False) -> None:
    with Session(proofread_task._get_sync_engine()) as db:
        task = db.scalar(select(ProofreadTask).where(ProofreadTask.id == db_task_id).with_for_update())
        if task is None:
            return
        if expired_only and task.status in ACTIVE:
            since = task.started_at or task.created_at
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            budget = 15 if task.status == "PENDING" else 6
            if datetime.now(timezone.utc) - since <= timedelta(minutes=budget):
                return
        if task.status in ACTIVE:
            status = "CANCELLED" if task.cancel_requested else "FAILURE"
            if status == "CANCELLED":
                code, message = "USER_CANCELLED", "协作审校已取消；当前外部请求可能仍在结束中"
            result = dict(task.result_json or {})
            if result.get("collaboration"):
                result["collaboration"] = terminal_report(result["collaboration"], status, message)
            updated = db.execute(update(ProofreadTask).where(
                ProofreadTask.id == task.id, ProofreadTask.status.in_(ACTIVE),
            ).values(status=status, error_code=code, message=message, result_json=result,
                     finished_at=datetime.now(timezone.utc)))
            db.commit()
            if not updated.rowcount:
                return
        if task.status in ("FAILURE", "CANCELLED"):
            refund_collaboration_quota(task.task_id, (task.params_json or {}).get("quota_key"))


def expire_collaboration_task(db_task_id: int) -> None:
    with Session(proofread_task._get_sync_engine()) as db:
        task = db.get(ProofreadTask, db_task_id)
        if not task or (task.params_json or {}).get("kind") != "collaboration":
            return
        if task.status in ("FAILURE", "CANCELLED"):
            refund_collaboration_quota(task.task_id, task.params_json.get("quota_key"))
            return
        if task.status not in ACTIVE:
            return
        since = task.started_at or task.created_at
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        budget = 15 if task.status == "PENDING" else 6
        if datetime.now(timezone.utc) - since <= timedelta(minutes=budget):
            return
    finish_failure(db_task_id, "TASK_EXPIRED", "任务未能按时完成，请重新发起协作审校", expired_only=True)


@celery_app.task(name="proofread.collaborate", soft_time_limit=270, time_limit=300)
def async_collaboration(db_task_id: int):
    with Session(proofread_task._get_sync_engine()) as db:
        claimed = db.execute(update(ProofreadTask).where(
            ProofreadTask.id == db_task_id, ProofreadTask.status == "PENDING",
            ProofreadTask.cancel_requested.is_(False),
        ).values(status="STARTED", started_at=datetime.now(timezone.utc), message="正在准备协作审校"))
        db.commit()
        if claimed.rowcount != 1:
            return
        task = db.get(ProofreadTask, db_task_id)
        params, user_id = dict(task.params_json), task.owner_user_id

    async def on_progress(event: dict):
        report = CollaborationReport.model_validate(event["collaboration"]).model_dump(mode="json")
        with Session(proofread_task._get_sync_engine()) as db:
            updated = db.execute(update(ProofreadTask).where(
                ProofreadTask.id == db_task_id, ProofreadTask.status.in_(("STARTED", "PROGRESS")),
                ProofreadTask.cancel_requested.is_(False),
            ).values(status="PROGRESS", progress=min(99, max(1, event["progress"])),
                     phase="collaboration", message=event["message"][:500], result_json={"collaboration": report}))
            db.commit()
            if updated.rowcount != 1:
                raise CollaborationCancelled()

    async def monitor_cancel():
        while True:
            await asyncio.sleep(1)
            with Session(proofread_task._get_sync_engine()) as db:
                current = db.get(ProofreadTask, db_task_id)
                if current is None or current.cancel_requested or current.status not in ACTIVE:
                    raise CollaborationCancelled()

    async def execute():
        from app.core import redis as redis_module
        from app.services.collaboration import run_collaboration

        if redis_module.redis_client is None:
            await redis_module.init_redis()
        run = asyncio.create_task(run_collaboration(
            params["text"], domain=params["domain"], config_id=params["config_id"],
            user_id=user_id, on_progress=on_progress,
        ))
        monitor = asyncio.create_task(monitor_cancel())
        try:
            done, _ = await asyncio.wait((run, monitor), return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                await monitor
            return await run
        finally:
            run.cancel()
            monitor.cancel()
            await asyncio.gather(run, monitor, return_exceptions=True)

    async def bounded_execute():
        return await asyncio.wait_for(execute(), timeout=240)

    try:
        raw_result = proofread_task._run_async(bounded_execute())
        result = CollaborationResult.model_validate(raw_result).model_dump(mode="json")
        with Session(proofread_task._get_sync_engine()) as db:
            task = db.scalar(select(ProofreadTask).where(ProofreadTask.id == db_task_id).with_for_update())
            if task is None or task.cancel_requested or task.status not in ACTIVE:
                raise CollaborationCancelled()
            record = ProofreadRecord(
                user_id=user_id, type="text", original_text=params["text"], domain=result["domain"],
                check_types="[]", result=result, total_issues=result["total_issues"],
                token_usage=result["usage"], quota_weight=1,
            )
            db.add(record)
            db.flush()
            result["record_id"] = record.id
            updated = db.execute(update(ProofreadTask).where(
                ProofreadTask.id == db_task_id, ProofreadTask.status.in_(ACTIVE),
                ProofreadTask.cancel_requested.is_(False),
            ).values(status="SUCCESS", progress=100, phase="complete", result_json=result,
                     message="协作审校结束，请人工确认建议", finished_at=datetime.now(timezone.utc)))
            if updated.rowcount != 1:
                db.rollback()
                raise CollaborationCancelled()
            db.commit()
    except CollaborationCancelled:
        finish_failure(db_task_id, "USER_CANCELLED", "协作审校已取消")
    except TimeoutError:
        finish_failure(db_task_id, "TIMEOUT", "协作审校超过时间预算，已完成角色的状态保留")
    except Exception as exc:
        logger.warning("协作审校失败 task={} error={}", db_task_id, type(exc).__name__)
        finish_failure(db_task_id, "COLLABORATION_FAILED", "协作审校未能完成，请检查模型配置或稍后重试")
