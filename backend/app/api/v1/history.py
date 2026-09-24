"""
TextMirror 校对历史记录 API
仅登录用户可查看自己的历史
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.proofread import ProofreadRecord
from app.schemas.history import HistoryDetailResponse, HistoryListItem, HistoryListResponse
from app.schemas.review import ReviewExportRequest, ReviewResponse, ReviewVersionRequest, ReviewWriteRequest
from app.services.audit_log import record_audit_log
from app.services.review import (
    export_review_file,
    history_review_summary,
    load_review_record,
    load_review_source,
    review_content,
    review_response,
    save_review,
)

router = APIRouter(prefix="/history", tags=["校对历史"])


@router.get("/{record_id}/review", response_model=ReviewResponse, summary="读取审阅草稿和版本")
async def get_review(record_id: int, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    record = await load_review_record(db, record_id, current_user.id)
    return review_response(record)


@router.put("/{record_id}/review", response_model=ReviewResponse, summary="按修订号保存审阅草稿")
async def put_review(
    record_id: int, request: ReviewWriteRequest,
    db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user),
):
    record = await load_review_record(db, record_id, current_user.id)
    return await save_review(db, record, current_user.id, request)


@router.post("/{record_id}/versions", response_model=ReviewResponse, summary="保存不可变审阅版本")
async def create_review_version(
    record_id: int, request: ReviewVersionRequest,
    db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user),
):
    record = await load_review_record(db, record_id, current_user.id)
    return await save_review(db, record, current_user.id, request, create_version=True, label=request.label)


@router.post("/{record_id}/export", summary="下载已保存草稿或版本（仅采纳项）")
async def export_history_review(
    record_id: int, request: ReviewExportRequest,
    db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user),
):
    record = await load_review_record(db, record_id, current_user.id)
    if request.revision != record.review_revision:
        raise ConflictError(code="REVIEW_STALE", message="审阅版本已更新，请重新加载后重试")
    review = review_response(record)
    if request.version_id is not None:
        version = next((version for version in review.versions if version.id == request.version_id), None)
        if version is None:
            raise NotFoundError(code="VERSION_NOT_FOUND", message="审阅版本不存在")
        issues = version.issues
    else:
        if record.review_state is None:
            raise ValidationError(code="NO_REVIEW_DRAFT", message="请先保存审阅草稿再导出")
        issues = review.issues
    source = await load_review_source(db, record, current_user.id) if request.format == "docx" else None
    return await export_review_file(
        record.original_text, issues, request.format, record.source_filename,
        original_path=source.file_path if source is not None else None,
        file_ext=source.file_ext if source is not None else "",
    )


@router.get("/usage", summary='获取今日使用量与配额')
async def get_today_usage(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """获取当前用户今日使用次数与配额（北京时间口径，与配额检查一致）"""
    # UTC 范围比较可走 (user_id, created_at) 复合索引；to_char(AT TIME ZONE) 会使索引失效
    from app.core.rate_limit import _shanghai_today_range

    day_start, day_end = _shanghai_today_range()
    result = await db.execute(
        select(func.count()).select_from(ProofreadRecord).where(
            ProofreadRecord.user_id == current_user.id,
            ProofreadRecord.created_at >= day_start,
            ProofreadRecord.created_at < day_end,
        )
    )
    used = result.scalar() or 0
    return {
        "used_today": used,
        "daily_quota": current_user.daily_quota,  # None = 不限
    }


@router.get("", response_model=HistoryListResponse, summary='获取当前用户的校对历史列表')
async def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type: Optional[str] = Query(None, description="记录类型: text/document/polish"),
    domain: Optional[str] = Query(None, description="领域"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """获取当前用户的校对历史列表"""
    r = ProofreadRecord
    filters = [r.user_id == current_user.id]
    if type:
        filters.append(r.type == type)
    if domain:
        filters.append(r.domain == domain)

    total = (await db.execute(select(func.count()).select_from(r).where(*filters))).scalar() or 0
    # 两次批量查询；只取预览与当前审阅字段，绝不加载正文、自动成稿或 review_state.versions。
    list_query = select(
        r.id, r.type, r.domain, r.total_issues, r.source_filename, r.token_usage, r.created_at,
        func.substr(r.original_text, 1, 101).label("preview"),
        r.result["issues"].label("result_issues"),
        r.result["coverage"].label("result_coverage"),
        r.result["compare"].as_boolean().label("result_compare"),
        r.result["results"].label("result_models"),
        r.result["collaboration"].label("collaboration"),
        r.review_state["issues"].label("saved_issues"),
        r.review_state["coverage"].label("saved_coverage"),
        r.review_state["compare"].label("saved_compare"),
    ).where(*filters).order_by(desc(r.created_at), desc(r.id)).offset((page - 1) * page_size).limit(page_size)
    records = (await db.execute(list_query)).mappings().all()
    items = []
    for row in records:
        result = {
            "coverage": row["result_coverage"], "compare": row["result_compare"],
            "results": row["result_models"], "collaboration": row["collaboration"],
        }
        if row["result_issues"] is not None:
            result["issues"] = row["result_issues"]
        state = None if row["saved_issues"] is None else {
            "issues": row["saved_issues"], "coverage": row["saved_coverage"], "compare": row["saved_compare"],
        }
        preview = row["preview"] or ""
        metadata = history_review_summary(result, state) if row["type"] in ("text", "document") else {}
        items.append(HistoryListItem(
            **{key: row[key] for key in ("id", "type", "domain", "total_issues", "source_filename", "token_usage", "created_at")},
            text_preview=preview[:100] + ("..." if len(preview) > 100 else ""),
            **metadata,
        ))

    return HistoryListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{record_id}", response_model=HistoryDetailResponse, summary='获取校对记录详情')
async def get_history_detail(
    record_id: int,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """获取校对记录详情"""
    result = await db.execute(
        select(ProofreadRecord).where(
            ProofreadRecord.id == record_id,
            ProofreadRecord.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise NotFoundError(code="RECORD_NOT_FOUND", message="记录不存在")

    # 记录审计日志（查看历史详情）
    record_audit_log(
        http_request, "view_history", user=current_user,
        extra_params={"record_id": record_id, "record_type": record.type},
    )

    content = review_content(record.result, record.review_state) if record.type in ("text", "document") else None
    metadata = history_review_summary(record.result, record.review_state, content=content) if content is not None else {}
    return HistoryDetailResponse(
        **metadata,
        issues=content[0] if content is not None else [],
        id=record.id,
        type=record.type,
        domain=record.domain,
        original_text=record.original_text,
        modified_text=record.modified_text,
        check_types=record.check_types,
        result=record.result,
        total_issues=record.total_issues,
        source_filename=record.source_filename,
        token_usage=record.token_usage,
        created_at=record.created_at,
    )


@router.delete("/{record_id}", status_code=204, summary='删除校对记录')
async def delete_history(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """删除校对记录"""
    result = await db.execute(
        select(ProofreadRecord).where(
            ProofreadRecord.id == record_id,
            ProofreadRecord.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise NotFoundError(code="RECORD_NOT_FOUND", message="记录不存在")

    await db.delete(record)
