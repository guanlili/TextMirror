"""
TextMirror 异步任务状态查询 API
归属校验从 proofread_tasks 表读取，覆盖所有状态（含 FAILURE）。
"""
import asyncio
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy import select, update

from app.core.database import async_session_factory
from app.core.dependencies import get_current_user_optional

router = APIRouter(prefix="/tasks", tags=["异步任务"])


async def _load_task_with_auth(
    task_id: str,
    current_user,
    access_token: str = None,
    is_super_admin: bool = False,
):
    """
    加载 proofread_tasks 记录并校验归属。
    支持三种认证：登录用户（JWT）、游客（access_token SHA-256）、超管。
    """
    from app.models.proofread_task import ProofreadTask

    async with async_session_factory() as db:
        result = await db.execute(
            select(ProofreadTask).where(ProofreadTask.task_id == task_id)
        )
        db_task = result.scalar_one_or_none()

        if db_task is None:
            return None

        # 超管直通
        if current_user is not None:
            if not is_super_admin:
                from app.models.role import Role
                role_result = await db.execute(select(Role).where(Role.id == current_user.role_id))
                role = role_result.scalar_one_or_none()
                is_super_admin = bool(role and role.code == "super_admin")
            if is_super_admin:
                return db_task

    # 按 owner_kind 校验
    if db_task.owner_kind == "user":
        if current_user is None or current_user.id != db_task.owner_user_id:
            return None
    elif db_task.owner_kind == "guest":
        if not access_token:
            return None
        token_hash = hashlib.sha256(access_token.encode()).hexdigest()
        if token_hash != db_task.access_token_hash:
            return None
    elif db_task.owner_kind == "api_key":
        return None
    else:
        return None

    return db_task


def _build_status_payload(task_id: str, db_task) -> dict:
    """从 proofread_tasks 记录构造状态响应 dict"""
    if db_task is None:
        return {
            "task_id": task_id,
            "status": "PENDING",
            "progress": 0,
            "message": "任务排队中...",
        }

    status = db_task.status
    if status in ("STARTED", "PROGRESS"):
        display_status = "PROGRESS"
    elif status == "SUCCESS":
        display_status = "SUCCESS"
    elif status in ("FAILURE", "CANCELLED", "REVOKED"):
        display_status = status
    else:
        display_status = status

    response = {
        "task_id": task_id,
        "status": display_status,
        "progress": db_task.progress or 0,
        "message": db_task.message or "",
        "step": db_task.phase or "",
    }

    if display_status == "SUCCESS" and db_task.result_json:
        payload = dict(db_task.result_json)
        payload.pop("user_id", None)
        response["result"] = payload
    elif display_status in ("FAILURE", "CANCELLED", "REVOKED"):
        response["error"] = db_task.error_code or "task_failed"

    return response


@router.get("/{task_id}", summary='查询异步任务状态')
async def get_task_status(
    task_id: str,
    current_user=Depends(get_current_user_optional),
    access_token: str = None,
):
    """
    查询异步任务状态（DB 驱动）。
    登录用户通过 JWT 认证，游客通过 access_token 认证。
    """
    is_super_admin = bool(current_user and getattr(current_user, "role_code", None) == "super_admin")
    db_task = await _load_task_with_auth(
        task_id,
        current_user,
        access_token,
        is_super_admin=is_super_admin,
    )
    if db_task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    return _build_status_payload(task_id, db_task)


