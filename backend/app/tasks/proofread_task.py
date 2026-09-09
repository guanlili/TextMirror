"""
TextMirror 异步文档校对任务
通过 Celery 在后台执行耗时的大模型调用
"""
import asyncio
import json
import os
import threading

from celery import signals
from loguru import logger
from sqlalchemy import select, update

import app.models.role  # noqa

# 注册 ORM 元数据：任务内保存 ProofreadRecord 时其 user_id 外键需要解析到 users 表，
# 且 User↔Role 相互引用需同时注册，否则 mapper 初始化失败（记录保存静默失败）
import app.models.user  # noqa
from app.celery_app import celery_app
from app.core.config import settings
from app.core.file_security import build_download_url, safe_upload_path, sanitize_filename


def _run_async(coro):
    """在同步 Celery worker 中运行异步协程"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# 与 app.core.rate_limit._api_key_daily_redis_key 同构（此处为同步上下文，避免引入异步模块依赖）
_REFUND_LUA = (
    "local c = redis.call('GET', KEYS[1]) "
    "if c and tonumber(c) > 0 then return redis.call('DECR', KEYS[1]) end "
    "return 0"
)

# ---- Celery worker 子进程级同步引擎单例 ----
# _sync_engine / _sync_engine_pid 在 prefork 子进程首次 _get_sync_engine() 时惰性创建，
# PID 漂移（fork）后重建；worker_shutdown 信号统一 dispose。
_sync_engine = None
_sync_engine_pid = None
_sync_engine_lock = threading.Lock()


def _refund_key_daily_quota(api_key_id: int) -> None:
    """同步 Redis 退还密钥日配额计数（失败不抛出，仅记日志）"""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        import redis as sync_redis

        r = sync_redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            username=settings.REDIS_USERNAME or None,
            password=settings.REDIS_PASSWORD or None,
            db=settings.REDIS_DB,
            decode_responses=True,
            socket_timeout=5,
        )
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        r.eval(_REFUND_LUA, 1, f"textmirror:apikey_daily:{api_key_id}:{today}")
        r.close()
    except Exception as e:
        logger.warning(f"[退款] 密钥日配额退还失败 key_id={api_key_id}: {e}")


def _get_sync_engine():
    """同步数据库引擎（Celery worker 子进程级单例）。

    prefork worker 的子进程长驻且每个任务只跑在一个进程里，因此引擎可在进程内
    复用连接池。首次调用时创建；fork 后子进程继承的父进程引擎不可用，按 PID
    检测并重建。worker_shutdown 信号统一 dispose，避免容器停止时残留连接。
    """
    global _sync_engine, _sync_engine_pid
    pid = os.getpid()
    if _sync_engine is None or _sync_engine_pid != pid:
        with _sync_engine_lock:
            if _sync_engine is None or _sync_engine_pid != pid:
                from sqlalchemy import create_engine
                sync_url = settings.DATABASE_URL
                if sync_url.startswith("postgresql+asyncpg"):
                    sync_url = sync_url.replace("postgresql+asyncpg", "postgresql+psycopg2", 1)
                elif sync_url.startswith("sqlite+aiosqlite"):
                    sync_url = sync_url.replace("sqlite+aiosqlite", "sqlite+pysqlite", 1)
                _sync_engine = create_engine(
                    sync_url,
                    pool_pre_ping=True,
                    pool_size=5,
                    max_overflow=10,
                    pool_recycle=1800,
                )
                _sync_engine_pid = pid
                logger.info(
                    f"[celery-engine] 创建同步引擎 pid={pid} url={sync_url.split('@')[-1]}"
                )
    return _sync_engine


@signals.worker_shutdown.connect
def _dispose_sync_engine(**_kwargs) -> None:
    """worker 退出时释放连接池（prefork 主/子进程均会触发，无害幂等）"""
    global _sync_engine, _sync_engine_pid
    if _sync_engine is not None:
        try:
            _sync_engine.dispose()
            logger.info(f"[celery-engine] 已 dispose 同步引擎 pid={os.getpid()}")
        except Exception as e:
            logger.warning(f"[celery-engine] dispose 失败: {e}")
        finally:
            _sync_engine = None
            _sync_engine_pid = None


def _update_task_progress(session, db_task, phase: str, progress: int, message: str, status: str = None):
    """更新 DB 任务进度（同时更新 Celery meta 以保持兼容）"""
    db_task.phase = phase
    db_task.progress = progress
    db_task.message = message
    if status:
        db_task.status = status
    session.commit()


class _CancelledError(Exception):
    """worker 内部信号：用户已取消"""


class ProofreadRetryableError(Exception):
    """可重试的校对失败（LLM/网络/数据库瞬态错误），触发 Celery 自动重试。"""


class ProofreadDocumentTask(celery_app.Task):
    """文档校对任务基类：统一处理最终失败时的配额退款。"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        任务最终失败时统一退还密钥日配额。
        退款从任务体移到此处，保证多次重试只退一次；
        用户取消、集成方指定无效配置等不重试场景也不退款。
        """
        from sqlalchemy.orm import Session

        from app.models.proofread_task import ProofreadTask

        if not args:
            return
        db_task_id = args[0]
        sync_engine = _get_sync_engine()
        try:
            with Session(sync_engine) as session:
                db_task = session.get(ProofreadTask, db_task_id)
                if db_task is None:
                    return
                # 用户取消与集成方指定无效配置不退款；
                # 服务端未配置活跃模型属于可退款场景。
                if db_task.status == "CANCELLED":
                    return
                if db_task.error_code == "INVALID_CONFIG":
                    return
                # 若仍处在执行/重试状态，标记为最终失败
                if db_task.status in ("STARTED", "RETRYING"):
                    from datetime import datetime, timezone

                    db_task.status = "FAILURE"
                    if not db_task.error_code:
                        db_task.error_code = "PROOFREAD_FAILED"
                    if not db_task.message:
                        db_task.message = "校对任务最终失败"
                    db_task.finished_at = datetime.now(timezone.utc)
                    session.commit()
                if db_task.owner_api_key_id:
                    _refund_key_daily_quota(db_task.owner_api_key_id)
        except Exception as e:
            logger.warning(f"[on_failure] 处理失败 task_id={task_id}: {e}")


def _is_invalid_config_error(exc: Exception) -> bool:
    """用户指定了无效 config_id：不重试、不退款。"""
    return "指定的模型配置不存在或已停用" in str(exc)


def _is_missing_model_config_error(exc: Exception) -> bool:
    """服务端未配置活跃模型：不重试但退款（非用户责任）。"""
    return "尚未配置可用的大模型" in str(exc)


def _check_cancel(session, db_task, celery_task_id: str) -> None:
    """阶段间协作退出点：刷新 DB 标志，若已取消则抛 _CancelledError"""
    session.refresh(db_task)
    if db_task.cancel_requested or db_task.status == "CANCELLED":
        from datetime import datetime, timezone
        logger.info(f"[Task {celery_task_id}] 检测到取消请求，停止执行")
        db_task.status = "CANCELLED"
        db_task.error_code = "USER_CANCELLED"
        db_task.message = "任务已取消"
        db_task.finished_at = datetime.now(timezone.utc)
        session.commit()
        raise _CancelledError()


@celery_app.task(
    bind=True,
    base=ProofreadDocumentTask,
    name="proofread.async_document",
    autoretry_for=(ProofreadRetryableError,),
    retry_kwargs={"max_retries": 2, "countdown": 5},
    retry_backoff=True,
)
def async_proofread_document(self, db_task_id: int):
    """
    异步执行文档校对任务（DB 驱动）。
    只接收 proofread_tasks.id，所有数据从数据库加载——
    Redis broker 消息体不再包含文档全文。
    """
    from sqlalchemy.orm import Session

    from app.models.proofread_task import ProofreadTask
    from app.models.uploaded_document import UploadedDocument

    celery_task_id = self.request.id
    sync_engine = _get_sync_engine()


    is_retry = getattr(self.request, "retries", 0) > 0
    expected_status = "RETRYING" if is_retry else "PENDING"

    try:
        with Session(sync_engine) as session:
            from datetime import datetime, timezone

            claim = session.execute(
                update(ProofreadTask)
                .where(ProofreadTask.id == db_task_id, ProofreadTask.status == expected_status)
                .values(status="STARTED", started_at=datetime.now(timezone.utc))
            )
            if claim.rowcount != 1:
                session.rollback()
                logger.info(f"[Task {celery_task_id}] 任务非 {expected_status}，跳过")
                return {"skipped": True, "task_id": celery_task_id}
            session.commit()

            db_task = session.get(ProofreadTask, db_task_id)
            if db_task is None:
                logger.error(f"[Task {celery_task_id}] proofread_tasks id={db_task_id} 不存在")
                raise ValueError(f"Task record {db_task_id} not found")

            if db_task.document_id:
                doc_record = session.execute(
                    select(UploadedDocument).where(UploadedDocument.file_id == db_task.document_id)
                ).scalar_one_or_none()
            else:
                doc_record = None
            if doc_record is None:
                db_task.status = "FAILURE"
                db_task.error_code = "DOCUMENT_MISSING"
                db_task.message = "文档记录不存在或已被清理"
                session.commit()
                raise ValueError(f"Document {db_task.document_id} not found")

            text = doc_record.extracted_text
            if not text:
                db_task.status = "FAILURE"
                db_task.error_code = "TEXT_EMPTY"
                db_task.message = "文档文本为空"
                session.commit()
                raise ValueError("Document text is empty")

            params = db_task.params_json or {}
            domain = params.get("domain", "general")
            config_id = params.get("config_id")
            user_id = db_task.owner_user_id
            file_id = doc_record.file_id
            filename = doc_record.filename
            file_path = doc_record.file_path
            file_ext = doc_record.file_ext
            check_types = params.get("check_types")

            logger.info(
                f"[Task {celery_task_id}] 开始异步校对: db_task_id={db_task_id} "
                f"file={filename}, text_len={len(text)}"
            )

            _check_cancel(session, db_task, celery_task_id)
            _update_task_progress(session, db_task, "proofread", 10, "正在调用AI模型校对...")

            try:
                from app.services.proofread import proofread_text
                result = _run_async(proofread_text(
                    text=text, domain=domain, config_id=config_id, user_id=user_id,
                ))
            except Exception as e:
                logger.error(f"[Task {celery_task_id}] 校对失败: {e}")
                if _is_invalid_config_error(e):
                    db_task.status = "FAILURE"
                    db_task.error_code = "INVALID_CONFIG"
                    db_task.message = f"校对失败: {e}"
                    from datetime import datetime, timezone
                    db_task.finished_at = datetime.now(timezone.utc)
                    session.commit()
                    raise
                if _is_missing_model_config_error(e):
                    db_task.status = "FAILURE"
                    db_task.error_code = "MODEL_NOT_CONFIGURED"
                    db_task.message = f"校对失败: {e}"
                    from datetime import datetime, timezone
                    db_task.finished_at = datetime.now(timezone.utc)
                    session.commit()
                    raise
                # 瞬态错误：状态置为 RETRYING，由 Celery 自动重试；
                # 退款移到 on_failure，避免重试期间重复退还。
                db_task.status = "RETRYING"
                db_task.error_code = "PROOFREAD_RETRYABLE"
                db_task.message = f"校对暂时失败，将自动重试: {e}"
                session.commit()
                raise ProofreadRetryableError(f"校对服务暂时不可用: {e}") from e

            _check_cancel(session, db_task, celery_task_id)
            _update_task_progress(session, db_task, "generate", 80, "正在生成修订文档...")

            corrected_url = None
            try:
                if file_path and file_ext and file_ext in (".docx", ".txt"):
                    from app.services.document import generate_corrected_docx, generate_corrected_txt
                    corrected_filename = sanitize_filename(f"校对修订_{filename}")
                    corrected_path = safe_upload_path(file_id, corrected_filename)
                    if file_ext == ".docx":
                        generate_corrected_docx(file_path, result["issues"], corrected_path)
                    else:
                        generate_corrected_txt(text, result["issues"], corrected_path)
                    corrected_url = build_download_url(file_id, corrected_filename)
                    db_task.output_path = corrected_path
                    session.commit()
            except Exception as e:
                logger.warning(f"[Task {celery_task_id}] 生成修订文档失败: {e}")

            _check_cancel(session, db_task, celery_task_id)
            _update_task_progress(session, db_task, "save", 95, "正在保存记录...")

            record_id = None
            if user_id:
                try:
                    from app.models.proofread import ProofreadRecord
                    record = ProofreadRecord(
                        user_id=user_id,
                        type="document",
                        original_text=text[:10000],
                        check_types=json.dumps(check_types or []),
                        domain=domain,
                        result=result,
                        total_issues=result["total_issues"],
                        token_usage=result["usage"],
                        source_filename=filename,
                    )
                    session.add(record)
                    session.flush()
                    record_id = record.id
                except Exception as e:
                    logger.warning(f"[Task {celery_task_id}] 保存记录失败: {e}")

            result_payload = {
                "file_id": file_id,
                "filename": filename,
                "user_id": user_id,
                "issues": result["issues"],
                "total_issues": result["total_issues"],
                "chunks_count": result["chunks_count"],
                "usage": result["usage"],
                "domain": result["domain"],
                "record_id": record_id,
                "corrected_download_url": corrected_url,
            }

            from datetime import datetime, timezone
            db_task.status = "SUCCESS"
            db_task.progress = 100
            db_task.phase = "done"
            db_task.message = "校对完成"
            db_task.result_json = result_payload
            db_task.finished_at = datetime.now(timezone.utc)
            session.commit()

            logger.info(f"[Task {celery_task_id}] 异步校对完成: issues={result['total_issues']}")
            return result_payload

    except _CancelledError:
        logger.info(f"[Task {celery_task_id}] 任务已被用户取消")
        return {"cancelled": True, "task_id": celery_task_id}


@celery_app.task(name="maintenance.clean_uploaded_documents")
def clean_uploaded_documents():
    """
    定时回收上传目录的存储泄漏（由 celery beat 每日触发）。
    处理三类：
      1) 已软删除记录的残留目录（删除接口已即时清理，此处兜底历史数据与失败情形）
      2) 无数据库记录的孤儿目录（超过 ORPHAN_DIR_MIN_AGE_HOURS，避免误删进行中的上传）
      3) 超过 GUEST_FILE_RETENTION_DAYS 的游客文件（游客无历史入口，过期即可回收）
    登录用户的文件不自动删除——保留策略属产品决策，此处仅统计磁盘缺失的记录数。
    """
    import os
    import time
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text as sa_text

    from app.services.upload import remove_upload_dir

    upload_dir = os.path.abspath(settings.UPLOAD_DIR)
    if not os.path.isdir(upload_dir):
        logger.info("[定时清理] 上传目录不存在，跳过")
        return {"skipped": True}

    engine = _get_sync_engine()
    stats = {"deleted_records": 0, "orphan_dirs": 0, "expired_guest": 0, "missing_files": 0}
    with engine.connect() as conn:
        rows = conn.execute(sa_text(
            "SELECT file_id, owner_kind, status, created_at FROM uploaded_documents"
        )).fetchall()

    known = {r[0]: {"owner_kind": r[1], "status": r[2], "created_at": r[3]} for r in rows}

    # 1) 已删除记录的残留目录
    for file_id, info in known.items():
        if info["status"] == "deleted" and os.path.isdir(os.path.join(upload_dir, file_id)):
            remove_upload_dir(file_id)
            stats["deleted_records"] += 1

    # 3) 过期游客文件（同时把记录标记为 deleted，避免仍出现在后台列表）
    retention = settings.GUEST_FILE_RETENTION_DAYS
    if retention > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
        expired = [
            fid for fid, info in known.items()
            if info["owner_kind"] == "guest" and info["status"] != "deleted"
            and info["created_at"] is not None
            and _as_utc_naive_safe(info["created_at"]) < cutoff
        ]
        for file_id in expired:
            remove_upload_dir(file_id)
            stats["expired_guest"] += 1
        if expired:
            with engine.connect() as conn:
                conn.execute(
                    sa_text(
                        "UPDATE uploaded_documents SET status='deleted', deleted_at=NOW(), "
                        "extracted_text=NULL WHERE file_id = ANY(:ids)"
                    ),
                    {"ids": expired},
                )
                conn.commit()

    # 2) 孤儿目录（无任何数据库记录）
    min_age = settings.ORPHAN_DIR_MIN_AGE_HOURS * 3600
    now = time.time()
    for name in os.listdir(upload_dir):
        path = os.path.join(upload_dir, name)
        if not os.path.isdir(path) or name == "icons" or name in known:
            continue
        try:
            if now - os.path.getmtime(path) < min_age:
                continue
        except OSError:
            continue
        remove_upload_dir(name)
        stats["orphan_dirs"] += 1

    # 统计磁盘缺失的有效记录（只观察不处理）
    for file_id, info in known.items():
        if info["status"] != "deleted" and not os.path.isdir(os.path.join(upload_dir, file_id)):
            stats["missing_files"] += 1

    logger.info(
        f"[定时清理] 上传目录清理完成: 已删记录残留={stats['deleted_records']} "
        f"孤儿目录={stats['orphan_dirs']} 过期游客文件={stats['expired_guest']} "
        f"磁盘缺失记录={stats['missing_files']}"
    )
    return stats


def _as_utc_naive_safe(dt):
    """兼容 naive/aware datetime 的比较（历史列可能无时区）"""
    from datetime import timezone as _tz
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=_tz.utc)


@celery_app.task(name="maintenance.clean_old_audit_logs")
def clean_old_audit_logs(retention_days: int = 90):
    """
    定时清理过期审计日志（默认保留 90 天，与后台手动清理同口径）。
    由 celery beat 每日 03:30 触发；audit_logs 含全文快照，只进不出会持续膨胀。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import text as sa_text

    engine = _get_sync_engine()
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    with engine.connect() as conn:
        result = conn.execute(
            sa_text("DELETE FROM audit_logs WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        conn.commit()
        deleted = result.rowcount or 0
    logger.info(f"[定时清理] 审计日志清理完成: 删除 {deleted} 条（{retention_days} 天前）")
    return {"deleted": deleted}
