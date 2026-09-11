"""add proofread_records.quota_weight

Revision ID: b5d2f8a4e6c1
Revises: a7e9d3f5c1b8
Create Date: 2026-09-11

对比端点用量口径：多模型对比此前只做配额预检不落记录——预检通过后配额
实际不消耗（可无限对比），用量统计也不含对比调用。本迁移引入权重列：
用户配额/用量统计/dashboard 从行数改为 SUM(quota_weight)，对比按成功
模型数落一条带权重的记录。存量行默认 1（语义不变）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b5d2f8a4e6c1'
down_revision: Union[str, None] = 'a7e9d3f5c1b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proofread_records",
        sa.Column(
            "quota_weight",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="配额消耗权重(多模型对比=成功模型数,其余=1)",
        ),
    )


def downgrade() -> None:
    op.drop_column("proofread_records", "quota_weight")
