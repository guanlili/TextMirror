"""Add independent quality candidates and manually confirmed target samples.

Revision ID: e9b3c7d5a102
Revises: d8f2a6b4c901
"""
import sqlalchemy as sa

from alembic import op

revision = "e9b3c7d5a102"
down_revision = "d8f2a6b4c901"
branch_labels = None
depends_on = None

TABLE = "quality_feedback"


def upgrade() -> None:
    # Bootstrap may already have created the current ORM metadata.
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("proofread_records.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("original", sa.String(500), nullable=False),
        sa.Column("suggestion", sa.String(500), nullable=False),
        sa.Column("issue_type", sa.String(64), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("context_start", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("model_snapshot", sa.JSON(), nullable=False),
        sa.Column("sample", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("review_note", sa.String(1000), nullable=False, server_default=sa.text("''")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_quality_feedback_dedupe_key"),
        sa.CheckConstraint("revision >= 0", name="ck_quality_feedback_revision"),
        sa.CheckConstraint('"start" >= 0 AND "end" > "start"', name="ck_quality_feedback_span"),
        sa.CheckConstraint(
            "kind IN ('false_positive', 'bad_suggestion', 'preference', 'other', 'missed')",
            name="ck_quality_feedback_kind",
        ),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="ck_quality_feedback_status"),
        sa.CheckConstraint(
            "(status = 'confirmed' AND sample IS NOT NULL) OR "
            "(status IN ('pending', 'rejected') AND sample IS NULL)",
            name="ck_quality_feedback_sample",
        ),
    )
    op.create_index("ix_quality_feedback_status_id", TABLE, ["status", "id"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)
