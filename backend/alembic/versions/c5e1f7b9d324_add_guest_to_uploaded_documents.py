"""add guest owner to uploaded documents

Revision ID: c5e1f7b9d324
Revises: b4d9e3a7c215
Create Date: 2026-10-21
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e1f7b9d324"
down_revision: Union[str, None] = "b4d9e3a7c215"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "uploaded_documents",
        sa.Column("guest_id", sa.String(length=64), nullable=True, comment="游客上传归属的匿名访客ID"),
    )
    op.create_index(
        "ix_uploaded_documents_guest_created", "uploaded_documents", ["guest_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_uploaded_documents_guest_created", table_name="uploaded_documents")
    op.drop_column("uploaded_documents", "guest_id")
