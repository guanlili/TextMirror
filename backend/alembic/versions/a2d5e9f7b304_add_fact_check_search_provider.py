"""Prefer native model search while preserving legacy Tavily configuration and runs."""
import sqlalchemy as sa

from alembic import op

revision = "a2d5e9f7b304"
down_revision = "f1c4d8e6a203"
branch_labels = None
depends_on = None

TABLES = (
    ("fact_check_config", "ck_fact_check_config_search_provider"),
    ("fact_check_runs", "ck_fact_check_run_search_provider"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name, constraint_name in TABLES:
        inspector = sa.inspect(bind)
        has_column = any(column["name"] == "search_provider" for column in inspector.get_columns(table_name))
        has_constraint = any(item["name"] == constraint_name for item in inspector.get_check_constraints(table_name))
        if not has_column or not has_constraint:
            with op.batch_alter_table(table_name) as batch:
                if not has_column:
                    batch.add_column(sa.Column("search_provider", sa.String(16), nullable=False, server_default="model"))
                if not has_constraint:
                    batch.create_check_constraint(constraint_name, "search_provider IN ('model', 'tavily')")
        # Backfill only legacy schemas, preserving existing provider selections and snapshots.
        if not has_column:
            table = sa.table(table_name, sa.column("search_provider"), sa.column("api_key"))
            statement = table.update().values(search_provider="tavily")
            if table_name == "fact_check_config":
                statement = statement.where(table.c.api_key != "")
            bind.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name, _ in TABLES:
        if not inspector.has_table(table_name):
            continue
        if not any(column["name"] == "search_provider" for column in inspector.get_columns(table_name)):
            continue
        table = sa.table(table_name, sa.column("search_provider"), sa.column("enabled"))
        statement = sa.select(sa.literal(1)).select_from(table).where(table.c.search_provider == "model")
        if table_name == "fact_check_config":
            statement = statement.where(table.c.enabled.is_(True))
        if bind.scalar(statement.limit(1)) is not None:
            raise RuntimeError(
                "Cannot downgrade fact-check search provider: native/model runs or an enabled native/model "
                "configuration exist; removing search_provider would lose provider provenance."
            )
    for table_name, constraint_name in reversed(TABLES):
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            continue
        has_column = any(column["name"] == "search_provider" for column in inspector.get_columns(table_name))
        has_constraint = any(item["name"] == constraint_name for item in inspector.get_check_constraints(table_name))
        if has_column or has_constraint:
            with op.batch_alter_table(table_name) as batch:
                if has_constraint:
                    batch.drop_constraint(constraint_name, type_="check")
                if has_column:
                    batch.drop_column("search_provider")
