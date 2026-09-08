"""add cancel_requested to proofread_tasks

Revision ID: a1b2c3d4e5f6
Revises: c3d7e8f1a2b5
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'c3d7e8f1a2b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proofread_tasks",
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default="false",
            comment="取消请求标志（worker 在阶段间协作退出）",
        ),
    )


def downgrade() -> None:
    op.drop_column("proofread_tasks", "cancel_requested")
