"""
容器启动前的数据库引导：建表 + Alembic 版本对齐

历史上表由 create_all 建立、而迁移脚本又被 .dockerignore 排除在镜像外，
导致库里 alembic_version 为空：此时直接 upgrade head 会因表已存在而失败。
因此对未版本化的库先 stamp 到 head，再对已版本化的库做增量升级。
"""
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine

# 注册全部 ORM 元数据，确保 create_all 覆盖所有表
import app.models.api_key  # noqa: F401
import app.models.audit_log  # noqa: F401
import app.models.dictionary  # noqa: F401
import app.models.global_word  # noqa: F401
import app.models.issue_feedback  # noqa: F401
import app.models.llm_config  # noqa: F401
import app.models.proofread  # noqa: F401
import app.models.proofread_task  # noqa: F401
import app.models.role  # noqa: F401
import app.models.uploaded_document  # noqa: F401
import app.models.user  # noqa: F401
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from app.core.config import settings
from app.core.database import Base

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg2")
    engine = create_engine(sync_url)

    alembic_cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))

    try:
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
            versioned = bool(MigrationContext.configure(conn).get_current_heads())
    finally:
        engine.dispose()

    if versioned:
        command.upgrade(alembic_cfg, "head")
        logger.info("✅ 数据库迁移已升级至 head")
    else:
        command.stamp(alembic_cfg, "head")
        logger.info("✅ 数据库未版本化（create_all 建表），已标记至 head")


if __name__ == "__main__":
    main()
