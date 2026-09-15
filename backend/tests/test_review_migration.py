"""仅在内存 SQLite 验证迁移，绝不运行工作区/生产数据库 upgrade。"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import Base


def migration_for(connection):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/d8f2a6b4c901_add_review_state_to_proofread_records.py"
    spec = importlib.util.spec_from_file_location("review_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def test_review_migration_existing_rows_and_idempotency():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE uploaded_documents (file_id VARCHAR(100) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE proofread_records (id INTEGER PRIMARY KEY, original_text TEXT NOT NULL)"))
        connection.execute(sa.text("INSERT INTO proofread_records VALUES (1, '完整旧原文')"))
        migration = migration_for(connection)
        migration.upgrade()
        migration.upgrade()
        record = connection.execute(sa.text("SELECT * FROM proofread_records")).mappings().one()
        assert record["original_text"] == "完整旧原文"
        assert record["review_revision"] == 0 and record["review_state"] is None and record["source_file_id"] is None
        assert sa.inspect(connection).get_foreign_keys("proofread_records")[0]["referred_table"] == "uploaded_documents"
        migration.downgrade()
        assert {column["name"] for column in sa.inspect(connection).get_columns("proofread_records")} == {"id", "original_text"}
    engine.dispose()


def test_review_migration_after_bootstrap_create_all():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        migration = migration_for(connection)
        migration.upgrade()
        migration.upgrade()
        foreign_keys = sa.inspect(connection).get_foreign_keys("proofread_records")
        assert len([fk for fk in foreign_keys if fk["constrained_columns"] == ["source_file_id"]]) == 1
        migration.downgrade()
        migration.upgrade()
        assert "review_state" in {column["name"] for column in sa.inspect(connection).get_columns("proofread_records")}
    engine.dispose()
