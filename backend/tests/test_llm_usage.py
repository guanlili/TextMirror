import asyncio
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.api.v1.admin.dashboard import get_model_usage
from app.core.database import Base, async_session_factory
from app.models.llm_usage import LLMUsage
from app.services.llm import usage as usage_module
from app.services.llm.openai_compat import OpenAICompatProvider


@pytest.fixture
def captured(monkeypatch):
    events = []

    async def save(values):
        events.append(values)

    monkeypatch.setattr(usage_module, "save_usage", save)
    return events


def provider_with(handler, *, retries=1):
    provider = OpenAICompatProvider("sk-test", "https://usage-test.invalid/v1", "test-model", max_retries=retries)
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.api_base)
    provider._owns_client = True
    provider._verified_endpoint = "/chat/completions"
    provider.config_id = 123
    provider.usage_business = "evaluation"
    return provider


def chat_data(usage=None, finish="stop"):
    return {"choices": [{"message": {"content": "[]"}, "finish_reason": finish}], "usage": usage}


@pytest.mark.parametrize("raw,total", [(None, None), ({}, None), ({"prompt_tokens": 2, "completion_tokens": 3}, 5),
                                       ({"input_tokens": 2, "output_tokens": 3}, 5), ({"total_tokens": 0}, 0),
                                       ({"total_tokens": True}, None), ({"total_tokens": -1}, None)])
def test_token_counts_preserve_unknown(raw, total):
    assert usage_module.token_counts(raw)["total_tokens"] == total


async def test_chat_retries_and_self_check_are_metered_once(captured):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json=chat_data({"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}))

    provider = provider_with(handler, retries=2)
    try:
        with usage_module.usage_operation("self_check"):
            result = await provider.chat([{"role": "user", "content": "private source"}])
        assert result.usage["total_tokens"] == 6
        assert len(captured) == 2
        assert [event["outcome"] for event in captured] == ["error", "success"]
        assert [event["total_tokens"] for event in captured] == [None, 6]
        assert all(event["business"] == "evaluation" and event["operation"] == "self_check" for event in captured)
        assert "private source" not in str(captured) and "sk-test" not in str(captured)
    finally:
        await provider.close()


async def test_incomplete_response_still_keeps_usage(captured):
    provider = provider_with(lambda _: httpx.Response(200, json=chat_data({"total_tokens": 9}, "length")))
    try:
        await provider.chat([])
        assert captured[0]["total_tokens"] == 9
        assert captured[0]["outcome"] == "incomplete"
    finally:
        await provider.close()


async def test_stream_usage_only_frame_is_not_counted_twice(captured):
    chunks = [
        {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 4}},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
    ]
    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    provider = provider_with(lambda _: httpx.Response(200, text=body))
    try:
        assert [part async for part in provider.chat_stream([])] == ["hello"]
        assert len(captured) == 1
        assert captured[0]["total_tokens"] == 5
        assert captured[0]["outcome"] == "success"
    finally:
        await provider.close()


async def test_stream_without_usage_is_unknown(captured):
    provider = provider_with(lambda _: httpx.Response(200, text='data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'))
    try:
        assert [part async for part in provider.chat_stream([])] == ["hi"]
        assert captured[0]["total_tokens"] is None
        assert captured[0]["outcome"] == "incomplete"
    finally:
        await provider.close()


async def test_cancellation_is_metered_and_propagates(captured):
    provider = SimpleNamespace(model="model", config_id=None)
    with pytest.raises(asyncio.CancelledError):
        async with usage_module.meter_attempt(provider):
            raise asyncio.CancelledError
    assert captured[0]["outcome"] == "cancelled"
    assert captured[0]["total_tokens"] is None


async def test_database_failure_does_not_fail_model_call(monkeypatch):
    def fail_session():
        raise RuntimeError("database offline")

    monkeypatch.setattr(usage_module, "async_session_factory", fail_session)
    provider = provider_with(lambda _: httpx.Response(200, json=chat_data({"total_tokens": 2})))
    try:
        assert (await provider.chat([])).content == "[]"
    finally:
        await provider.close()


async def test_ledger_aggregation_excludes_old_results_and_keeps_unknown(client):
    now = datetime.now(timezone.utc)
    defaults = dict(config_id=1, config_name="usage-test", model="model", business="fact_check",
                    operation="native_search", elapsed_ms=100)
    async with async_session_factory() as db:
        await db.execute(sa.delete(LLMUsage))
        db.add_all([
            LLMUsage(**defaults, outcome="success", total_tokens=7, search_queries=1, created_at=now),
            LLMUsage(**defaults, outcome="error", total_tokens=None, created_at=now),
            LLMUsage(**defaults, outcome="incomplete", total_tokens=3, created_at=now),
            LLMUsage(**defaults, outcome="success", total_tokens=100, created_at=now - timedelta(days=8)),
        ])
        await db.commit()
        result = await get_model_usage(days=7, db=db, _user=None)
        assert result["calls"] == 3 and result["total_tokens"] == 10
        assert result["unknown_usage_calls"] == 1
        row = result["items"][0]
        assert row["errors"] == 1 and row["incomplete"] == 1 and row["search_queries"] == 1
        assert result["tracked_since"] is not None
    response = await client.get("/api/v1/admin/dashboard/model-usage")
    assert response.status_code == 401


async def test_native_search_usage_is_metered_once(captured, monkeypatch):
    from app.services import fact_check_search as search

    payload = {"status": "completed", "usage": {"input_tokens": 7, "output_tokens": 2},
               "output": [{"type": "web_search_call", "status": "completed"}]}
    original_client = httpx.AsyncClient
    monkeypatch.setattr(search.httpx, "AsyncClient", lambda **_: original_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))))
    monkeypatch.setattr(search, "_read_response", AsyncMock(return_value=payload))
    provider = SimpleNamespace(model="doubao-seed-2-0-mini-260428", provider_slug="volcengine", api_key="sk-test",
                               api_base="https://ark.cn-beijing.volces.com/api/v3")
    result = await search.search_model("测试陈述", provider, None)
    assert result.search_queries == 1
    assert len(captured) == 1
    assert captured[0]["business"] == "fact_check" and captured[0]["operation"] == "native_search"
    assert captured[0]["total_tokens"] == 9 and captured[0]["search_queries"] == 1


async def test_successful_usage_write_is_persisted(client):
    provider = SimpleNamespace(model="persist-test", provider_name="persist-test", config_id=None, usage_business="proofread")
    async with usage_module.meter_attempt(provider) as event:
        event["usage"] = {"total_tokens": 11}
        event["outcome"] = "success"
    async with async_session_factory() as db:
        rows = (await db.execute(sa.select(LLMUsage).where(LLMUsage.model == "persist-test"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].total_tokens == 11 and rows[0].prompt_tokens is None


@pytest.mark.parametrize("bootstrap", [False, True])
def test_usage_migration_matches_metadata(bootstrap):
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=[table for table in Base.metadata.sorted_tables
                                             if bootstrap or table != LLMUsage.__table__])
        path = Path(__file__).resolve().parents[1] / "alembic/versions/b3e6f8a0c415_add_llm_usage_ledger.py"
        spec = importlib.util.spec_from_file_location("usage_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        migration.upgrade()
        context = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": True})
        assert compare_metadata(context, Base.metadata) == []
        migration.downgrade()
        migration.downgrade()
        migration.upgrade()
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()
