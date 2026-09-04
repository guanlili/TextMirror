"""add_api_keys_table

Revision ID: c8e2d4f9a301
Revises: b7c4e9f21a3d
Create Date: 2026-09-04

开放平台 API 密钥表。
注意：幂等写法（表已由 create_all 建出的环境直接跳过），
因为 init_db 的 create_all 会在应用启动时自动建新表，
此迁移保证 alembic 迁移链完整，后续对该表的变更可正常 autogenerate。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c8e2d4f9a301'
down_revision: Union[str, None] = 'b7c4e9f21a3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = 'api_keys'


def _table_exists(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, TABLE_NAME):
        return

    op.create_table(TABLE_NAME,
    sa.Column('user_id', sa.Integer(), nullable=False, comment='归属用户ID'),
    sa.Column('name', sa.String(length=100), nullable=False, comment='密钥名称（用途备注）'),
    sa.Column('key_prefix', sa.String(length=20), nullable=False, comment='密钥前缀（明文展示用）'),
    sa.Column('key_suffix', sa.String(length=8), nullable=False, comment='密钥后4位（明文展示用）'),
    sa.Column('key_hash', sa.String(length=64), nullable=False, comment='密钥SHA-256哈希'),
    sa.Column('daily_quota', sa.Integer(), nullable=True, comment='密钥每日调用上限(null=跟随用户配额)'),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True, comment='过期时间(null=永不过期)'),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True, comment='最近使用时间'),
    sa.Column('is_active', sa.Boolean(), nullable=False, comment='是否有效（吊销后为False）'),
    sa.Column('remark', sa.String(length=500), nullable=True, comment='备注'),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False, comment='主键ID'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='更新时间'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_api_keys_user_id', TABLE_NAME, ['user_id'], unique=False)
    op.create_index('ix_api_keys_key_hash', TABLE_NAME, ['key_hash'], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _table_exists(inspector, TABLE_NAME):
        return
    op.drop_index('ix_api_keys_key_hash', table_name=TABLE_NAME)
    op.drop_index('ix_api_keys_user_id', table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
