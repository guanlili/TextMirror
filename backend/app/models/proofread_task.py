"""
TextMirror 持久校对任务模型
任务提交时先写 DB 再投递 Celery，消除重复 LLM 调用并支持鉴权/取消/刷新恢复。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class ProofreadTask(BaseModel):
    """持久校对任务表"""
    __tablename__ = "proofread_tasks"
    __table_args__ = (
        Index("ix_proofread_tasks_task_id", "task_id", unique=True),
        Index("ix_proofread_tasks_idempotency", "idempotency_key", unique=True),
        Index("ix_proofread_tasks_document_id", "document_id"),
        Index("ix_proofread_tasks_owner", "owner_kind", "owner_user_id"),
    )

    task_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Celery task UUID"
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="uploaded_documents.file_id"
    )
    owner_kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default="user", comment="user / guest / api_key"
    )
    owner_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="登录用户 ID"
    )
    owner_api_key_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="开放 API 密钥 ID"
    )
    access_token_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="游客访问令牌 SHA-256 哈希"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING",
        comment="PENDING/STARTED/PROGRESS/RETRYING/SUCCESS/FAILURE/REVOKED/CANCELLED",
    )
    phase: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="upload/proofread/generate/save"
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="0-100"
    )
    message: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="当前阶段描述"
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="作用域化幂等键 SHA-256"
    )
    params_json: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="提交参数 JSON"
    )
    result_json: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="校对结果 JSON"
    )
    error_code: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="错误码"
    )
    output_path: Mapped[Optional[str]] = mapped_column(
        String(1000), nullable=True, comment="修订文档路径"
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
        comment="取消请求标志（worker 在阶段间协作退出）",
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="开始执行时间"
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="完成时间"
    )

    def __repr__(self):
        return f"<ProofreadTask(task_id={self.task_id}, status={self.status})>"
