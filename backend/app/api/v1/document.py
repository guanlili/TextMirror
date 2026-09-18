"""
TextMirror 文档校对 API
支持上传 .doc / .docx / .pdf / .txt 文件进行校对
"""
import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from collections import OrderedDict
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, get_db
from app.core.dependencies import get_current_user_optional
from app.core.file_security import (
    safe_upload_path,
    sanitize_filename,
    verify_download_signature,
)
from app.core.rate_limit import (
    charge_user_daily_quota,
    check_guest_rate_limit,
    check_upload_rate_limit,
    reject_guest_if_disabled,
)
from app.core.security import derive_guest_task_access_token, hash_scoped_idempotency_key
from app.core.task_quota import refund_document_quota
from app.models.proofread import ProofreadRecord
from app.models.uploaded_document import UploadedDocument
from app.schemas.document import (
    DocumentExtractedTextResponse,
    DocumentProofreadRequest,
    DocumentProofreadResponse,
    DocumentUploadResponse,
    ExportReportRequest,
    ExportRevisedTextRequest,
)
from app.schemas.review import DocumentReviewExportRequest
from app.services.audit_log import record_audit_log
from app.services.document import extract_html_from_file, extract_text_from_file
from app.services.proofread import proofread_text
from app.services.review import export_review_file
from app.services.upload import UploadRejected, remove_upload_silently, store_upload
from app.tasks.proofread_task import async_proofread_document

router = APIRouter(prefix="/document", tags=["文档校对"])


def _normalize_idempotency_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key 必须为 1-128 个非空字符")
    return value


def _build_async_task_response(db_task) -> dict:
    response = {
        "task_id": db_task.task_id,
        "message": "校对任务已提交，请通过 task_id 查询进度",
    }
    if db_task.owner_kind == "guest":
        response["access_token"] = derive_guest_task_access_token(db_task.task_id)
    return response


# 允许的文件扩展名
ALLOWED_EXTENSIONS = {".doc", ".docx", ".pdf", ".txt"}

# 上传文件文本缓存：LRU 限容（entry 含全文提取文本，无淘汰会随上传量无限增长；
# 未命中时从 uploaded_documents 表回填，容量仅影响回填频率）
_UPLOAD_CACHE_MAX = 200
_uploaded_files_cache: OrderedDict = OrderedDict()
_upload_cache_lock = Lock()


def _cache_put(file_id: str, file_info: dict) -> None:
    with _upload_cache_lock:
        _uploaded_files_cache[file_id] = file_info
        _uploaded_files_cache.move_to_end(file_id)
        while len(_uploaded_files_cache) > _UPLOAD_CACHE_MAX:
            _uploaded_files_cache.popitem(last=False)


def _cache_get(file_id: str) -> Optional[dict]:
    with _upload_cache_lock:
        info = _uploaded_files_cache.get(file_id)
        if info is not None:
            _uploaded_files_cache.move_to_end(file_id)
        return info


def invalidate_document_cache(file_id: str) -> None:
    """清除指定文档的进程内缓存（后台删除文档时调用，避免删除后仍可凭缓存校对）"""
    with _upload_cache_lock:
        _uploaded_files_cache.pop(file_id, None)


async def _check_document_ownership(file_info: dict, current_user, db: AsyncSession) -> None:
    """
    校验文档归属：
      - legacy：上传者已删号或来源不可考，一律拒绝（此前这类文档因 user_id 为空
        被当作游客文档放行，等于删号后文档对所有人开放）
      - guest：游客上传，file_id 本身即访问凭证
      - user：仅本人及超管可访问
    """
    owner_kind = file_info.get("owner_kind") or ("user" if file_info.get("user_id") is not None else "legacy")
    not_found = HTTPException(status_code=404, detail="文件不存在或已过期，请重新上传")

    if owner_kind == "guest":
        return
    if owner_kind != "user":
        raise not_found

    owner_id = file_info.get("user_id")
    if owner_id is None or current_user is None:
        raise not_found
    if current_user.id == owner_id:
        return
    # 非本人：仅超级管理员放行
    from sqlalchemy import select as _select

    from app.models.role import Role
    result = await db.execute(_select(Role).where(Role.id == current_user.role_id))
    role = result.scalar_one_or_none()
    if not (role and role.code == "super_admin"):
        raise not_found


