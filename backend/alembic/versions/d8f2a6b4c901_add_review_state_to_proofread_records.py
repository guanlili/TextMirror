"""Add source association and revisioned review drafts/snapshots.

Revision ID: d8f2a6b4c901
Revises: a7c3e9f1b8d2
"""
from alembic import op
import sqlalchemy as sa

revision = "d8f2a6b4c901"
down_revision = "a7c3e9f1b8d2"
branch_labels = None
depends_on = None

TABLE = "proofread_records"
FK = "fk_proofread_records_source_file_id"


def upgrade() -> None:
    # bootstrap creates current metadata before upgrade; don't add existing columns/FKs.
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    foreign_keys = inspector.get_foreign_keys(TABLE)
    with op.batch_alter_table(TABLE) as batch:
        if "source_file_id" not in columns:
            batch.add_column(sa.Column("source_file_id", sa.String(100), nullable=True))
        if "review_revision" not in columns:
            batch.add_column(sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0"))
        if "review_state" not in columns:
            batch.add_column(sa.Column("review_state", sa.JSON(), nullable=True))
        if not any(fk["constrained_columns"] == ["source_file_id"] for fk in foreign_keys):
            batch.create_foreign_key(FK, "uploaded_documents", ["source_file_id"], ["file_id"], ondelete="SET NULL")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    # Naming convention also handles the unnamed FK created by ORM bootstrap on SQLite.
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table(TABLE, naming_convention=naming) as batch:
        for fk in inspector.get_foreign_keys(TABLE):
            if fk["constrained_columns"] == ["source_file_id"]:
                batch.drop_constraint(fk["name"] or FK, type_="foreignkey")
        for column in ("review_state", "review_revision", "source_file_id"):
            if column in columns:
                batch.drop_column(column)