@router.get("/{task_id}/stream", summary='任务状态 SSE 推送')
async def stream_task_status(
    task_id: str,
    http_request: Request,
    current_user=Depends(get_current_user_optional),
    token: str = None,
    access_token: str = None,
):
    """
    任务状态 SSE 推送。
    服务端以短会话自适应轮询 proofread_tasks 表，终态推送后关闭流。

    认证方式：
    - 登录用户：Authorization header 或 ?token=
    - 游客：?access_token=
    """
    role_code = None
    if current_user is None and token:
        from app.core.security import decode_token
        from app.models.user import User
        try:
            payload = decode_token(token)
            if payload and payload.get("type") == "access":
                role_code = payload.get("role_code")
                if current_user is None:
                    async with async_session_factory() as db:
                        res = await db.execute(select(User).where(User.id == int(payload.get("sub"))))
                        current_user = res.scalar_one_or_none()
        except Exception:
            current_user = None

    if current_user is not None and role_code is None:
        role_code = getattr(current_user, "role_code", None)
    is_super_admin = role_code == "super_admin"

    db_task = await _load_task_with_auth(
        task_id,
        current_user,
        access_token,
        is_super_admin=is_super_admin,
    )
    if db_task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        from app.models.proofread_task import ProofreadTask

        last_payload = None
        idle_seconds = 0.0
        heartbeat_seconds = 0.0
        interval = 1.0
        async with async_session_factory() as db:
            while idle_seconds < 600:
                if await http_request.is_disconnected():
                    return

                db.expire_all()
                result = await db.execute(
                    select(ProofreadTask).where(ProofreadTask.task_id == task_id)
                )
                fresh_task = result.scalar_one_or_none()

                payload = _build_status_payload(task_id, fresh_task)
                changed = payload != last_payload
                if changed:
                    last_payload = payload
                    idle_seconds = 0.0
                    heartbeat_seconds = 0.0
                    interval = 1.0
                    yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                    if payload["status"] in ("SUCCESS", "FAILURE", "REVOKED", "CANCELLED"):
                        return
                else:
                    idle_seconds += interval
                    heartbeat_seconds += interval
                    if heartbeat_seconds >= 15:
                        heartbeat_seconds = 0.0
                        yield ": heartbeat\n\n"

                await asyncio.sleep(interval)
                if not changed:
                    interval = min(interval + 1.0, 5.0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{task_id}/cancel", summary='取消任务')
async def cancel_task(
    task_id: str,
    current_user=Depends(get_current_user_optional),
    access_token: str = None,
):
    """
    取消任务。
    - 排队中（PENDING）：CAS 置为 CANCELLED + revoke Celery
    - 执行中（STARTED/PROGRESS）：置 cancel_requested 标志，worker 在阶段间协作退出
    - 已终态：幂等返回成功
    """
    from datetime import datetime, timezone

    from app.models.proofread_task import ProofreadTask

    is_super_admin = bool(current_user and getattr(current_user, "role_code", None) == "super_admin")
    db_task = await _load_task_with_auth(
        task_id,
        current_user,
        access_token,
        is_super_admin=is_super_admin,
    )
    if db_task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    TERMINAL = {"SUCCESS", "FAILURE", "CANCELLED", "REVOKED"}
    if db_task.status in TERMINAL:
        return {"message": "任务已结束", "task_id": task_id}

    async with async_session_factory() as db:
        result = await db.execute(
            select(ProofreadTask).where(ProofreadTask.task_id == task_id)
        )
        fresh = result.scalar_one_or_none()
        if fresh is None or fresh.status in TERMINAL:
            return {"message": "任务已结束", "task_id": task_id}

        if fresh.status == "PENDING":
            result = await db.execute(
                update(ProofreadTask)
                .where(
                    ProofreadTask.task_id == task_id,
                    ProofreadTask.status == "PENDING",
                )
                .values(
                    status="CANCELLED",
                    cancel_requested=True,
                    error_code="USER_CANCELLED",
                    message="用户取消",
                    finished_at=datetime.now(timezone.utc),
                )
            )
            if result.rowcount:
                await db.commit()
                try:
                    from app.celery_app import celery_app
                    celery_app.control.revoke(task_id, terminate=False)
                except Exception as e:
                    logger.warning(f"revoke 失败 (非致命): {e}")
                logger.info(f"[Cancel] 排队任务已取消: task_id={task_id}")
                return {"message": "取消请求已接受", "task_id": task_id}
            await db.rollback()
            fresh = (await db.execute(
                select(ProofreadTask).where(ProofreadTask.task_id == task_id)
            )).scalar_one_or_none()
            if fresh is None or fresh.status in TERMINAL:
                return {"message": "任务已结束", "task_id": task_id}

        cancel_request = await db.execute(
            update(ProofreadTask)
            .where(
                ProofreadTask.task_id == task_id,
                ProofreadTask.status.not_in(TERMINAL),
            )
            .values(cancel_requested=True, message="正在取消...")
        )
        if not cancel_request.rowcount:
            await db.rollback()
            return {"message": "任务已结束", "task_id": task_id}
        await db.commit()
        logger.info(f"[Cancel] 已发送取消请求: task_id={task_id}, status={fresh.status}")

    return {"message": "取消请求已接受", "task_id": task_id}