async def _load_document_info(file_id: str, db: AsyncSession) -> dict:
    """
    加载文档信息：始终先查数据库确认记录存在且未被删除，再复用内存缓存里的正文。
    （此前缓存命中会直接跳过状态检查，导致后台软删除后仍能继续校对该文档）
    """
    from sqlalchemy import select

    result = await db.execute(
        select(UploadedDocument).where(UploadedDocument.file_id == file_id)
    )
    doc_record = result.scalar_one_or_none()
    if doc_record is None or doc_record.status == "deleted":
        raise HTTPException(status_code=404, detail="文件不存在或已过期，请重新上传")

    cached = _cache_get(file_id)
    extracted_text = (cached or {}).get("text") or doc_record.extracted_text
    if not extracted_text:
        # 正文缺失：尝试从磁盘重新提取
        if not os.path.exists(doc_record.file_path):
            raise HTTPException(status_code=404, detail="文件已从服务器删除，请重新上传")
        try:
            extracted_text = await asyncio.to_thread(
                extract_text_from_file, doc_record.file_path, doc_record.file_ext
            )
        except Exception as e:
            logger.error(f"重新提取文本失败: {e}")
            raise HTTPException(status_code=500, detail="文本提取失败，请重新上传")

    file_info = {
        "filename": doc_record.filename,
        "file_path": doc_record.file_path,
        "file_ext": doc_record.file_ext,
        "file_size": doc_record.file_size,
        "text": extracted_text,
        "user_id": doc_record.user_id,
        "owner_kind": doc_record.owner_kind,
    }
    _cache_put(file_id, file_info)
    return file_info


