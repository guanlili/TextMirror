"""Add independent fact-check configuration and snapshot-backed runs."""
import sqlalchemy as sa

from alembic import op

revision = "f1c4d8e6a203"
down_revision = "e9b3c7d5a102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("fact_check_config"):
        op.create_table(
            "fact_check_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("api_key", sa.Text(), nullable=False, server_default=sa.text("''")),
            sa.Column("max_claims", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("sources", sa.JSON(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_fact_check_config_singleton"),
            sa.CheckConstraint("max_claims BETWEEN 1 AND 10", name="ck_fact_check_max_claims"),
        )
    if not sa.inspect(op.get_bind()).has_table("fact_check_runs"):
        op.create_table(
            "fact_check_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("record_id", sa.Integer(), sa.ForeignKey("proofread_records.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("request_id", sa.String(36), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("source_hash", sa.String(64), nullable=False),
            sa.Column("source_text", sa.Text(), nullable=False),
            sa.Column("mode", sa.String(16), nullable=False),
            sa.Column("sources", sa.JSON(), nullable=False),
            sa.Column("config_id", sa.Integer(), nullable=False),
            sa.Column("max_claims", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.String(36), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("message", sa.String(500), nullable=False, server_default=sa.text("'等待核查'")),
            sa.Column("result_json", sa.JSON(none_as_null=True), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("user_id", "request_id", name="uq_fact_check_request"),
            sa.CheckConstraint("mode IN ('web', 'trusted')", name="ck_fact_check_mode"),
            sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILURE', 'CANCELLED')", name="ck_fact_check_status"),
            sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_fact_check_progress"),
        )
        op.create_index("ix_fact_check_runs_record_id", "fact_check_runs", ["record_id", "id"])
        op.create_index("ix_fact_check_runs_user_created", "fact_check_runs", ["user_id", "created_at"])


def downgrade() -> None:
    for table in ("fact_check_runs", "fact_check_config"):
        if sa.inspect(op.get_bind()).has_table(table):
            op.drop_table(table)
