"""llm_configs 活跃配置唯一索引（防并发激活产生多个 is_active=True）

Revision ID: b7c4e9f21a3d
Revises: 96739972ca65
Create Date: 2026-09-03

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b7c4e9f21a3d'
down_revision = '96739972ca65'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 部分唯一索引：is_active=True 的行全局仅允许一条
    op.create_index(
        'uq_llm_config_single_active',
        'llm_configs',
        ['is_active'],
        unique=True,
        postgresql_where=op.text('is_active IS TRUE'),
    )


def downgrade() -> None:
    op.drop_index('uq_llm_config_single_active', table_name='llm_configs')
