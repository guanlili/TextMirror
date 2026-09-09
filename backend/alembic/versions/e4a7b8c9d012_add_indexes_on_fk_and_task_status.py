"""add indexes on FK columns and task status

Revision ID: e4a7b8c9d012
Revises: a1b2c3d4e5f6
Create Date: 2026-09-09

PostgreSQL 不为外键自动创建索引。users.role_id、role_permissions.role_id/permission_id、
uploaded_documents.user_id 均为高频过滤列，缺少索引会导致 JOIN/WHERE 全表扫描。
proofread_tasks.status 是任务列表页的主要过滤条件，同样需要索引。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e4a7b8c9d012'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ('ix_users_role_id', 'users', ['role_id']),
    ('ix_role_permissions_role_id', 'role_permissions', ['role_id']),
    ('ix_role_permissions_permission_id', 'role_permissions', ['permission_id']),
    ('ix_uploaded_documents_user_id', 'uploaded_documents', ['user_id']),
    ('ix_proofread_tasks_status', 'proofread_tasks', ['status']),
]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, columns in INDEXES:
        if name not in [i['name'] for i in inspector.get_indexes(table)]:
            op.create_index(name, table, columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, _ in INDEXES:
        if name in [i['name'] for i in inspector.get_indexes(table)]:
            op.drop_index(name, table_name=table)
