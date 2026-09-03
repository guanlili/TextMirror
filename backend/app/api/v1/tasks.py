"""
TextMirror 异步任务状态查询 API
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from celery.result import AsyncResult
from loguru import logger
from sqlalchemy import select

from app.celery_app import celery_app
from app.core.dependencies import get_current_user_optional

router = APIRouter(prefix="/tasks", tags=["异步任务"])


def _task_user_id(result: AsyncResult):
    """提取任务归属（PROGRESS meta / SUCCESS 结果中的 user_id）"""
    if result.state == "PROGRESS" and isinstance(result.info, dict):
        return result.info.get("user_id")
    if result.state == "SUCCESS" and isinstance(result.result, dict):
        return result.result.get("user_id")
    return None


def _build_status(task_id: str, result: AsyncResult) -> dict:
    """构造任务状态响应（与 GET /{task_id} 同构）"""
    response = {
        "task_id": task_id,
        "status": result.state,
    }
    if result.state == "PENDING":
        response["progress"] = 0
        response["message"] = "任务排队中..."
    elif result.state == "PROGRESS":
        meta = result.info or {}
        response["progress"] = meta.get("progress", 0)
        response["message"] = meta.get("message", "处理中...")
        response["step"] = meta.get("step", "")
    elif result.state == "SUCCESS":
        response["progress"] = 100
        response["message"] = "校对完成"
        payload = dict(result.result) if isinstance(result.result, dict) else result.result
        if isinstance(payload, dict):
            payload.pop("user_id", None)
        response["result"] = payload
    elif result.state == "FAILURE":
        logger.warning(f"异步任务失败: task_id={task_id}, error={result.info}")
        response["progress"] = 0
        response["message"] = "任务失败，请重新提交（如持续失败请联系管理员）"
        response["error"] = "task_failed"
    else:
        response["progress"] = 5
        response["message"] = f"状态: {result.state}"
    return response


@router.get("/{task_id}", summary='查询异步任务状态')
async def get_task_status(
    task_id: str,
    current_user=Depends(get_current_user_optional),
):
    """
    查询异步任务状态
    返回任务当前状态、进度和结果（任务归属校验：仅任务提交者及超管可查看）
    """
    result = AsyncResult(task_id, app=celery_app)

    task_user_id = _task_user_id(result)
    if task_user_id is not None:
        if current_user is None or current_user.id != task_user_id:
            raise HTTPException(status_code=404, detail="任务不存在")

    return _build_status(task_id, result)


@router.get("/{task_id}/stream", summary='任务状态 SSE 推送')
async def stream_task_status(
    task_id: str,
    current_user=Depends(get_current_user_optional),
    token: str = None,
):
    """
    任务状态 SSE 推送
    服务端轮询 Celery 状态（500ms）仅在变化时推送，
    终态（SUCCESS/FAILURE/REVOKED）推送后关闭流。
    EventSource 无法携带 Authorization 头，登录用户通过 ?token= 认证。
    """
    from fastapi import Request

    # EventSource 场景：query token 兜底认证
    if current_user is None and token:
        from app.core.dependencies import get_current_user
        from app.core.database import async_session_factory
        from app.core.security import decode_token
        from app.models.user import User
        try:
            payload = decode_token(token)
            if payload and payload.get("type") == "access":
                async with async_session_factory() as db:
                    res = await db.execute(select(User).where(User.id == int(payload.get("sub"))))
                    current_user = res.scalar_one_or_none()
        except Exception:
            current_user = None

    # 归属校验放在流开始前，避免无权限连接占用资源
    result = AsyncResult(task_id, app=celery_app)
    task_user_id = _task_user_id(result)
    if task_user_id is not None:
        if current_user is None or current_user.id != task_user_id:
            raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        last_payload = None
        idle_seconds = 0
        # 空闲 10 分钟（任务丢失/未执行）自动断开
        while idle_seconds < 600:
            result = AsyncResult(task_id, app=celery_app)
            payload = _build_status(task_id, result)
            if payload != last_payload:
                last_payload = payload
                idle_seconds = 0
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                if payload["status"] in ("SUCCESS", "FAILURE", "REVOKED"):
                    return
            else:
                idle_seconds += 0.5
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
