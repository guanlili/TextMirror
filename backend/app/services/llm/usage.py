import asyncio
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

from loguru import logger

from app.core.database import async_session_factory
from app.models.llm_usage import LLMUsage

_operation: ContextVar[str | None] = ContextVar("llm_usage_operation", default=None)


@contextmanager
def usage_operation(operation: str):
    token = _operation.set(operation)
    try:
        yield
    finally:
        _operation.reset(token)


def token_counts(usage) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    counts = {}
    for key, alias in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"),
                       ("total_tokens", "total_tokens")):
        value = usage.get(key, usage.get(alias))
        counts[key] = value if type(value) is int and 0 <= value <= 2**63 - 1 else None
    if counts["total_tokens"] is None and all(counts[key] is not None for key in ("prompt_tokens", "completion_tokens")):
        total = counts["prompt_tokens"] + counts["completion_tokens"]
        counts["total_tokens"] = total if total <= 2**63 - 1 else None
    return counts


async def save_usage(values: dict):
    try:
        async with asyncio.timeout(2):
            async with async_session_factory() as session:
                session.add(LLMUsage(**values))
                await session.commit()
    except Exception as exc:
        logger.warning("模型用量记账失败，统计可能不完整：{}", type(exc).__name__)


@asynccontextmanager
async def meter_attempt(provider, operation="chat", *, business=None):
    started = time.monotonic()
    event = {"usage": None, "outcome": "error", "search_queries": None}
    try:
        yield event
    except (asyncio.CancelledError, GeneratorExit):
        event["outcome"] = "cancelled"
        raise
    except BaseException:
        event["outcome"] = "error"
        raise
    finally:
        # Only physical requests write here; business-result aggregates never enter this ledger.
        await save_usage({
            "config_id": getattr(provider, "config_id", None),
            "config_name": getattr(provider, "provider_name", "")[:200],
            "model": provider.model[:200],
            "business": business or getattr(provider, "usage_business", "other"),
            "operation": _operation.get() or operation,
            "outcome": event["outcome"],
            **token_counts(event["usage"]),
            "search_queries": event["search_queries"],
            "elapsed_ms": min(2147483647, max(0, int((time.monotonic() - started) * 1000))),
        })
