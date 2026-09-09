"""add users.password_changed_at

Revision ID: c9d1e2f3a4b5
Revises: e4a7b8c9d012
Create Date: 2026-09-09

密码变更使旧 Token 失效：get_current_user / refresh 校验 JWT iat 与
password_changed_at，早于变更时间签发的 Token 拒绝。
存量行 backfill now()——部署后既有会话一次性全部失效，用户重新登录一次
（安全默认：避免历史行 NULL 永不失效）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c9d1e2f3a4b5'
down_revision: Union[str, None] = 'e4a7b8c9d012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="密码设定/变更时间（早于此时间签发的Token失效）",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