@router.post("/{file_id}/export", summary="导出上传文档的采纳项（无需校对调用）")
async def export_document_review(
    file_id: str,
    request: DocumentReviewExportRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    if current_user is None:
        from app.services.guest_policy import get_guest_policy
        await reject_guest_if_disabled(http_request)
        if not (await get_guest_policy())["allow_upload"]:
            raise HTTPException(403, "当前未开放游客文档功能，请登录后使用")
    file_info = await _load_document_info(file_id, db)
    # 复用现有策略；apikey/legacy 文档绝不因 user_id 为空而当作游客文档。
    await _check_document_ownership(file_info, current_user, db)
    if request.format == "docx" and not os.path.isfile(file_info["file_path"]):
        raise HTTPException(410, "源文件已删除或过期；请导出 TXT")
    return await export_review_file(
        file_info["text"], request.issues, request.format, file_info["filename"],
        original_path=file_info["file_path"] if request.format == "docx" else None,
        file_ext=file_info["file_ext"],
    )


def _generate_revised_text_docx(text: str, output_path: str) -> None:
    """将修订文本写入 Word 文档"""
    from docx import Document
    document = Document()
    for line in text.split("\n"):
        document.add_paragraph(line)
    document.save(output_path)


def _generate_report_docx(data: ExportReportRequest, output_path: str) -> None:
    """将问题报告写入 Word 文档"""
    from docx import Document
    from docx.shared import RGBColor, Pt

    document = Document()

    # 标题
    title = document.add_heading(f"文档校对报告 - {data.filename}", level=1)
    title.runs[0].font.size = Pt(16)

    # 状态摘要
    status_map = {"completed": "已完成", "partial": "部分完成", "unknown": "未记录"}
    status_text = status_map.get(data.status, data.status)
    if data.status == "partial":
        status_text += "（不能视为全文无误）"

    document.add_paragraph(f"审校状态: {status_text}")
    document.add_paragraph(f"共发现 {data.total_issues} 个问题")
    document.add_paragraph(f"已接受: {data.accepted_count}  已忽略: {data.ignored_count}  待处理: {data.pending_count}")

    # 覆盖范围
    if data.coverage:
        document.add_paragraph(f"完成范围: {data.coverage.completed_chunks}/{data.coverage.total_chunks} 段")
        for chunk in data.coverage.failed_chunks:
            document.add_paragraph(f"未审范围: 第 {chunk.start + 1}–{chunk.end} 字（{chunk.error_code}）")

    document.add_paragraph("")

    # 问题列表
    severity_labels = {"critical": "严重", "major": "重要", "minor": "轻微", "suggestion": "建议"}
    type_labels = {
        "typo": "错别字", "grammar": "语法", "punctuation": "标点",
        "formatting": "格式", "terminology": "术语", "style": "风格",
        "fact": "事实", "other": "其他",
    }
    status_labels = {"accepted": "已接受", "ignored": "已忽略", "pending": "待处理"}

    for i, issue in enumerate(data.issues, 1):
        status_label = status_labels.get(issue.status, issue.status)
        type_label = type_labels.get(issue.type, issue.type)
        severity_label = severity_labels.get(issue.severity, issue.severity)

        # 问题标题行
        heading = document.add_paragraph()
        heading.add_run(f"{i}. [{status_label}] ").bold = True
        heading.add_run(f"[{type_label}] {severity_label}")

        # 位置
        if issue.context:
            document.add_paragraph(f"   位置: {issue.context}")

        # 原文
        original_para = document.add_paragraph()
        original_para.add_run("   原文: ").bold = True
        original_run = original_para.add_run(issue.original)
        original_run.font.color.rgb = RGBColor(0xCC, 0, 0)

        # 建议
        suggestion_para = document.add_paragraph()
        suggestion_para.add_run("   建议: ").bold = True
        suggestion_run = suggestion_para.add_run(issue.suggestion)
        suggestion_run.font.color.rgb = RGBColor(0, 0x80, 0)

        # 说明
        if issue.explanation:
            document.add_paragraph(f"   说明: {issue.explanation}")

        document.add_paragraph("")

    document.save(output_path)


@router.post("/{file_id}/export-revised-text", summary="导出修订文本为 Word")
async def export_revised_text(
    file_id: str,
    request: ExportRevisedTextRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """将前端编辑器的修订文本导出为 Word 文档"""
    if current_user is None:
        from app.services.guest_policy import get_guest_policy
        await reject_guest_if_disabled(http_request)
        if not (await get_guest_policy())["allow_upload"]:
            raise HTTPException(403, "当前未开放游客文档功能，请登录后使用")
    file_info = await _load_document_info(file_id, db)
    await _check_document_ownership(file_info, current_user, db)

    tmp_dir = tempfile.TemporaryDirectory(prefix="textmirror-revised-")
    output_path = os.path.join(tmp_dir.name, "revised.docx")
    try:
        await asyncio.to_thread(_generate_revised_text_docx, request.text, output_path)
        base = os.path.splitext(request.filename)[0] if "." in request.filename else request.filename
        name = sanitize_filename(f"{base}.docx")
        return FileResponse(
            output_path,
            filename=name,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            background=BackgroundTask(tmp_dir.cleanup),
        )
    except Exception as exc:
        tmp_dir.cleanup()
        raise HTTPException(500, f"Word 生成失败: {exc}") from exc


@router.post("/{file_id}/export-report", summary="导出问题报告为 Word")
async def export_report(
    file_id: str,
    request: ExportReportRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """将问题报告导出为 Word 文档"""
    if current_user is None:
        from app.services.guest_policy import get_guest_policy
        await reject_guest_if_disabled(http_request)
        if not (await get_guest_policy())["allow_upload"]:
            raise HTTPException(403, "当前未开放游客文档功能，请登录后使用")
    file_info = await _load_document_info(file_id, db)
    await _check_document_ownership(file_info, current_user, db)

    tmp_dir = tempfile.TemporaryDirectory(prefix="textmirror-report-")
    output_path = os.path.join(tmp_dir.name, "report.docx")
    try:
        await asyncio.to_thread(_generate_report_docx, request, output_path)
        base = os.path.splitext(request.filename)[0] if "." in request.filename else request.filename
        name = sanitize_filename(f"{base}.docx")
        return FileResponse(
            output_path,
            filename=name,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            background=BackgroundTask(tmp_dir.cleanup),
        )
    except Exception as exc:
        tmp_dir.cleanup()
        raise HTTPException(500, f"Word 生成失败: {exc}") from exc


@router.post("/upload", response_model=DocumentUploadResponse, summary='上传文档并提取文本')
async def upload_document(
    file: UploadFile = File(...),
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """
    上传文档并提取文本
    返回文件 ID 和文本预览，供后续校对使用
    """
    # 游客模式关闭时拒绝游客上传
    if current_user is None:
        from app.services.guest_policy import get_guest_policy
        await reject_guest_if_disabled(http_request)
        if not (await get_guest_policy())["allow_upload"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前未开放游客上传文档，请登录后使用",
            )

    # 上传频率限制（避免反复上传只做解析落盘，绕过按校对次数计的配额）
    await check_upload_rate_limit(http_request, current_user)

    # 校验文件名（净化后使用，防路径穿越）
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    filename = sanitize_filename(file.filename)
    if not filename:
        raise HTTPException(status_code=400, detail="文件名不合法")

    # 获取扩展名
    _, file_ext = os.path.splitext(filename)
    file_ext = file_ext.lower()

    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {file_ext}，仅支持 .doc / .docx / .pdf / .txt",
        )

    # 生成文件 ID，分块落盘并校验内容（不把整个文件读进内存）
    file_id = str(uuid.uuid4())
    try:
        stored = await store_upload(file, file_id, filename, file_ext)
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail=e.message)

    file_path = stored.file_path
    file_size = stored.file_size

    # 落盘之后的任何失败都不该在磁盘留下孤儿文件
    try:
        # 提取文本（同步解析放线程池：.doc 走 LibreOffice subprocess，最长阻塞 60s）
        try:
            extracted_text = await asyncio.to_thread(extract_text_from_file, file_path, file_ext)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"文本提取失败: {e}")
            raise HTTPException(status_code=500, detail="文件文本提取失败，请检查文件是否损坏")

        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail="文件中未提取到有效文本内容")

        text_preview = extracted_text[:200] + ("..." if len(extracted_text) > 200 else "")

        # 提取格式化 HTML（保留排版和字体样式）
        extracted_html = ""
        try:
            extracted_html = await asyncio.to_thread(extract_html_from_file, file_path, file_ext, extracted_text)
        except Exception as e:
            logger.warning(f"HTML格式提取失败，将降级使用纯文本: {e}")

        # 写入数据库记录：失败则整体失败，避免出现磁盘有文件而无记录可追溯的状态
        try:
            doc_record = UploadedDocument(
                file_id=file_id,
                filename=filename,
                file_ext=file_ext,
                file_size=file_size,
                file_path=file_path,
                text_length=len(extracted_text),
                extracted_text=extracted_text,
                user_id=current_user.id if current_user else None,
                username=current_user.username if current_user else None,
                owner_kind="user" if current_user else "guest",
                status="uploaded",
            )
            db.add(doc_record)
            await db.commit()
        except Exception as e:
            logger.error(f"保存文档上传记录失败: {e}")
            raise HTTPException(status_code=500, detail="上传记录保存失败，请重新上传")
    except Exception:
        remove_upload_silently(file_path)
        raise

    # 缓存提取的文本和文件信息
    _cache_put(file_id, {
        "filename": filename,
        "file_path": file_path,
        "file_ext": file_ext,
        "file_size": file_size,
        "text": extracted_text,
        "user_id": current_user.id if current_user else None,
        "owner_kind": "user" if current_user else "guest",
    })

    logger.info(f"文档上传成功: {filename}, 文本长度={len(extracted_text)}")

    # 记录审计日志（文档上传）
    if http_request:
        record_audit_log(
            http_request, "proofread_doc", user=current_user,
            input_text=text_preview,
            extra_params={"action": "upload", "text_length": len(extracted_text)},
            file_id=file_id,
            file_name=filename,
            file_path=file_path,
            file_size=file_size,
        )

    return DocumentUploadResponse(
        file_id=file_id,
        filename=filename,
        file_size=file_size,
        file_ext=file_ext,
        text_length=len(extracted_text),
        text_preview=text_preview,
    )


