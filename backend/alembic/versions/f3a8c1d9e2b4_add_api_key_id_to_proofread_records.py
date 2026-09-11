"""add proofread_records.api_key_id

Revision ID: f3a8c1d9e2b4
Revises: c9d1e2f3a4b5
Create Date: 2026-09-11

开放 API 用量统计：校对记录归属到调用方密钥。
api_key_id 可空——Web 端（JWT/游客）调用为 NULL，仅开放 API 调用（文本/异步文档/润色）写入。
聚合查询走 (api_key_id, created_at) 复合索引；存量行全部为 NULL，无需回填。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3a8c1d9e2b4'
down_revision: Union[str, None] = 'c9d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proofread_records",
        sa.Column("api_key_id", sa.Integer(), nullable=True, comment="开放API密钥ID(非API调用为null)"),
    )
    op.create_foreign_key(
        "fk_proofread_records_api_key_id",
        "proofread_records",
        "api_keys",
        ["api_key_id"],
        ["id"],
    )
    op.create_index(
        "ix_proofread_records_apikey_created",
        "proofread_records",
        ["api_key_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_proofread_records_apikey_created", table_name="proofread_records")
    op.drop_constraint("fk_proofread_records_api_key_id", "proofread_records", type_="foreignkey")
    op.drop_column("proofread_records", "api_key_id")
