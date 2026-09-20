"""
TextMirror 异步文档校对任务
通过 Celery 在后台执行耗时的大模型调用
"""
import asyncio
import json
import os
import threading

from billiard.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from celery import signals
from celery.worker.request import Request
from loguru import logger
from sqlalchemy import func, select, update

# 注册 ORM 元数据：任务内保存 ProofreadRecord 时其 user_id/api_key_id 外键需要解析到
# users/api_keys 表，且 User↔Role 相互引用需同时注册，否则 mapper 初始化失败（记录保存静默失败）
import app.models.api_key  # noqa
import app.models.role  # noqa
import app.models.user  # noqa
from app.celery_app import celery_app
from app.core.config import settings
from app.core.file_security import build_download_url, safe_upload_path, sanitize_filename
from app.core.task_quota import REFUND_TASK_QUOTA_LUA, document_quota_key, refund_document_quota_sync

_run_async_state = threading.local()
_DOCUMENT_TIME_BUDGET_S = 240


def _run_async(coro):
    """在同步 Celery worker 中运行异步协程

    每线程复用已创建的 loop（避免 asyncio.run 每次关闭 loop 丢弃绑定其上的连接池），
    并显式管理 loop 生命周期，不依赖已弃用的 get_event_loop 自动创建行为。
    """
    loop = getattr(_run_async_state, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _run_async_state.loop = loop
    task = loop.create_task(coro)
    try:
        return loop.run_until_complete(task)
    except BaseException:
        # 信号中断不能把未完成的协程留到同一进程的下一笔任务。
        task.cancel()
        loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        raise


# ---- Celery worker 子进程级同步引擎单例 ----
# _sync_engine / _sync_engine_pid 在 prefork 子进程首次 _get_sync_engine() 时惰性创建，
# PID 漂移（fork）后重建；worker_shutdown 信号统一 dispose。
_sync_engine = None
_sync_engine_pid = None
_sync_engine_lock = threading.Lock()


def _get_sync_redis():
    """同步 Redis 客户端（任务内退款用；调用方负责 close）"""
    import redis as sync_redis

    return sync_redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        username=settings.REDIS_USERNAME or None,
        password=settings.REDIS_PASSWORD or None,
        db=settings.REDIS_DB,
        decode_responses=True,
        socket_timeout=5,
    )


def _refund_key_daily_quota(api_key_id: int, task_id: str) -> bool:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        with _get_sync_redis() as redis:
            return bool(redis.eval(
                REFUND_TASK_QUOTA_LUA, 2, f"textmirror:apikey_daily:{api_key_id}:{today}",
                f"textmirror:document_key_refund:{task_id}",
            ))
    except Exception as e:
        logger.warning(f"[退款] 密钥日配额退还失败 key_id={api_key_id}: {e}")
        return False


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


@signals.worker_process_init.connect
def _reset_sync_engine_after_fork(**_kwargs) -> None:
    global _sync_engine, _sync_engine_pid, _sync_engine_lock
    _sync_engine_lock = threading.Lock()
    if _sync_engine is not None:
        _sync_engine.dispose(close=False)
    _sync_engine = None
    _sync_engine_pid = None


@signals.worker_process_shutdown.connect
@signals.worker_shutdown.connect
def _dispose_sync_engine(**_kwargs) -> None:
    """主进程与 prefork 子进程退出时分别释放各自连接池。"""
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


class ProofreadDocumentRequest(Request):
    def on_timeout(self, soft, timeout):
        super().on_timeout(soft, timeout)
        if not soft:
            # Billiard 在此回调返回后才终止子进程，数据库清理不能阻塞它。
            threading.Thread(
                target=self.task.on_failure,
                args=(TimeLimitExceeded(timeout), self.id, self.args, self.kwargs, None),
                daemon=True,
            ).start()