@router.get("/{file_id}/extracted-text", response_model=DocumentExtractedTextResponse, summary='获取文档提取的文本')
async def get_extracted_text(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """
    按 file_id 获取上传时提取的全文，供前端会话恢复使用。
    归属校验：登录用户只能取自己的文件，游客文件仅同 IP 可取。
    """
    file_info = await _load_document_info(file_id, db)
    await _check_document_ownership(file_info, current_user, db)

    extracted_html = ""
    try:
        extracted_html = await asyncio.to_thread(
            extract_html_from_file, file_info["file_path"], file_info["file_ext"], file_info["text"]
        )
    except Exception as e:
        logger.warning(f"HTML格式提取失败，将降级使用纯文本: {e}")

    return DocumentExtractedTextResponse(
        file_id=file_id,
        extracted_text=file_info["text"],
        extracted_html=extracted_html,
    )


@router.post("/proofread", response_model=DocumentProofreadResponse, summary='对已上传的文档执行校对')
async def document_proofread(
    request: DocumentProofreadRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_optional),
):
    """
    对已上传的文档执行校对
    需要先调用 /upload 获取 file_id
    """
    # 加载文档（先校验记录存在且未删除，再复用缓存正文）
    file_info = await _load_document_info(request.file_id, db)

    # 归属校验先于预扣，未获授权的请求不消耗额度。
    await _check_document_ownership(file_info, current_user, db)

    quota_key = None
    refund_id = str(uuid.uuid4())
    if current_user is None:
        await reject_guest_if_disabled(http_request)
        await check_guest_rate_limit(http_request)
    else:
        quota_key = await charge_user_daily_quota(current_user)

    text = file_info["text"]
    filename = file_info["filename"]

    # 调用校对服务（同步路径加超时保护，避免 LLM 卡住时请求无限挂起）
    try:
        async with asyncio.timeout(120):
            result = await proofread_text(
                text=text,
                domain=request.domain,
                config_id=request.config_id,
                user_id=current_user.id if current_user else None,
                depth=request.depth,
            )
    except TimeoutError:
        logger.warning(f"文档同步校对超时(120s): {filename}, text_length={len(text)}")
        await refund_document_quota(refund_id, quota_key)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="校对耗时过长，请改用异步校对或缩小文档范围",
        )
    except RuntimeError as e:
        import traceback
        logger.error(f"文档校对服务异常: {e}\n{traceback.format_exc()}")
        await refund_document_quota(refund_id, quota_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="校对服务暂时不可用，请稍后重试",
        )
    except Exception as e:
        import traceback
        logger.error(f"文档校对未知错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        await refund_document_quota(refund_id, quota_key)
        raise HTTPException(status_code=500, detail="校对过程发生错误")

    # Web 审阅前不再自动采纳全部建议；用户决定后通过私有 export 接口下载。
    corrected_url = None

    # 保存校对记录
    record_id = None
    if current_user:
        record = ProofreadRecord(
            user_id=current_user.id,
            type="document",
            source_file_id=request.file_id,
            original_text=text,
            check_types=json.dumps(request.check_types or []),
            domain=request.domain,
            result=result,
            total_issues=result["total_issues"],
            token_usage=result["usage"],
            source_filename=filename,
        )
        db.add(record)
        await db.flush()
        record_id = record.id

    logger.info(f"文档校对完成: {filename}, 问题数={result['total_issues']}")

    # 记录审计日志（文档校对）
    record_audit_log(
        http_request, "proofread_doc", user=current_user,
        input_text=text[:500],
        output_text=f"发现{result['total_issues']}个问题",
        extra_params={
            "action": "proofread",
            "check_types": request.check_types,
            "domain": request.domain,
            "total_issues": result["total_issues"],
        },
        file_id=request.file_id,
        file_name=filename,
        file_path=file_info.get("file_path"),
        file_size=file_info.get("file_size"),
        token_usage=result.get("usage"),
    )

    return DocumentProofreadResponse(
        file_id=request.file_id,
        filename=filename,
        issues=result["issues"],
        total_issues=result["total_issues"],
        chunks_count=result["chunks_count"],
        usage=result["usage"],
        domain=result["domain"],
        record_id=record_id,
        corrected_download_url=corrected_url,
        coverage=result.get("coverage"),
        depth=result.get("depth", "standard"),
        config_id=result.get("config_id"),
        check_types=result.get("check_types", []),
    )


@router.post("/proofread/async", summary='异步文档校对 - 提交 Celery 任务')
async def document_proofread_async(
    request: DocumentProofreadRequest,
    http_request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user=Depends(get_current_user_optional),
):
    """
    异步文档校对 - 先写 proofread_tasks 再投递 Celery（broker 消息不含全文）。
    返回 task_id；游客额外返回 access_token 用于后续状态查询鉴权。
    """
    raw_idempotency_key = _normalize_idempotency_key(idempotency_key)

    async with async_session_factory() as db:
        file_info = await _load_document_info(request.file_id, db)
        await _check_document_ownership(file_info, current_user, db)

    owner_kind = "user" if current_user else "guest"
    owner_scope = f"web:user:{current_user.id}" if current_user else f"web:guest-document:{request.file_id}"
    scoped_idempotency_key = (
        hash_scoped_idempotency_key(owner_scope, raw_idempotency_key)
        if raw_idempotency_key else None
    )

    from app.models.proofread_task import ProofreadTask

    if scoped_idempotency_key:
        async with async_session_factory() as db:
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
                        logger.warning(f"幂等重试投递失败: {e}")
                return _build_async_task_response(existing)

    task_uuid = str(uuid.uuid4())
    quota_key = None
    if current_user is None:
        await reject_guest_if_disabled(http_request)
        await check_guest_rate_limit(http_request)
    else:
        # 保存实际预扣日；不限额/Redis 未扣成功均为 None。
        quota_key = await charge_user_daily_quota(current_user)

    access_token_hash = None
    if owner_kind == "guest":
        access_token_hash = hashlib.sha256(
            derive_guest_task_access_token(task_uuid).encode()
        ).hexdigest()

    async with async_session_factory() as db:
        db_task = ProofreadTask(
            task_id=task_uuid,
            document_id=request.file_id,
            owner_kind=owner_kind,
            owner_user_id=current_user.id if current_user else None,
            access_token_hash=access_token_hash,
            idempotency_key=scoped_idempotency_key,
            status="PENDING",
            progress=0,
            message="任务排队中...",
            params_json={
                "domain": request.domain,
                "config_id": request.config_id,
                "check_types": request.check_types,
                "depth": request.depth,
                "review_before_export": True,
                "quota_key": quota_key,
            },
        )
        db.add(db_task)
        try:
            await db.commit()
            await db.refresh(db_task)
        except IntegrityError:
            await db.rollback()
            # 并发幂等提交的输家也有预扣，按其自己的 UUID 退还，不影响胜者。
            await refund_document_quota(task_uuid, quota_key)
            if scoped_idempotency_key:
                existing = (await db.execute(
                    select(ProofreadTask).where(ProofreadTask.idempotency_key == scoped_idempotency_key)
                )).scalar_one_or_none()
                if existing:
                    return _build_async_task_response(existing)
            raise
        except Exception:
            await db.rollback()
            await refund_document_quota(task_uuid, quota_key)
            raise
        db_task_pk_id = db_task.id

    try:
        async_proofread_document.apply_async(
            args=(db_task_pk_id,),
            task_id=task_uuid,
        )
    except Exception as e:
        logger.error(f"异步任务投递失败: {e}")
        async with async_session_factory() as db:
            failed = await db.execute(
                update(ProofreadTask)
                .where(ProofreadTask.id == db_task_pk_id, ProofreadTask.status == "PENDING")
                .values(status="FAILURE", error_code="DISPATCH_FAILED", message="任务投递失败，请重试")
            )
            await db.commit()
        # broker 确认丢失时任务可能已执行；只有 CAS 成功才退。
        # 保留现有幂等重投不再扣费策略，重投后取消/失败也只能退同一笔预扣。
        if failed.rowcount:
            await refund_document_quota(task_uuid, quota_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="任务队列暂时不可用，请稍后重试",
        )

    logger.info(f"异步校对任务已提交: task_id={task_uuid}, file={file_info['filename']}")
    return _build_async_task_response(db_task)


@router.get("/download/{file_id}/{filename:path}", summary='签名下载：校验 HMAC 签名与有效期后返回上传目录内的文件')
async def download_file(
    file_id: str,
    filename: str,
    expires: int = 0,
    signature: str = "",
    db: AsyncSession = Depends(get_db),
):
    """
    签名下载：校验 HMAC 签名与有效期后返回上传目录内的文件
    （替代原先无鉴权的 /uploads/ 静态挂载）
    """
    filename = sanitize_filename(filename)
    if not verify_download_signature(file_id, filename, expires, signature):
        raise HTTPException(status_code=403, detail="下载链接无效或已过期，请重新校对生成")

    try:
        file_path = safe_upload_path(file_id, filename)
    except ValueError:
        raise HTTPException(status_code=403, detail="下载链接无效或已过期，请重新校对生成")

    # 已删除的文档不得凭旧签名继续下载（签名有效期 24h，删除后不应仍可取回）
    from sqlalchemy import select
    result = await db.execute(
        select(UploadedDocument.status).where(UploadedDocument.file_id == file_id)
    )
    doc_status = result.scalar_one_or_none()
    if doc_status == "deleted":
        raise HTTPException(status_code=404, detail="文件不存在或已被清理")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="文件不存在或已被清理")

    return FileResponse(path=file_path, filename=filename)
