"""add_issue_feedbacks_table

Revision ID: f7a3c1e8d560
Revises: e5f1a2b7c934
Create Date: 2026-09-05

审校建议反馈表（接受/忽略行为落库）——词库优化数据飞轮的起点。
幂等写法：表已由 create_all 建出时跳过。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f7a3c1e8d560'
down_revision: Union[str, None] = 'e5f1a2b7c934'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = 'issue_feedbacks'
INDEXES = [
    ('ix_issue_feedbacks_record', ['record_id']),
    ('ix_issue_feedbacks_user', ['user_id']),
    ('ix_issue_feedbacks_original', ['original']),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return

    op.create_table(TABLE_NAME,
    sa.Column('record_id', sa.Integer(), nullable=True, comment='校对记录ID（记录被删时保留反馈）'),
    sa.Column('user_id', sa.Integer(), nullable=False, comment='操作用户ID'),
    sa.Column('original', sa.String(length=500), nullable=False, comment='问题原文片段'),
    sa.Column('suggestion', sa.String(length=500), nullable=True, comment='修改建议'),
    sa.Column('issue_type', sa.String(length=20), nullable=True, comment='问题类型: typo/grammar/...'),
    sa.Column('action', sa.String(length=10), nullable=False, comment='动作: accept/ignore'),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='主键ID'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='更新时间'),
    sa.ForeignKeyConstraint(['record_id'], ['proofread_records.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    for name, cols in INDEXES:
        op.create_index(name, TABLE_NAME, cols, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    for name, _ in INDEXES:
        op.drop_index(name, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
