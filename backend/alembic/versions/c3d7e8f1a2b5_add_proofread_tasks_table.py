"""add proofread_tasks table

Revision ID: c3d7e8f1a2b5
Revises: b2c7d5e93f14
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3d7e8f1a2b5'
down_revision: Union[str, None] = 'b2c7d5e93f14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "proofread_tasks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(64), nullable=False, comment="Celery task UUID"),
        sa.Column("document_id", sa.String(64), nullable=True, comment="uploaded_documents.file_id"),
        sa.Column("owner_kind", sa.String(10), nullable=False, server_default="user", comment="user/guest/api_key"),
        sa.Column("owner_user_id", sa.Integer, nullable=True, comment="登录用户 ID"),
        sa.Column("owner_api_key_id", sa.Integer, nullable=True, comment="开放 API 密钥 ID"),
        sa.Column("access_token_hash", sa.String(64), nullable=True, comment="游客访问令牌 SHA-256"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING",
                  comment="PENDING/STARTED/PROGRESS/SUCCESS/FAILURE/REVOKED/CANCELLED"),
        sa.Column("phase", sa.String(20), nullable=True, comment="upload/proofread/generate/save"),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=True, comment="幂等键"),
        sa.Column("result_json", sa.JSON, nullable=True, comment="校对结果 JSON"),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("output_path", sa.String(1000), nullable=True),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_proofread_tasks_task_id", "proofread_tasks", ["task_id"], unique=True)
    op.create_index("ix_proofread_tasks_idempotency", "proofread_tasks", ["idempotency_key"], unique=True)
    op.create_index("ix_proofread_tasks_document_id", "proofread_tasks", ["document_id"])
    op.create_index("ix_proofread_tasks_owner", "proofread_tasks", ["owner_kind", "owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_proofread_tasks_owner", table_name="proofread_tasks")
    op.drop_index("ix_proofread_tasks_document_id", table_name="proofread_tasks")
    op.drop_index("ix_proofread_tasks_idempotency", table_name="proofread_tasks")
    op.drop_index("ix_proofread_tasks_task_id", table_name="proofread_tasks")
    op.drop_table("proofread_tasks")
