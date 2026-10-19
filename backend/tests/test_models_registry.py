"""模型注册完整性：防止新增模型只在 app.main 导入、db_bootstrap 建表时漏表。"""
import app.models  # noqa: F401  触发统一注册
from app.core.database import Base

EXPECTED_TABLES = {
    "api_keys",
    "audit_logs",
    "dictionaries",
    "dictionary_entries",
    "fact_check_config",
    "fact_check_reviews",
    "fact_check_runs",
    "global_words",
    "issue_feedbacks",
    "llm_configs",
    "llm_usage",
    "permissions",
    "proofread_records",
    "proofread_tasks",
    "quality_feedback",
    "role_permissions",
    "roles",
    "uploaded_documents",
    "users",
    "whitelist_words",
}


def test_all_orm_tables_registered():
    assert EXPECTED_TABLES <= set(Base.metadata.tables)
