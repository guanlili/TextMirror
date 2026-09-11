"""
TextMirror 开放 API——异步文档审校模块（提交 + 任务轮询）
"""
import asyncio
import os
import uuid
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.open_common import (
    DOC_ERROR_RESPONSES,
    JOBS_ERROR_RESPONSES,
    _check_user_quota_contract,
    _existing_submit_response,
    _normalize_idempotency_key,
)
from app.core.database import get_db
from app.core.dependencies import get_current_user_or_apikey
from app.core.file_security import sanitize_filename
from app.core.rate_limit import (
    charge_api_key_daily,
    check_api_key_rpm,
    check_upload_rate_limit,
    refund_api_key_daily_usage,
)
from app.core.security import hash_scoped_idempotency_key
from app.models.api_key import ApiKey
from app.models.uploaded_document import UploadedDocument
from app.models.user import User
from app.schemas.open import OpenDocumentSubmitResponse, OpenJobStatusResponse
from app.schemas.proofread import Domain
from app.services.audit_log import record_audit_log
from app.services.document import extract_text_from_file
from app.services.upload import UploadRejected, remove_upload_silently, store_upload
from app.tasks.proofread_task import async_proofread_document

router = APIRouter(tags=["开放API"])


def _parse_form_check_types(raw: Optional[str]) -> Optional[List[str]]:
    """已废弃参数：任何输入静默忽略（与「传入无效果」承诺自洽），仅保留签名兼容"""
    return None


def _validate_form_domain(raw: str) -> str:
    valid = {d.value for d in Domain}
    if raw not in valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": f"domain 非法: {raw}（可选: {', '.join(sorted(valid))}）"},
        )
    return raw



# ======================================================================
# 异步文档审校
# ======================================================================

ALLOWED_DOC_EXTENSIONS = {".doc", ".docx", ".pdf", ".txt"}


