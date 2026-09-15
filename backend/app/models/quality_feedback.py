"""质量候选独立于采纳/忽略事件；审核样例不依赖源记录生命周期。"""
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class QualityFeedback(BaseModel):
    __tablename__ = "quality_feedback"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_quality_feedback_dedupe_key"),
        Index("ix_quality_feedback_status_id", "status", "id"),
        CheckConstraint("revision >= 0", name="ck_quality_feedback_revision"),
        CheckConstraint('\"start\" >= 0 AND \"end\" > \"start\"', name="ck_quality_feedback_span"),
        CheckConstraint(
            "kind IN ('false_positive', 'bad_suggestion', 'preference', 'other', 'missed')",
            name="ck_quality_feedback_kind",
        ),
        CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="ck_quality_feedback_status"),
        CheckConstraint(
            "(status = 'confirmed' AND sample IS NOT NULL) OR "
            "(status IN ('pending', 'rejected') AND sample IS NULL)",
            name="ck_quality_feedback_sample",
        ),
    )

    record_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("proofread_records.id", ondelete="SET NULL"), nullable=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    reviewer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    original: Mapped[str] = mapped_column(String(500), nullable=False)
    suggestion: Mapped[str] = mapped_column(String(500), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    start: Mapped[int] = mapped_column(Integer, nullable=False)
    end: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    context_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_start: Mapped[int] = mapped_column(Integer, nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    model_snapshot: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    sample: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    review_note: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default=text("''"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