class ProofreadDocumentTask(celery_app.Task):
    """文档校对任务基类：统一处理最终失败时的配额退款。"""

    Request = ProofreadDocumentRequest

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        任务最终失败时统一退还额度。
        退款从任务体移到此处，保证多次重试只退一次；
        用户取消、集成方指定无效配置等不重试场景也不退款（密钥口径）。
        """
        from sqlalchemy.orm import Session

        from app.models.proofread_task import ProofreadTask

        if not args:
            return
        db_task_id = args[0]
        try:
            with Session(_get_sync_engine()) as session:
                db_task = session.scalar(select(ProofreadTask).where(
                    ProofreadTask.id == db_task_id,
                ).with_for_update())
                if db_task is None or db_task.status in ("SUCCESS", "REVOKED"):
                    return
                if db_task.status in ("STARTED", "PROGRESS", "RETRYING"):
                    from datetime import datetime, timezone

                    db_task.status = "FAILURE"
                    if isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded)):
                        db_task.error_code = "TIMEOUT"
                        db_task.message = "文档处理超时，请缩短文档或稍后重试"
                    if not db_task.error_code:
                        db_task.error_code = "PROOFREAD_FAILED"
                    if not db_task.message:
                        db_task.message = "校对任务最终失败"
                    db_task.finished_at = datetime.now(timezone.utc)
                refund_task_id, quota_key = db_task.task_id, document_quota_key(db_task)
                api_key_id = db_task.owner_api_key_id
                refund_key = db_task.status != "CANCELLED" and db_task.error_code != "INVALID_CONFIG"
                failure = {"error_code": db_task.error_code or "PROOFREAD_FAILED",
                           "message": db_task.message or "校对任务最终失败"}
                session.commit()
            refund_document_quota_sync(refund_task_id, quota_key)
            if refund_key and api_key_id and _refund_key_daily_quota(api_key_id, refund_task_id):
                from app.services.webhook import build_event, dispatch_webhook

                dispatch_webhook(api_key_id, build_event("document.failed", refund_task_id, failure))
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
                .values(
                    status="STARTED",
                    started_at=(func.coalesce(ProofreadTask.started_at, datetime.now(timezone.utc))
                                if is_retry else datetime.now(timezone.utc)),
                )
            )
            if claim.rowcount != 1:
                session.rollback()
                skipped_task = session.get(ProofreadTask, db_task_id)
                if skipped_task is not None and skipped_task.status in ("CANCELLED", "FAILURE"):
                    refund_document_quota_sync(skipped_task.task_id, document_quota_key(skipped_task))
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
            depth = params.get("depth", "standard")
            user_id = db_task.owner_user_id
            file_id = doc_record.file_id
            filename = doc_record.filename
            file_path = doc_record.file_path
            file_ext = doc_record.file_ext
            check_types = params.get("check_types")
            # 取消异常退出 session 后 ORM 属性可能过期，先保存退款上下文。
            refund_task_id, quota_key = db_task.task_id, document_quota_key(db_task)

            logger.info(
                f"[Task {celery_task_id}] 开始异步校对: db_task_id={db_task_id} "
                f"file={filename}, text_len={len(text)}"
            )

            _check_cancel(session, db_task, celery_task_id)
            _update_task_progress(session, db_task, "proofread", 10, "正在调用AI模型校对...")

            # 分片粒度进度回调：proofread_text 报 0~70/72，映射到任务条的 10~80%
            # （80 起是修订文档生成与保存阶段）。回调在 worker 的事件循环线程池里
            # 执行，与下面的 run_until_complete 串行——DB 写不与主流程并发。
            def _on_proofread_progress(pct: int, message: str):
                task_pct = 10 + int(pct * 0.7)
                db_task.phase = "proofread"
                db_task.progress = task_pct
                db_task.message = message
                session.commit()

            # 分片完成回调：逐片写入部分问题列表，SSE 端点读取后推送给前端
            _partial_issues: list = []

            def _on_chunk_done(chunk_index: int, issues: list, total_issues: int):
                _partial_issues.extend(issues)
                result_payload = db_task.result_json or {}
                result_payload["partial_issues"] = list(_partial_issues)
                result_payload["partial_chunks"] = min(len(_partial_issues) and (chunk_index + 1), len(_partial_issues))
                result_payload["partial_total"] = total_issues
                db_task.result_json = result_payload
                session.commit()

            try:
                from app.services.proofread import _make_chunk_cache, proofread_text

                started_at = db_task.started_at
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                remaining = _DOCUMENT_TIME_BUDGET_S - (datetime.now(timezone.utc) - started_at).total_seconds()

                chunk_cache = None
                try:
                    chunk_cache = _make_chunk_cache(_get_sync_redis(), text, config_id, depth)
                except Exception as e:
                    logger.warning(f"[Task {celery_task_id}] 分片缓存初始化失败（降级为无缓存）: {e}")

                result = _run_async(asyncio.wait_for(proofread_text(
                    text=text, domain=domain, config_id=config_id, user_id=user_id,
                    depth=depth, on_progress=_on_proofread_progress,
                    chunk_cache=chunk_cache, on_chunk_done=_on_chunk_done,
                ), timeout=max(0, remaining)))
            except (TimeoutError, SoftTimeLimitExceeded):
                db_task.status = "FAILURE"
                db_task.error_code = "TIMEOUT"
                db_task.message = "文档审校超过时间预算，请缩短文档或稍后重试"
                db_task.finished_at = datetime.now(timezone.utc)
                session.commit()
                raise
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
                # 新 Web 任务显式标记先审阅；开放 API（含 Bearer 用户）及旧任务保留修订件。
                if not params.get("review_before_export", False) and file_path and file_ext in (".docx", ".txt"):
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
            except SoftTimeLimitExceeded:
                raise
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
                        api_key_id=db_task.owner_api_key_id,
                        type="document",
                        source_file_id=file_id,
                        original_text=text,
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
                except SoftTimeLimitExceeded:
                    raise
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
                "coverage": result.get("coverage"),
                "depth": result.get("depth", "standard"),
                "config_id": result.get("config_id"),
                "check_types": result.get("check_types", []),
            }

            from datetime import datetime, timezone
            completed = session.execute(update(ProofreadTask).where(
                ProofreadTask.id == db_task_id,
                ProofreadTask.status.in_(("STARTED", "PROGRESS")),
            ).values(status="SUCCESS", progress=100, phase="done", message="校对完成",
                     result_json=result_payload, finished_at=datetime.now(timezone.utc)))
            if completed.rowcount != 1:
                session.rollback()
                return {"skipped": True, "task_id": celery_task_id}
            session.commit()

            # 开放 API 提交且配置了回调：推送完成事件（尽力而为，不阻塞主任务）
            if db_task.owner_api_key_id:
                from app.services.webhook import build_event, dispatch_webhook

                dispatch_webhook(db_task.owner_api_key_id, build_event(
                    "document.completed",
                    db_task.task_id,
                    {
                        "total_issues": result["total_issues"],
                        "chunks_count": result["chunks_count"],
                        "corrected_download_url": result_payload.get("corrected_download_url"),
                    },
                ))

            logger.info(f"[Task {celery_task_id}] 异步校对完成: issues={result['total_issues']}")
            return result_payload

    except _CancelledError:
        logger.info(f"[Task {celery_task_id}] 任务已被用户取消")
        # 取消=零产出：退还预扣的用户日配额（密钥额度按既有口径不退）
        refund_document_quota_sync(refund_task_id, quota_key)
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
        deleted_rows = conn.execute(sa_text(
            "SELECT file_id, owner_kind, status, created_at FROM uploaded_documents WHERE status = 'deleted'"
        )).fetchall()
    known = {r[0]: {"owner_kind": r[1], "status": r[2], "created_at": r[3]} for r in deleted_rows}

    # 1) 已删除记录的残留目录
    for file_id, info in known.items():
        if os.path.isdir(os.path.join(upload_dir, file_id)):
            remove_upload_dir(file_id)
            stats["deleted_records"] += 1

    # 3) 过期游客文件（同时把记录标记为 deleted，避免仍出现在后台列表）
    retention = settings.GUEST_FILE_RETENTION_DAYS
    if retention > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
        with engine.connect() as conn:
            expired_rows = conn.execute(sa_text(
                "SELECT file_id FROM uploaded_documents "
                "WHERE owner_kind = 'guest' AND status != 'deleted' AND created_at < :cutoff"
            ), {"cutoff": cutoff}).fetchall()
        expired = [r[0] for r in expired_rows]
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

    # 2) 孤儿目录（无任何数据库记录）— 需要全量 file_id 列表做差集
    with engine.connect() as conn:
        all_file_ids = {r[0] for r in conn.execute(sa_text(
            "SELECT file_id FROM uploaded_documents"
        )).fetchall()}
    min_age = settings.ORPHAN_DIR_MIN_AGE_HOURS * 3600
    now = time.time()
    for name in os.listdir(upload_dir):
        path = os.path.join(upload_dir, name)
        if not os.path.isdir(path) or name == "icons" or name in all_file_ids:
            continue
        try:
            if now - os.path.getmtime(path) < min_age:
                continue
        except OSError:
            continue
        remove_upload_dir(name)
        stats["orphan_dirs"] += 1

    # 统计磁盘缺失的有效记录（只观察不处理）
    with engine.connect() as conn:
        active_rows = conn.execute(sa_text(
            "SELECT file_id FROM uploaded_documents WHERE status != 'deleted'"
        )).fetchall()
    for r in active_rows:
        file_id = r[0]
        if not os.path.isdir(os.path.join(upload_dir, file_id)):
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
    分批删除（每批 1000），避免大表单次长事务锁。
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text as sa_text

    engine = _get_sync_engine()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    batch_size = 1000
    total_deleted = 0

    while True:
        with engine.connect() as conn:
            result = conn.execute(
                sa_text(
                    "DELETE FROM audit_logs WHERE id IN ("
                    "SELECT id FROM audit_logs WHERE created_at < :cutoff LIMIT :batch_size"
                    ")"
                ),
                {"cutoff": cutoff, "batch_size": batch_size},
            )
            conn.commit()
            deleted = result.rowcount or 0
        total_deleted += deleted
        if deleted < batch_size:
            break

    logger.info(f"[定时清理] 审计日志清理完成: 删除 {total_deleted} 条（{retention_days} 天前）")
    return {"deleted": total_deleted}
