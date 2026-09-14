"""add index on uploaded_documents(created_at)

Revision ID: a7c3e9f1b8d2
Revises: b5d2f8a4e6c1
Create Date: 2026-09-13

后台文档管理列表按 created_at DESC 排序分页，
无索引时数据量增大后每次列表查询都是全表排序。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b8d2'
down_revision: Union[str, None] = 'b5d2f8a4e6c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = 'ix_uploaded_documents_created_at'


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = [i['name'] for i in inspector.get_indexes('uploaded_documents')]
    if INDEX_NAME in existing:
        return
    op.create_index(INDEX_NAME, 'uploaded_documents', ['created_at'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = [i['name'] for i in inspector.get_indexes('uploaded_documents')]
    if INDEX_NAME not in existing:
        return
    op.drop_index(INDEX_NAME, table_name='uploaded_documents')
