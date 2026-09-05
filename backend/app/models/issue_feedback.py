"""
TextMirror 审校建议反馈模型
记录用户对每条审校建议的接受/忽略行为——词库优化的数据飞轮起点
"""
from typing import Optional

from sqlalchemy import String, Integer, ForeignKey, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class IssueFeedback(BaseModel):
    """审校建议反馈表"""
    __tablename__ = "issue_feedbacks"
    __table_args__ = (
        # 高频查询：按记录聚合、按用户统计、按原文聚合（发现高频误报词）
        Index("ix_issue_feedbacks_record", "record_id"),
        Index("ix_issue_feedbacks_user", "user_id"),
        Index("ix_issue_feedbacks_original", "original"),
    )

    record_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("proofread_records.id", ondelete="SET NULL"),
        nullable=True, comment="校对记录ID（记录被删时保留反馈）"
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, comment="操作用户ID"
    )
    original: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="问题原文片段"
    )
    suggestion: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="修改建议"
    )
    issue_type: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="问题类型: typo/grammar/..."
    )
    action: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="动作: accept/ignore"
    )

    def __repr__(self):
        return f"<IssueFeedback(id={self.id}, action={self.action}, original={self.original[:20]})>"
