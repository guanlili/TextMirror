from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class FactCheckConfig(BaseModel):
    __tablename__ = "fact_check_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_fact_check_config_singleton"),
        CheckConstraint("max_claims BETWEEN 1 AND 10", name="ck_fact_check_max_claims"),
        CheckConstraint("search_provider IN ('model', 'tavily')", name="ck_fact_check_config_search_provider"),
    )

    search_provider: Mapped[str] = mapped_column(String(16), nullable=False, default="model", server_default="model")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    max_claims: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    sources: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)


class FactCheckRun(BaseModel):
    __tablename__ = "fact_check_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_fact_check_request"),
        Index("ix_fact_check_runs_record_id", "record_id", "id"),
        Index("ix_fact_check_runs_user_created", "user_id", "created_at"),
        CheckConstraint("mode IN ('web', 'trusted')", name="ck_fact_check_mode"),
        CheckConstraint("status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILURE', 'CANCELLED')", name="ck_fact_check_status"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_fact_check_progress"),
        CheckConstraint("search_provider IN ('model', 'tavily')", name="ck_fact_check_run_search_provider"),
    )

    search_provider: Mapped[str] = mapped_column(String(16), nullable=False, default="model", server_default="model")
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("proofread_records.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    sources: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    config_id: Mapped[int] = mapped_column(Integer, nullable=False)
    max_claims: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    message: Mapped[str] = mapped_column(String(500), nullable=False, default="等待核查", server_default=text("'等待核查'"))
    result_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
