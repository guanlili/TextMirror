"""add guest owner to proofread records

Revision ID: a3f8c2d6b901
Revises: b3c7e9a1d5f2
Create Date: 2026-10-21
"""
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "a3f8c2d6b901"
down_revision: Union[str, None] = "b3c7e9a1d5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proofread_records",
        sa.Column(
            "owner_kind", sa.String(length=10), server_default="user", nullable=False,
            comment="归属类型: user=登录用户/guest=匿名游客/api_key=开放API",
        ),
    )
    op.add_column(
        "proofread_records",
        sa.Column(
            "guest_id", sa.String(length=64), nullable=True,
            comment="匿名访客ID(登录用户与API调用为null)",
        ),
    )
    op.create_index(
        "ix_proofread_records_owner_created", "proofread_records", ["owner_kind", "created_at"],
    )
    op.create_index(
        "ix_proofread_records_guest_created", "proofread_records", ["guest_id", "created_at"],
    )
    op.add_column(
        "proofread_tasks",
        sa.Column("owner_guest_id", sa.String(length=64), nullable=True, comment="游客任务的匿名访客 ID"),
    )
    op.execute("UPDATE proofread_records SET owner_kind = 'api_key' WHERE api_key_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("proofread_tasks", "owner_guest_id")
    op.drop_index("ix_proofread_records_guest_created", table_name="proofread_records")
    op.drop_index("ix_proofread_records_owner_created", table_name="proofread_records")
    op.drop_column("proofread_records", "guest_id")
    op.drop_column("proofread_records", "owner_kind")
