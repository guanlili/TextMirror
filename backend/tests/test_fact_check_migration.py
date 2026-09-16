import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.core.database import Base
from app.models.fact_check import FactCheckConfig, FactCheckRun

ORIGINAL = "f1c4d8e6a203_add_fact_check"
SEARCH_PROVIDER = "a2d5e9f7b304_add_fact_check_search_provider"


def migration_for(connection, name=ORIGINAL):
    path = Path(__file__).resolve().parents[1] / f"alembic/versions/{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def create_parents(connection):
    connection.execute(sa.text("PRAGMA foreign_keys=ON"))
    connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    connection.execute(sa.text("CREATE TABLE proofread_records (id INTEGER PRIMARY KEY)"))
    connection.execute(sa.text("INSERT INTO users VALUES (1)"))
    connection.execute(sa.text("INSERT INTO proofread_records VALUES (1)"))


def run_values(**changes):
    return dict(record_id=1, user_id=1, request_id="request-1", request_hash="a" * 64, source_hash="b" * 64,
                source_text="原文", mode="trusted", sources=[], config_id=1, max_claims=10, task_id="task-1", **changes)


@pytest.mark.parametrize("bootstrap", [False, True])
def test_fact_check_migration_matches_orm_and_is_idempotent(bootstrap):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        tables = [table for table in Base.metadata.sorted_tables if bootstrap or table not in (FactCheckConfig.__table__, FactCheckRun.__table__)]
        Base.metadata.create_all(connection, tables=tables)
        original = migration_for(connection)
        provider = migration_for(connection, SEARCH_PROVIDER)
        assert original.down_revision == "e9b3c7d5a102"
        assert provider.revision == "a2d5e9f7b304" and provider.down_revision == "f1c4d8e6a203"
        original.upgrade()
        provider.upgrade()
        original.upgrade()
        provider.upgrade()
        context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
        assert compare_metadata(context, Base.metadata) == []
        provider.downgrade()
        provider.downgrade()
        assert "search_provider" not in {c["name"] for c in sa.inspect(connection).get_columns("fact_check_runs")}
        provider.upgrade()
        assert compare_metadata(context, Base.metadata) == []
        provider.downgrade()
        original.downgrade()
        original.downgrade()
        original.upgrade()
        provider.upgrade()
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()


@pytest.mark.parametrize("key,expected", [("encrypted:keep-this-secret", "tavily"), ("", "model")])
def test_provider_migration_legacy_backfill_preserves_records_and_downgrade(key, expected):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        create_parents(connection)
        migration_for(connection).upgrade()
        metadata = sa.MetaData()
        config = sa.Table("fact_check_config", metadata, autoload_with=connection)
        runs = sa.Table("fact_check_runs", metadata, autoload_with=connection)
        connection.execute(config.insert().values(id=1, api_key=key, enabled=bool(key), sources=[{"id": "official"}], max_claims=3))
        for index, status in enumerate(("PENDING", "RUNNING", "SUCCESS", "FAILURE", "CANCELLED")):
            values = run_values(status=status, result_json={"legacy": [status]}, error_code="KEEP")
            values["request_id"] = f"request-{index}"
            connection.execute(runs.insert().values(**values))
        original_config = dict(connection.execute(sa.select(config)).mappings().one())
        original_runs = [dict(row) for row in connection.execute(sa.select(runs).order_by(runs.c.id)).mappings()]
        migration = migration_for(connection, SEARCH_PROVIDER)
        migration.upgrade()
        migration.upgrade()
        config = sa.Table("fact_check_config", sa.MetaData(), autoload_with=connection)
        runs = sa.Table("fact_check_runs", sa.MetaData(), autoload_with=connection)
        migrated_config = dict(connection.execute(sa.select(config)).mappings().one())
        assert migrated_config.pop("search_provider") == expected
        assert migrated_config == original_config
        migrated_runs = [dict(row) for row in connection.execute(sa.select(runs).order_by(runs.c.id)).mappings()]
        assert all(row.pop("search_provider") == "tavily" for row in migrated_runs)
        assert migrated_runs == original_runs
        migration.downgrade()
        migration.downgrade()
        config = sa.Table("fact_check_config", sa.MetaData(), autoload_with=connection)
        runs = sa.Table("fact_check_runs", sa.MetaData(), autoload_with=connection)
        assert dict(connection.execute(sa.select(config)).mappings().one()) == original_config
        assert [dict(row) for row in connection.execute(sa.select(runs).order_by(runs.c.id)).mappings()] == original_runs
        assert "search_provider" not in runs.c and "search_provider" not in config.c
        migration.upgrade()
        config = sa.Table("fact_check_config", sa.MetaData(), autoload_with=connection)
        runs = sa.Table("fact_check_runs", sa.MetaData(), autoload_with=connection)
        assert dict(connection.execute(sa.select(config)).mappings().one()) == {**original_config, "search_provider": expected}
        assert [dict(row) for row in connection.execute(sa.select(runs).order_by(runs.c.id)).mappings()] == [
            {**row, "search_provider": "tavily"} for row in original_runs
        ]
    engine.dispose()


