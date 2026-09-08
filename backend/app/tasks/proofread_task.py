"""
TextMirror 异步文档校对任务
通过 Celery 在后台执行耗时的大模型调用
"""
import os
import json
import asyncio
from loguru import logger

from app.celery_app import celery_app
from app.core.config import settings
from app.core.file_security import sanitize_filename, safe_upload_path, build_download_url

# 注册 ORM 元数据：任务内保存 ProofreadRecord 时其 user_id 外键需要解析到 users 表，
# 且 User↔Role 相互引用需同时注册，否则 mapper 初始化失败（记录保存静默失败）
import app.models.user  # noqa
import app.models.role  # noqa


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


def _refund_key_daily_quota(api_key_id: int) -> None:
    """同步 Redis 退还密钥日配额计数（失败不抛出，仅记日志）"""
    try:
        import redis as sync_redis
        from datetime import datetime
        from zoneinfo import ZoneInfo

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


@celery_app.task(bind=True, name="proofread.async_document")
def async_proofread_document(
    self,
    text: str,
    check_types: list = None,
    domain: str = "general",
    file_id: str = None,
    filename: str = None,
    file_path: str = None,
    file_ext: str = None,
    user_id: int = None,
    config_id: int = None,
    api_key_id: int = None,
):
    """
    异步执行文档校对任务

    :param text: 提取的文本内容
    :param check_types: 校对类型
    :param domain: 领域
    :param file_id: 文件ID
    :param filename: 原始文件名
    :param file_path: 文件路径
    :param file_ext: 文件扩展名
    :param user_id: 用户ID
    :param config_id: 指定模型配置ID（None 用当前活跃模型）
    :param api_key_id: 开放API密钥ID（用于失败时退还密钥日配额；Web端调用不传）
    """
    task_id = self.request.id
    logger.info(f"[Task {task_id}] 开始异步校对: file={filename}, text_len={len(text)}")

    # 更新进度（meta 带 user_id，供任务状态查询做归属校验）
    self.update_state(state="PROGRESS", meta={"step": "proofread", "progress": 10, "message": "正在调用AI模型校对...", "user_id": user_id})

    try:
        # 调用校对服务（异步转同步）
        from app.services.proofread import proofread_text
        result = _run_async(proofread_text(text=text, domain=domain, config_id=config_id, user_id=user_id))
    except Exception as e:
        logger.error(f"[Task {task_id}] 校对失败: {e}")
        # 开放API提交的任务：退还密钥日配额（服务端失败不该消耗额度）
        if api_key_id:
            _refund_key_daily_quota(api_key_id)
        # 不能手动写 FAILURE state（Celery 仅允许 raise 触发），
        # meta 带错误信息供状态查询展示
        self.update_state(state="PROGRESS", meta={"step": "failed", "progress": 0, "message": f"校对失败: {e}", "user_id": user_id})
        raise

    self.update_state(state="PROGRESS", meta={"step": "generate", "progress": 80, "message": "正在生成修订文档...", "user_id": user_id})

    # 生成修订文档
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
    except Exception as e:
        logger.warning(f"[Task {task_id}] 生成修订文档失败: {e}")

    self.update_state(state="PROGRESS", meta={"step": "save", "progress": 95, "message": "正在保存记录...", "user_id": user_id})

    # 保存校对记录到数据库
    record_id = None
    if user_id:
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import Session
            from app.models.proofread import ProofreadRecord

            sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg2")
            sync_engine = create_engine(sync_url)
            with Session(sync_engine) as session:
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
                session.commit()
                record_id = record.id
            sync_engine.dispose()
        except Exception as e:
            logger.warning(f"[Task {task_id}] 保存记录失败: {e}")

    logger.info(f"[Task {task_id}] 异步校对完成: issues={result['total_issues']}")

    return {
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
    from sqlalchemy import create_engine, text as sa_text
    from datetime import datetime, timedelta, timezone

    from app.services.upload import remove_upload_dir

    upload_dir = os.path.abspath(settings.UPLOAD_DIR)
    if not os.path.isdir(upload_dir):
        logger.info("[定时清理] 上传目录不存在，跳过")
        return {"skipped": True}

    sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = create_engine(sync_url)
    stats = {"deleted_records": 0, "orphan_dirs": 0, "expired_guest": 0, "missing_files": 0}
    try:
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
    finally:
        engine.dispose()


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
    from sqlalchemy import create_engine, text as sa_text
    from datetime import datetime, timedelta

    sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = create_engine(sync_url)
    try:
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
    finally:
        engine.dispose()