@router.post(
    "/documents",
    response_model=OpenDocumentSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="文档审校（异步）",
    description=(
        "上传文档并提交异步审校任务，立即返回 `job_id`。\n\n"
        "支持格式：`.doc` / `.docx` / `.pdf` / `.txt`，大小上限 20MB。\n\n"
        "**快速开始**（curl）：\n"
        "```bash\n"
        'curl -X POST .../api/v1/open/documents -H "Authorization: Bearer tm_..." \\\n'
        '  -F "file=@报告.docx"\n'
        "```\n\n"
        "**表单参数**（均可选）：`domain`（默认 general）、`config_id`"
        "（`check_types` 已废弃，传入无效果）。\n\n"
        "之后轮询 `status_url`（即 `GET /open/jobs/{job_id}`），"
        "`status=SUCCESS` 时 `result` 字段含审校结果与修订文档下载地址。"
    ),
    responses=DOC_ERROR_RESPONSES,
)
async def open_submit_document(
    http_request: Request,
    file: UploadFile = File(..., description="待审校文档：.doc/.docx/.pdf/.txt"),
    check_types: Optional[str] = Form(None, deprecated=True, description="（已废弃，传入无效果）历史参数：校对类型"),
    domain: str = Form("general", description="文本领域"),
    config_id: Optional[int] = Form(None, description="指定模型配置ID（可选）"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    开放文档异步审校：上传 → 提交 Celery 任务 → 返回 job_id
    """
    user, api_key = auth
    raw_idempotency_key = _normalize_idempotency_key(idempotency_key)
    owner_scope = f"open:api-key:{api_key.id}" if api_key else f"open:user:{user.id}"
    scoped_idempotency_key = (
        hash_scoped_idempotency_key(owner_scope, raw_idempotency_key)
        if raw_idempotency_key else None
    )

    from app.models.proofread_task import ProofreadTask

    if scoped_idempotency_key:
        existing = (await db.execute(
            select(ProofreadTask).where(ProofreadTask.idempotency_key == scoped_idempotency_key)
        )).scalar_one_or_none()
        if existing:
            if (
                existing.status == "FAILURE"
                and existing.error_code == "DISPATCH_FAILED"
                and existing.started_at is None
            ):
                await db.execute(
                    update(ProofreadTask)
                    .where(ProofreadTask.id == existing.id, ProofreadTask.status == "FAILURE")
                    .values(status="PENDING", error_code=None, message="任务重新排队中...")
                )
                await db.commit()
                try:
                    async_proofread_document.apply_async(
                        args=(existing.id,),
                        task_id=existing.task_id,
                    )
                except Exception as e:
                    logger.warning(f"[OpenAPI] 幂等重试投递失败: {e}")
            return await _existing_submit_response(db, existing)

    parsed_check_types = _parse_form_check_types(check_types)
    parsed_domain = _validate_form_domain(domain)

    if api_key is not None:
        await check_api_key_rpm(api_key)

    # 上传频率限制（JWT 调用此前不受任何频率限制）
    await check_upload_rate_limit(http_request, user)

    # ---- 文件校验 ----
    if not file.filename:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件名不能为空"})
    filename = sanitize_filename(file.filename)
    if not filename:
        raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件名不合法"})
    _, file_ext = os.path.splitext(filename)
    file_ext = file_ext.lower()
    if file_ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_FILE", "message": f"不支持的文件格式: {file_ext}，仅支持 .doc / .docx / .pdf / .txt"},
        )

    # ---- 分块落盘并校验内容（不把整个文件读进内存）----
    file_id = str(uuid.uuid4())
    try:
        stored = await store_upload(file, file_id, filename, file_ext)
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message})

    file_path = stored.file_path
    file_size = stored.file_size

    # 此后任何失败路径都不该在磁盘留下孤儿文件
    try:
        try:
            extracted_text = await asyncio.to_thread(extract_text_from_file, file_path, file_ext)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": str(e)})
        except Exception as e:
            logger.error(f"[OpenAPI] 文档文本提取失败: {e}")
            raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": "文档文本提取失败，请检查文件是否损坏"})
        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail={"code": "INVALID_FILE", "message": "文件中未提取到有效文本内容"})

        # ---- 配额（用户输入校验完成后才计费）----
        try:
            await _check_user_quota_contract(user, db)
        except HTTPException:
            raise
        if api_key is not None:
            await charge_api_key_daily(api_key, 1)
    except HTTPException:
        remove_upload_silently(file_path)
        raise
    except Exception:
        remove_upload_silently(file_path)
        raise

    # ---- 在同一事务中保存上传记录与持久任务 ----
    doc_record = UploadedDocument(
        file_id=file_id,
        filename=filename,
        file_ext=file_ext,
        file_size=file_size,
        file_path=file_path,
        text_length=len(extracted_text),
        extracted_text=extracted_text,
        user_id=user.id,
        username=user.username,
        owner_kind="user",
        status="uploaded",
    )
    task_uuid = str(uuid.uuid4())
    db_task = ProofreadTask(
        task_id=task_uuid,
        document_id=file_id,
        owner_kind="api_key" if api_key else "user",
        owner_user_id=user.id,
        owner_api_key_id=api_key.id if api_key else None,
        idempotency_key=scoped_idempotency_key,
        status="PENDING",
        progress=0,
        message="任务排队中...",
        params_json={
            "domain": parsed_domain,
            "config_id": config_id,
            "check_types": parsed_check_types,
        },
    )
    db.add_all([doc_record, db_task])
    try:
        await db.flush()
        db_task_pk_id = db_task.id
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        remove_upload_silently(file_path)
        if scoped_idempotency_key:
            existing = (await db.execute(
                select(ProofreadTask).where(ProofreadTask.idempotency_key == scoped_idempotency_key)
            )).scalar_one_or_none()
            if existing:
                return await _existing_submit_response(db, existing)
        logger.exception("[OpenAPI] 幂等任务记录冲突但未找到既有任务")
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": "任务记录保存失败，请重新提交"},
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"[OpenAPI] 上传/任务记录保存失败: {e}")
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        remove_upload_silently(file_path)
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": "任务记录保存失败，请重新提交"},
        )

    # ---- 投递 Celery（只传 DB 主键，worker 自行加载全文） ----
    try:
        async_proofread_document.apply_async(
            args=(db_task_pk_id,),
            task_id=task_uuid,
        )
    except Exception as e:
        logger.error(f"[OpenAPI] 任务队列不可用: {e}")
        await db.execute(
            update(ProofreadTask)
            .where(ProofreadTask.id == db_task_pk_id, ProofreadTask.status == "PENDING")
            .values(status="FAILURE", error_code="DISPATCH_FAILED", message="任务投递失败，请重试")
        )
        await db.commit()
        if api_key is not None:
            await refund_api_key_daily_usage(api_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "TASK_QUEUE_UNAVAILABLE", "message": "任务队列暂时不可用，请稍后重试"},
        )

    record_audit_log(
        http_request, "api_proofread_doc", user=user,
        input_text=extracted_text[:200],
        extra_params={
            "action": "submit",
            "text_length": len(extracted_text),
            "domain": parsed_domain,
            "check_types": parsed_check_types,
            "job_id": task_uuid,
        },
        file_id=file_id,
        file_name=filename,
        file_path=file_path,
        file_size=file_size,
    )

    return OpenDocumentSubmitResponse(
        job_id=task_uuid,
        filename=filename,
        text_length=len(extracted_text),
        status="queued",
        status_url=f"/api/v1/open/jobs/{task_uuid}",
    )


async def _load_db_task(job_id: str, db: AsyncSession):
    """从 proofread_tasks 加载任务记录"""
    from app.models.proofread_task import ProofreadTask
    result = await db.execute(
        select(ProofreadTask).where(ProofreadTask.task_id == job_id)
    )
    return result.scalar_one_or_none()


def _build_job_payload_from_db(job_id: str, db_task) -> OpenJobStatusResponse:
    """从 proofread_tasks 记录构造任务状态响应"""
    if db_task is None:
        return OpenJobStatusResponse(
            job_id=job_id, status="PENDING", progress=0, message="任务排队中...",
        )
    status = db_task.status
    if status in ("STARTED", "PROGRESS"):
        display_status = "PROGRESS"
    elif status == "SUCCESS":
        display_status = "SUCCESS"
    elif status in ("FAILURE", "CANCELLED", "REVOKED"):
        display_status = "FAILURE"
    else:
        display_status = status

    payload = OpenJobStatusResponse(
        job_id=job_id,
        status=display_status,
        progress=db_task.progress or 0,
        message=db_task.message or "",
    )
    if display_status == "SUCCESS" and db_task.result_json:
        data = dict(db_task.result_json)
        data.pop("user_id", None)
        payload.result = data or None
    elif display_status == "FAILURE":
        payload.error = db_task.error_code or "TASK_FAILED"
    return payload


@router.get(
    "/jobs/{job_id}",
    response_model=OpenJobStatusResponse,
    summary="查询任务状态",
    description=(
        "轮询异步文档审校任务：\n\n"
        "- `PENDING`：排队中（提交后立即轮询会出现）\n"
        "- `PROGRESS`：处理中，`progress` 为百分比\n"
        "- `SUCCESS`：完成，`result` 含 issues / total_issues / corrected_download_url（修订文档下载地址，相对路径，带签名有时效）\n"
        "- `FAILURE`：失败（`error=TASK_FAILED`），失败任务不消耗密钥当日配额\n\n"
        "建议轮询间隔 2-5 秒。"
    ),
    responses=JOBS_ERROR_RESPONSES,
)
async def open_get_job(
    job_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    auth: Tuple[User, Optional[ApiKey]] = Depends(get_current_user_or_apikey),
):
    """
    查询异步任务状态（仅任务提交者可查看，归属从 proofread_tasks 表校验）
    """
    user, api_key = auth

    db_task = await _load_db_task(job_id, db)
    if db_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
        )

    # 归属校验
    if db_task.owner_kind == "api_key":
        if api_key is None or db_task.owner_api_key_id != api_key.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
            )
    elif db_task.owner_kind == "user":
        if user.id != db_task.owner_user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"},
        )

    return _build_job_payload_from_db(job_id, db_task)
