"""ORM 模型统一入口

统一导入全部模型，确保 Base.metadata 完整注册。
db_bootstrap（独立进程）与 FastAPI 启动都依赖此入口；
新增模型时只需在这里登记，避免只在一处导入导致建表缺表。
"""
from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.models.dictionary import Dictionary, DictionaryEntry, WhitelistWord
from app.models.fact_check import FactCheckConfig, FactCheckReview, FactCheckRun
from app.models.global_word import GlobalWord
from app.models.issue_feedback import IssueFeedback
from app.models.llm_config import LLMConfig
from app.models.llm_usage import LLMUsage
from app.models.proofread import ProofreadRecord
from app.models.proofread_task import ProofreadTask
from app.models.quality_feedback import QualityFeedback
from app.models.role import Permission, Role, RolePermission
from app.models.uploaded_document import UploadedDocument
from app.models.user import User

__all__ = [
    "ApiKey", "AuditLog", "Dictionary", "DictionaryEntry", "WhitelistWord",
    "FactCheckConfig", "FactCheckReview", "FactCheckRun", "GlobalWord",
    "IssueFeedback", "LLMConfig", "LLMUsage", "ProofreadRecord", "ProofreadTask",
    "QualityFeedback", "Permission", "Role", "RolePermission",
    "UploadedDocument", "User",
]
