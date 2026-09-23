"""
TextMirror 校对服务
负责文本分片、Prompt构建、调用大模型、解析结构化结果
"""
from loguru import logger

from app.core.database import async_session_factory
from app.core.secret_crypto import decrypt_secret
from app.services.consistency import check_consistency
from app.services.format_rules import check_format_rules
from app.services.llm.openai_compat import OpenAICompatProvider

from .cache import ChunkCache, _make_chunk_cache
from .chunking import (
    ChunkSpan,
    detect_domain,
    split_text_into_chunk_spans,
    split_text_into_chunks,
)
from .constants import (
    _DEEP_CHUNK_TIMEOUT_S,
    _DOMAIN_FEATURES,
    _SELF_CHECK_ERROR_THRESHOLD,
    _SELF_CHECK_MAX_CHUNK_CALLS,
    _SELF_CHECK_TEXT_LIMIT,
    _SHORT_FIELD_MAP,
    DOMAIN_MAP,
    DOMAIN_PROMPTS,
    PROOFREAD_SYSTEM_PROMPT,
    PROOFREAD_TYPES,
    PROOFREAD_USER_PROMPT,
    SELF_CHECK_PROMPT,
)
from .errors import (
    InvalidModelConfigError,
    InvalidProofreadResponse,
    ModelProofreadError,
)
from .locate import (
    _check_suggestion_effective,
    _filter_whitelist_issues,
    _normalize_for_match,
    locate_issues,
    verify_llm_issues,
)
from .orchestrator import (
    _gather_preparation,
    _report_chunk_done,
    _report_progress,
    proofread_text,
)
from .parsing import parse_proofread_result
from .prompts import _get_domain_rules, build_system_prompt
from .provider import get_llm_provider
from .scan import merge_issues, scan_words_deterministic
from .self_check import _needs_self_check, self_check_pass
from .words import _load_all_words, load_global_words, load_user_words

__all__ = [
    "ChunkCache", "_make_chunk_cache",
    "ChunkSpan", "split_text_into_chunk_spans", "split_text_into_chunks", "detect_domain",
    "PROOFREAD_TYPES", "DOMAIN_MAP", "DOMAIN_PROMPTS",
    "PROOFREAD_SYSTEM_PROMPT", "PROOFREAD_USER_PROMPT",
    "_DEEP_CHUNK_TIMEOUT_S", "_DOMAIN_FEATURES", "_SHORT_FIELD_MAP",
    "_SELF_CHECK_TEXT_LIMIT", "_SELF_CHECK_MAX_CHUNK_CALLS", "_SELF_CHECK_ERROR_THRESHOLD",
    "SELF_CHECK_PROMPT",
    "InvalidModelConfigError", "InvalidProofreadResponse", "ModelProofreadError",
    "get_llm_provider", "OpenAICompatProvider", "decrypt_secret", "async_session_factory",
    "load_global_words", "load_user_words", "_load_all_words",
    "build_system_prompt", "_get_domain_rules",
    "parse_proofread_result",
    "scan_words_deterministic", "merge_issues",
    "check_consistency", "check_format_rules",
    "proofread_text", "_gather_preparation", "_report_progress", "_report_chunk_done",
    "locate_issues", "verify_llm_issues",
    "_filter_whitelist_issues", "_normalize_for_match", "_check_suggestion_effective",
    "_needs_self_check", "self_check_pass",
    "logger",
]
