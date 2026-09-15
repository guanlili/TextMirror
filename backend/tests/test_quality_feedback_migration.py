"""仅内存 SQLite 验证质量表迁移与外键；不运行任何工作区数据库升级。"""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.core.database import Base
from app.models.quality_feedback import QualityFeedback


def migration_for(connection):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/e9b3c7d5a102_add_quality_feedback.py"
    spec = importlib.util.spec_from_file_location("quality_feedback_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def test_quality_feedback_migration_defaults_unique_and_record_set_null():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE proofread_records (id INTEGER PRIMARY KEY, original_text TEXT NOT NULL)"))
        connection.execute(sa.text("INSERT INTO users VALUES (1)"))
        connection.execute(sa.text("INSERT INTO proofread_records VALUES (7, '不修改的原文')"))
        migration = migration_for(connection)
        assert migration.down_revision == "d8f2a6b4c901"
        migration.upgrade()
        migration.upgrade()
        table = sa.Table("quality_feedback", sa.MetaData(), autoload_with=connection)
        values = dict(record_id=7, user_id=1, dedupe_key="a" * 64, kind="preference", original="原文",
                      suggestion="", issue_type="typo", start=1, end=3, note="说明", context_text="不修改的原文",
                      context_start=0, domain="general", model_snapshot=[])
        connection.execute(table.insert().values(**values))
        row = connection.execute(sa.select(table)).mappings().one()
        assert row["status"] == "pending" and row["revision"] == 0 and row["sample"] is None
        assert row["review_note"] == "" and row["reviewer_id"] is None and row["reviewed_at"] is None
        assert row["created_at"] is not None and row["updated_at"] is not None
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(table.insert().values(**values))
        foreign_keys = sa.inspect(connection).get_foreign_keys("quality_feedback")
        assert {tuple(fk["constrained_columns"]) for fk in foreign_keys} == {("record_id",), ("user_id",), ("reviewer_id",)}
        for updates in ({"kind": "ignore"}, {"status": "confirmed"}, {"revision": -1}, {"start": 3},
                        {"status": "rejected", "sample": {"text": "不能保留"}}):
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(table.insert().values(**{**values, "dedupe_key": "b" * 64, **updates}))
        connection.execute(sa.text("DELETE FROM proofread_records WHERE id=7"))
        retained = connection.execute(sa.select(table)).mappings().one()
        assert retained["record_id"] is None and retained["context_text"] == "不修改的原文"
        migration.downgrade()
        migration.downgrade()
        assert not sa.inspect(connection).has_table("quality_feedback")
        assert sa.inspect(connection).has_table("users") and sa.inspect(connection).has_table("proofread_records")
    engine.dispose()


@pytest.mark.parametrize("bootstrap", [False, True])
def test_quality_feedback_migration_matches_orm_and_bootstrap(bootstrap):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        tables = [table for table in Base.metadata.sorted_tables if bootstrap or table is not QualityFeedback.__table__]
        Base.metadata.create_all(connection, tables=tables)
        migration = migration_for(connection)
        migration.upgrade()
        migration.upgrade()
        context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
        assert compare_metadata(context, Base.metadata) == []
        assert len(sa.inspect(connection).get_unique_constraints("quality_feedback")) == 1
        migration.downgrade()
        migration.upgrade()
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()
