"""add api_keys webhook columns

Revision ID: a7e9d3f5c1b8
Revises: f3a8c1d9e2b4
Create Date: 2026-09-11

API 密钥回调配置：webhook_url（异步文档任务完成/失败时 POST 通知）+
webhook_secret（Fernet 加密存储，HMAC-SHA256 请求签名）。存量行均为 NULL（不回调）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7e9d3f5c1b8'
down_revision: Union[str, None] = 'f3a8c1d9e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("webhook_url", sa.String(length=500), nullable=True, comment="异步任务完成回调地址(null=不回调)"),
    )
    op.add_column(
        "api_keys",
        sa.Column("webhook_secret", sa.String(length=300), nullable=True, comment="回调签名密文(Fernet加密, HMAC-SHA256签名用)"),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "webhook_secret")
    op.drop_column("api_keys", "webhook_url")