@pytest.mark.parametrize("selected,key", [("model", "retained-tavily-secret"), ("tavily", "")])
def test_provider_migration_bootstrap_preserves_existing_selections(selected, key):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        create_parents(connection)
        FactCheckConfig.__table__.create(connection)
        FactCheckRun.__table__.create(connection)
        connection.execute(FactCheckConfig.__table__.insert().values(id=1, api_key=key, search_provider=selected, sources=[]))
        connection.execute(FactCheckRun.__table__.insert().values(**run_values(search_provider=selected)))
        migration_for(connection).upgrade()
        provider = migration_for(connection, SEARCH_PROVIDER)
        provider.upgrade()
        provider.upgrade()
        assert connection.scalar(sa.select(FactCheckConfig.search_provider)) == selected
        assert connection.scalar(sa.select(FactCheckConfig.api_key)) == key
        assert connection.scalar(sa.select(FactCheckRun.search_provider)) == selected
    engine.dispose()


@pytest.mark.parametrize("native_status", ["PENDING", "RUNNING", "SUCCESS", "FAILURE", "CANCELLED", None])
@pytest.mark.parametrize("key", ["", "retained-tavily-secret"])
def test_provider_downgrade_refuses_native_state_before_any_schema_changes(native_status, key, monkeypatch):
    from datetime import datetime
    from unittest.mock import Mock

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        create_parents(connection)
        migration_for(connection).upgrade()
        provider = migration_for(connection, SEARCH_PROVIDER)
        provider.upgrade()
        config = sa.Table("fact_check_config", sa.MetaData(), autoload_with=connection)
        runs = sa.Table("fact_check_runs", sa.MetaData(), autoload_with=connection)
        connection.execute(config.insert().values(
            id=1, enabled=True, search_provider="tavily" if native_status else "model", api_key=key,
            sources=[{"id": "official"}], max_claims=3,
        ))
        finished_at = datetime(2026, 1, 1) if native_status in ("SUCCESS", "FAILURE", "CANCELLED") else None
        connection.execute(runs.insert().values(**run_values(
            search_provider="model" if native_status else "tavily", status=native_status or "PENDING",
            finished_at=finished_at, result_json={"keep": "historical attribution"},
        )))
        tables = (config, runs)
        rows_before = [connection.execute(sa.select(table)).mappings().all() for table in tables]
        schema_before = {
            table.name: (sa.inspect(connection).get_columns(table.name), sa.inspect(connection).get_check_constraints(table.name))
            for table in tables
        }
        alter = Mock(wraps=provider.op.batch_alter_table)
        monkeypatch.setattr(provider.op, "batch_alter_table", alter)
        for _ in range(2):
            with pytest.raises(RuntimeError, match="Cannot downgrade.*native/model.*provenance"):
                provider.downgrade()
            alter.assert_not_called()
            for table, expected_rows in zip(tables, rows_before):
                inspector = sa.inspect(connection)
                columns, constraints = schema_before[table.name]
                assert [column["name"] for column in inspector.get_columns(table.name)] == [column["name"] for column in columns]
                assert inspector.get_check_constraints(table.name) == constraints
                reflected = sa.Table(table.name, sa.MetaData(), autoload_with=connection)
                assert connection.execute(sa.select(reflected)).mappings().all() == expected_rows
        provider.upgrade()
        assert [connection.execute(sa.select(table)).mappings().all() for table in tables] == rows_before
    engine.dispose()


def test_run_constraints_and_source_record_deletion_cascade():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        create_parents(connection)
        migration_for(connection).upgrade()
        migration_for(connection, SEARCH_PROVIDER).upgrade()
        table = sa.Table("fact_check_runs", sa.MetaData(), autoload_with=connection)
        values = run_values()
        connection.execute(table.insert().values(**values))
        row = connection.execute(sa.select(table)).mappings().one()
        assert row["status"] == "PENDING" and row["progress"] == 0 and row["result_json"] is None
        assert row["search_provider"] == "model"
        for changes in ({}, {"request_id": "other", "mode": "unknown"}, {"request_id": "other", "progress": 101},
                        {"request_id": "other", "search_provider": "unknown"}, {"request_id": "other", "search_provider": None}):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(table.insert().values(**{**values, **changes}))
        config = sa.Table("fact_check_config", sa.MetaData(), autoload_with=connection)
        connection.execute(config.insert().values(id=1, sources=[]))
        assert connection.scalar(sa.select(config.c.search_provider)) == "model"
        for invalid in ("unknown", None):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(config.update().values(search_provider=invalid))
        connection.execute(sa.text("DELETE FROM proofread_records WHERE id=1"))
        assert connection.execute(sa.select(table)).all() == []
    engine.dispose()
