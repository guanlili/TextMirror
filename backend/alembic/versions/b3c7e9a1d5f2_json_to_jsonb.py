"""json -> jsonb for hot read paths

Revision ID: b3c7e9a1d5f2
Revises: d7f2e8a4b601
Create Date: 2026-10-19

PostgreSQL 的 json 类型以文本存储，每次 -> 路径提取都要重新解析整个文档；
历史列表单行提取 8 个路径、任务轮询反复读取 result_json，实测 jsonb 快约 8 倍。
SQLite（单测）仍使用通用 JSON 类型。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b3c7e9a1d5f2'
down_revision: Union[str, None] = 'd7f2e8a4b601'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (表名, 列名, 新默认值)
COLUMNS = [
    ("audit_logs", "extra_params", None),
    ("audit_logs", "token_usage", None),
    ("fact_check_config", "sources", None),
    ("fact_check_runs", "config_snapshot", "{}"),
    ("fact_check_runs", "result_json", None),
    ("fact_check_runs", "selected_claim_ids", None),
    ("fact_check_runs", "sources", None),
    ("fact_check_runs", "supplemental_urls", "[]"),
    ("proofread_records", "result", None),
    ("proofread_records", "review_state", None),
    ("proofread_records", "token_usage", None),
    ("proofread_tasks", "params_json", None),
    ("proofread_tasks", "result_json", None),
    ("quality_feedback", "model_snapshot", None),
    ("quality_feedback", "sample", None),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column, default in COLUMNS:
        # json 默认值无法自动转换为 jsonb，先移除再重建
        op.alter_column(table, column, server_default=None)
        op.alter_column(
            table, column,
            type_=postgresql.JSONB(astext_type=sa.Text()),
            postgresql_using=f"{column}::jsonb",
            existing_type=sa.JSON(),
        )
        if default is not None:
            op.alter_column(
                table, column,
                server_default=sa.text(f"'{default}'::jsonb"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column, default in COLUMNS:
        op.alter_column(table, column, server_default=None)
        op.alter_column(
            table, column,
            type_=sa.JSON(),
            postgresql_using=f"{column}::json",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
        )
        if default is not None:
            op.alter_column(
                table, column,
                server_default=sa.text(f"'{default}'::json"),
            )
