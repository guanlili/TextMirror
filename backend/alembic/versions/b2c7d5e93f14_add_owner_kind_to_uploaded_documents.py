"""add owner_kind and deleted_at to uploaded_documents

Revision ID: b2c7d5e93f14
Revises: d4b8e2f10a67
Create Date: 2026-09-08

user_id IS NULL 此前同时表示「游客上传」和「上传者已被删号」，而归属校验把
两者都视为游客直接放行——删号后其文档反而变成任何人可访问。owner_kind 显式
区分三者；历史的 NULL owner 无法事后分辨来源，保守标记为 legacy 并拒绝访问
（需要重新上传），避免把删号遗留文档误当公开文档。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c7d5e93f14'
down_revision: Union[str, None] = 'd4b8e2f10a67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'uploaded_documents'
INDEX_NAME = 'ix_uploaded_documents_owner_kind'


def _columns(inspector) -> set:
    return {c['name'] for c in inspector.get_columns(TABLE)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = _columns(inspector)

    if 'owner_kind' not in existing:
        op.add_column(
            TABLE,
            sa.Column('owner_kind', sa.String(length=10), nullable=False, server_default='user',
                      comment='归属类型: user=登录用户 / guest=游客 / legacy=上传者已删号或来源不可考（拒绝访问）'),
        )
        # 历史数据：有 user_id 的是登录用户上传；NULL 的来源不可考，保守标 legacy
        op.execute(f"UPDATE {TABLE} SET owner_kind = 'user' WHERE user_id IS NOT NULL")
        op.execute(f"UPDATE {TABLE} SET owner_kind = 'legacy' WHERE user_id IS NULL")

    if 'deleted_at' not in existing:
        op.add_column(
            TABLE,
            sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True, comment='删除时间(软删除标记时间)'),
        )
        op.execute(f"UPDATE {TABLE} SET deleted_at = updated_at WHERE status = 'deleted'")

    if INDEX_NAME not in [i['name'] for i in inspector.get_indexes(TABLE)]:
        op.create_index(INDEX_NAME, TABLE, ['owner_kind'])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if INDEX_NAME in [i['name'] for i in inspector.get_indexes(TABLE)]:
        op.drop_index(INDEX_NAME, table_name=TABLE)
    existing = _columns(inspector)
    if 'deleted_at' in existing:
        op.drop_column(TABLE, 'deleted_at')
    if 'owner_kind' in existing:
        op.drop_column(TABLE, 'owner_kind')
