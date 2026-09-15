"""
TextMirror 校对历史 Schema
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.review import ReviewIssue


class HistoryReviewSummary(BaseModel):
    """已保存草稿（未保存时为原始报告）的去重问题数，不是补丁数。"""
    total: int = 0
    accepted: int = 0
    ignored: int = 0
    pending: int = 0
    failed_models: int = 0


class HistoryReviewMetadata(BaseModel):
    mode: Literal["single", "compare", "collaboration"] | None = None
    coverage_status: Literal["complete", "partial", "unknown"] = "unknown"
    review_summary: HistoryReviewSummary = Field(default_factory=HistoryReviewSummary)


class HistoryListItem(HistoryReviewMetadata):
    """历史列表项"""
    id: int
    type: str
    domain: str
    total_issues: int
    text_preview: str
    source_filename: Optional[str] = None
    token_usage: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class HistoryListResponse(BaseModel):
    """历史列表响应（分页）"""
    items: List[HistoryListItem]
    total: int
    page: int
    page_size: int


class HistoryDetailResponse(HistoryReviewMetadata):
    """历史详情响应；issues 使用与继续审阅相同的合并及用户决策口径。"""
    issues: list[ReviewIssue] = Field(default_factory=list)
    id: int
    type: str
    domain: str
    original_text: str
    modified_text: Optional[str] = None
    check_types: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    total_issues: int
    source_filename: Optional[str] = None
    token_usage: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
