"""游客数据生命周期：摘要、导出、认领与删除。"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.exceptions import BadRequestError
from app.core.guest_identity import COOKIE_NAME, get_guest_id
from app.models.issue_feedback import IssueFeedback
from app.models.proofread import ProofreadRecord
from app.models.proofread_task import ProofreadTask
from app.models.uploaded_document import UploadedDocument
from app.services.audit_log import record_audit_log
from app.services.upload import remove_upload_dir

router = APIRouter(prefix="/guest-data", tags=["游客数据"])


async def _counts(db: AsyncSession, guest_id: Optional[str]) -> dict:
    if not guest_id:
        return {"records": 0, "feedbacks": 0, "documents": 0}
    records = await db.scalar(
        select(func.count()).select_from(ProofreadRecord).where(
            ProofreadRecord.owner_kind == "guest",
            ProofreadRecord.guest_id == guest_id,
        )
    ) or 0
    feedbacks = await db.scalar(
        select(func.count()).select_from(IssueFeedback).where(
            IssueFeedback.guest_id == guest_id,
        )
    ) or 0
    documents = await db.scalar(
        select(func.count()).select_from(UploadedDocument).where(
            UploadedDocument.owner_kind == "guest",
            UploadedDocument.guest_id == guest_id,
        )
    ) or 0
    return {"records": int(records), "feedbacks": int(feedbacks), "documents": int(documents)}


@router.get("/summary", summary="当前访客的游客数据摘要")
async def guest_data_summary(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
    guest_id: Optional[str] = Depends(get_guest_id),
):
    counts = await _counts(db, guest_id)
    return {
        **counts,
        "total": counts["records"] + counts["feedbacks"] + counts["documents"],
        "can_claim": current_user is not None,
    }


@router.get("/export", summary="导出当前访客的全部游客数据")
async def export_guest_data(
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
    guest_id: Optional[str] = Depends(get_guest_id),
):
    if not guest_id:
        raise BadRequestError(code="GUEST_DATA_EMPTY", message="当前浏览器没有游客数据")

    records = (await db.execute(
        select(ProofreadRecord).where(
            ProofreadRecord.owner_kind == "guest",
            ProofreadRecord.guest_id == guest_id,
        ).order_by(ProofreadRecord.id)
    )).scalars().all()
    feedbacks = (await db.execute(
        select(IssueFeedback).where(IssueFeedback.guest_id == guest_id).order_by(IssueFeedback.id)
    )).scalars().all()
    documents = (await db.execute(
        select(UploadedDocument).where(
            UploadedDocument.owner_kind == "guest",
            UploadedDocument.guest_id == guest_id,
        ).order_by(UploadedDocument.id)
    )).scalars().all()

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "records": [
            {
                "id": r.id,
                "type": r.type,
                "domain": r.domain,
                "original_text": r.original_text,
                "modified_text": r.modified_text,
                "result": r.result,
                "total_issues": r.total_issues,
                "quota_weight": r.quota_weight,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
        "feedbacks": [
            {
                "record_id": f.record_id,
                "original": f.original,
                "suggestion": f.suggestion,
                "issue_type": f.issue_type,
                "action": f.action,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in feedbacks
        ],
        "documents": [
            {
                "file_id": d.file_id,
                "filename": d.filename,
                "file_ext": d.file_ext,
                "file_size": d.file_size,
                "text_length": d.text_length,
                "extracted_text": d.extracted_text,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in documents
        ],
    }
    record_audit_log(
        http_request, "guest_data_export", user=current_user,
        extra_params={"records": len(records), "feedbacks": len(feedbacks), "documents": len(documents)},
    )
    body = json.dumps(payload, ensure_ascii=False, default=str)
    filename = f"textmirror-guest-data-{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/claim", summary="把当前访客的游客数据合并到登录账号")
async def claim_guest_data(
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    guest_id: Optional[str] = Depends(get_guest_id),
):
    if not guest_id:
        return {"records": 0, "feedbacks": 0, "documents": 0, "tasks": 0}

    records = await db.execute(
        update(ProofreadRecord)
        .where(ProofreadRecord.owner_kind == "guest", ProofreadRecord.guest_id == guest_id)
        .values(user_id=current_user.id, owner_kind="user", guest_id=None)
    )
    feedbacks = await db.execute(
        update(IssueFeedback)
        .where(IssueFeedback.guest_id == guest_id, IssueFeedback.user_id.is_(None))
        .values(user_id=current_user.id, guest_id=None)
    )
    documents = await db.execute(
        update(UploadedDocument)
        .where(UploadedDocument.owner_kind == "guest", UploadedDocument.guest_id == guest_id)
        .values(
            user_id=current_user.id, owner_kind="user", guest_id=None,
            username=current_user.username,
        )
    )
    tasks = await db.execute(
        update(ProofreadTask)
        .where(ProofreadTask.owner_kind == "guest", ProofreadTask.owner_guest_id == guest_id)
        .values(owner_kind="user", owner_user_id=current_user.id, owner_guest_id=None)
    )
    await db.commit()

    counts = {
        "records": int(records.rowcount or 0),
        "feedbacks": int(feedbacks.rowcount or 0),
        "documents": int(documents.rowcount or 0),
        "tasks": int(tasks.rowcount or 0),
    }
    record_audit_log(
        http_request, "guest_data_claim", user=current_user, extra_params=counts,
    )
    logger.info(f"[游客数据] user={current_user.id} 认领 {counts}")
    return counts


class GuestDataDeleteRequest(BaseModel):
    confirm: str = Field(..., description="输入 DELETE 确认删除")


@router.delete("", summary="删除当前访客的全部游客数据")
async def delete_guest_data(
    payload: GuestDataDeleteRequest,
    http_request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
    guest_id: Optional[str] = Depends(get_guest_id),
):
    if payload.confirm != "DELETE":
        raise BadRequestError(code="CONFIRM_REQUIRED", message="请输入 DELETE 确认删除")
    if not guest_id:
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"records": 0, "feedbacks": 0, "documents": 0, "tasks": 0}

    document_ids = (await db.execute(
        select(UploadedDocument.file_id).where(
            UploadedDocument.owner_kind == "guest",
            UploadedDocument.guest_id == guest_id,
        )
    )).scalars().all()
    from app.api.v1.document import invalidate_document_cache

    for file_id in document_ids:
        try:
            await asyncio.to_thread(remove_upload_dir, file_id)
            invalidate_document_cache(file_id)
        except Exception as e:
            logger.warning(f"[游客数据] 清理文件失败 file_id={file_id}: {e}")

    tasks = await db.execute(
        delete(ProofreadTask).where(
            ProofreadTask.owner_kind == "guest",
            ProofreadTask.owner_guest_id == guest_id,
        )
    )
    feedbacks = await db.execute(
        delete(IssueFeedback).where(IssueFeedback.guest_id == guest_id)
    )
    records = await db.execute(
        delete(ProofreadRecord).where(
            ProofreadRecord.owner_kind == "guest",
            ProofreadRecord.guest_id == guest_id,
        )
    )
    documents = await db.execute(
        delete(UploadedDocument).where(
            UploadedDocument.owner_kind == "guest",
            UploadedDocument.guest_id == guest_id,
        )
    )
    await db.commit()

    counts = {
        "records": int(records.rowcount or 0),
        "feedbacks": int(feedbacks.rowcount or 0),
        "documents": int(documents.rowcount or 0),
        "tasks": int(tasks.rowcount or 0),
    }
    record_audit_log(
        http_request, "guest_data_delete", user=current_user, extra_params=counts,
    )
    response.delete_cookie(COOKIE_NAME, path="/")
    logger.info(f"[游客数据] 游客删除 {counts}")
    return counts
