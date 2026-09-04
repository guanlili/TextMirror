"""add index on proofread_records(user_id, created_at)

Revision ID: e5f1a2b7c934
Revises: c8e2d4f9a301
Create Date: 2026-09-04

配额检查（每次校对请求）按 (user_id, created_at) 统计当日记录，
无索引时全表扫描，记录量增大后拖慢所有校对请求。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e5f1a2b7c934'
down_revision: Union[str, None] = 'c8e2d4f9a301'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = 'ix_proofread_records_user_created'


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = [i['name'] for i in inspector.get_indexes('proofread_records')]
    if INDEX_NAME in existing:
        return
    op.create_index(INDEX_NAME, 'proofread_records', ['user_id', 'created_at'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = [i['name'] for i in inspector.get_indexes('proofread_records')]
    if INDEX_NAME not in existing:
        return
    op.drop_index(INDEX_NAME, table_name='proofread_records')
