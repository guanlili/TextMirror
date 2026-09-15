"""登录提交独立质量候选；沿用全局词库编辑权限进行人工审核。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BeforeValidator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.schemas.quality_feedback import (
    FeedbackReviewRequest,
    FeedbackStatus,
    QualityFeedback,
    QualityFeedbackCreate,
    QualityFeedbackList,
)
from app.services.quality_feedback import create_quality_feedback, list_quality_feedback, review_quality_feedback

router = APIRouter(tags=["审校质量反馈"])


def _http_integer(value):
    # HTTP 参数本来是字符串，只接收十进制整数文本，不允许 1.0 / 1e0 等隐式转换。
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 10:
        return int(value)
    raise ValueError("必须使用十进制正整数")


HttpInteger = Annotated[int, BeforeValidator(_http_integer)]


@router.post("/proofread/quality-feedback", response_model=QualityFeedback)
async def submit_feedback(
    data: QualityFeedbackCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    return await create_quality_feedback(db, user.id, data)


@router.get("/admin/global-dict/quality-feedback", response_model=QualityFeedbackList)
async def list_feedback(
    status: FeedbackStatus | None = Query(None),
    page: Annotated[HttpInteger, Query(ge=1, le=2147483647)] = 1,
    page_size: Annotated[HttpInteger, Query(ge=1, le=50)] = 20,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("admin:global_dict:edit")),
):
    return await list_quality_feedback(db, status, page, page_size)


@router.put("/admin/global-dict/quality-feedback/{feedback_id}", response_model=QualityFeedback)
async def review_feedback(
    data: FeedbackReviewRequest,
    feedback_id: Annotated[HttpInteger, Path(ge=1, le=2147483647)],
    db: AsyncSession = Depends(get_db),
    user=Depends(require_permission("admin:global_dict:edit")),
):
    return await review_quality_feedback(db, feedback_id, user.id, data)
