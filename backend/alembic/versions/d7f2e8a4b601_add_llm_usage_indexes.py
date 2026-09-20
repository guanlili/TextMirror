"""Add indexes on llm_usage for dashboard query performance.

Revision ID: d7f2e8a4b601
Revises: c4f7a9b1d526
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "d7f2e8a4b601"
down_revision: Union[str, None] = "c4f7a9b1d526"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "llm_usage"


def upgrade() -> None:
    inspector = op.get_bind().dialect
    existing = {
        idx["name"]
        for idx in inspector.get_indexes(TABLE)
    }
    if "ix_llm_usage_config_id" not in existing:
        op.create_index("ix_llm_usage_config_id", TABLE, ["config_id"])
    if "ix_llm_usage_business" not in existing:
        op.create_index("ix_llm_usage_business", TABLE, ["business"])
    if "ix_llm_usage_config_created" not in existing:
        op.create_index("ix_llm_usage_config_created", TABLE, ["config_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_config_created", TABLE)
    op.drop_index("ix_llm_usage_business", TABLE)
    op.drop_index("ix_llm_usage_config_id", TABLE)
