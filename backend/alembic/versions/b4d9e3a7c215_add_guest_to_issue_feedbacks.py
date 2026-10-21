"""add guest owner to issue feedbacks

Revision ID: b4d9e3a7c215
Revises: a3f8c2d6b901
Create Date: 2026-10-21
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4d9e3a7c215"
down_revision: Union[str, None] = "a3f8c2d6b901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("issue_feedbacks", "user_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "issue_feedbacks",
        sa.Column("guest_id", sa.String(length=64), nullable=True, comment="匿名访客ID（登录用户为null）"),
    )
    op.create_index("ix_issue_feedbacks_guest", "issue_feedbacks", ["guest_id"])


def downgrade() -> None:
    # 回滚后 user_id 恢复非空：删除只属于游客的反馈，避免迁移失败
    op.execute("DELETE FROM issue_feedbacks WHERE user_id IS NULL")
    op.drop_index("ix_issue_feedbacks_guest", table_name="issue_feedbacks")
    op.drop_column("issue_feedbacks", "guest_id")
    op.alter_column("issue_feedbacks", "user_id", existing_type=sa.Integer(), nullable=False)
