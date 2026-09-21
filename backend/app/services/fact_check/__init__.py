"""Bounded, evidence-only fact checking. The caller owns the LLM provider lifecycle."""

import asyncio

import httpx
from loguru import logger

from app.schemas.fact_check import FactJudgmentEvidence
from app.services.fact_check_search import NativeSearchError, SearchResult

from .constants import (
    EXTRACTION_PROMPT,
    FETCH_TIMEOUT,
    JUDGMENT_PROMPT,
    MAX_BYTES,
    MAX_EXTRACTED,
    MAX_PAGE_TEXT,
    MAX_REDIRECTS,
    MODEL_TIMEOUT,
    QUOTE_CONTEXT_CHARS,
    SEARCH_URL,
)
from .errors import FactCheckError, _FetchError, _SearchFailure
from .extraction import _claims_from_model, _locate, _source_segments
from .fetching import (
    _fetch_page,
    _hostname,
    _normalize,
    _now,
    _Page,
    _public_ip,
    _read_limited,
    _resolve_public,
    _validate_url,
)
from .judgment import _judge_result, _same_material
from .llm import _chat_json
from .orchestrator import run_fact_check
from .search import _search

__all__ = [
    "EXTRACTION_PROMPT",
    "FactCheckError",
    "FactJudgmentEvidence",
    "FETCH_TIMEOUT",
    "JUDGMENT_PROMPT",
    "MAX_BYTES",
    "MAX_EXTRACTED",
    "MAX_PAGE_TEXT",
    "MAX_REDIRECTS",
    "MODEL_TIMEOUT",
    "NativeSearchError",
    "QUOTE_CONTEXT_CHARS",
    "SEARCH_URL",
    "SearchResult",
    "_FetchError",
    "_Page",
    "_SearchFailure",
    "_chat_json",
    "_claims_from_model",
    "_fetch_page",
    "_hostname",
    "_judge_result",
    "_locate",
    "_normalize",
    "_now",
    "_public_ip",
    "_read_limited",
    "_resolve_public",
    "_same_material",
    "_search",
    "_source_segments",
    "_validate_url",
    "run_fact_check",
    # Re-exported so monkeypatch targets (e.g. fc.httpx, fc.asyncio, fc.logger) resolve.
    "asyncio",
    "httpx",
    "logger",
]
