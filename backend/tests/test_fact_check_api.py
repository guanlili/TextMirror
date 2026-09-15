import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select, update

from app.core.database import async_session_factory
from app.core.dependencies import get_current_user
from app.core.secret_crypto import decrypt_secret, encrypt_secret
from app.main import app
from app.models.fact_check import FactCheckConfig, FactCheckRun
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.role import Permission, Role, RolePermission
from app.models.user import User
from app.schemas.fact_check import DAILY_LIMIT, FactCheckReport
from app.tasks.fact_check_task import async_fact_check

BASE = "/api/v1/fact-check"
ADMIN = "/api/v1/admin/fact-check/settings"
SOURCE = {"id": "official", "name": "示例发布机构", "domain": "example.com", "path_prefix": "/news", "is_enabled": True}
TEXT = "\U0001f600示例事件发生于2020年。"


@pytest.fixture
async def actors(client, monkeypatch):
    dispatch = Mock()
    monkeypatch.setattr(async_fact_check, "apply_async", dispatch)
    async with async_session_factory() as db:
        users = []
        for name, codes in (("owner", ["proofread:text", "proofread:document"]),
                            ("admin", ["admin:settings:edit"]), ("stranger", [])):
            role = Role(name=name, code=uuid.uuid4().hex)
            db.add(role)
            await db.flush()
            user = User(employee_id=uuid.uuid4().hex, username=name, password_hash="unused", role_id=role.id)
            db.add(user)
            await db.flush()
            users.append(user)
            for code in codes:
                permission = await db.scalar(select(Permission).where(Permission.code == code))
                if permission is None:
                    permission = Permission(name=code, code=code, type="button")
                    db.add(permission)
                    await db.flush()
                db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        config = await db.get(FactCheckConfig, 1)
        if config is None:
            config = FactCheckConfig(id=1)
            db.add(config)
        config.enabled, config.api_key, config.sources, config.max_claims = True, encrypt_secret("test-search-key"), [SOURCE], 2
        config.search_provider = "tavily"
        model = await db.scalar(select(LLMConfig).where(LLMConfig.is_active.is_(True)))
        if model is None:
            model = LLMConfig(name=uuid.uuid4().hex, is_active=True)
            db.add(model)
        model.provider, model.model, model.api_base = "openai", "test-model", "https://example.com/v1"
        model.api_key, model.is_enabled = encrypt_secret("test-model-secret"), True
        record = ProofreadRecord(user_id=users[0].id, type="text", original_text=TEXT, domain="general", result={"issues": []})
        db.add(record)
        await db.commit()
        await db.refresh(record)
    state = SimpleNamespace(owner=users[0], admin=users[1], stranger=users[2], current=users[0], record=record, model=model, dispatch=dispatch)
    app.dependency_overrides[get_current_user] = lambda: state.current
    yield state
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
async def native(actors):
    async with async_session_factory() as db:
        model = await db.get(LLMConfig, actors.model.id)
        model.provider = "volcengine"
        model.api_base = "https://ark.cn-beijing.volces.com/api/v3"
        model.model = "doubao-seed-2-0-lite-260428"
        config = await db.get(FactCheckConfig, 1)
        config.search_provider, config.api_key = "model", ""
        await db.commit()
        actors.model = model
    return actors


def payload(record, **changes):
    return {"record_id": record.id, "mode": "web", "source_ids": [], "allow_external_search": True,
            "request_id": str(uuid.uuid4()), **changes}


async def submit(client, actors, **changes):
    response = await client.post(f"{BASE}/runs", json=payload(actors.record, **changes))
    assert response.status_code == 202, response.text
    return response.json()


def empty_report():
    return {"claims": [], "coverage": {"extracted": 0, "checked": 0, "unverified": 0, "status": "complete", "reason": "未识别到可核查事实"},
            "usage": {"total_tokens": 10, "search_queries": 0, "pages_fetched": 0}, "checked_at": datetime.now(timezone.utc).isoformat()}


@pytest.mark.parametrize("legacy", [True, False])
async def test_saved_reports_with_and_without_audit_fields_remain_viewable(client, actors, legacy):
    from app.schemas.fact_check import FactEvidenceChecks
    from app.services.fact_check import _Page

    evidence = {"id": "c1-e1", "title": "正文标题", "url": "https://example.com/news/report", "quote": TEXT,
                "published_at": None, "retrieved_at": "2026-09-01T00:00:00Z", "publisher": "正文发布方", "stance": "supports"}
    claim = {"id": "c1", "original": TEXT, "start": 0, "end": len(TEXT), "statement": TEXT,
             "verdict": "supported", "reason": "正文给出同一事件与年份", "suggestion": None, "evidence": [evidence], "checked": True}
    if not legacy:
        checks = FactEvidenceChecks.model_validate({
            "subject": {"status": "match", "reason": "正文描述同一事件"},
            "event_time": {"status": "match", "reason": "正文说明事件发生于2020年"},
            "scope_unit": {"status": "not_applicable", "reason": "不是统计陈述"},
        })
        fetched = _Page(evidence["id"], evidence["title"], evidence["url"], "前文 " + TEXT + " 后文",
                        None, evidence["retrieved_at"], evidence["publisher"])
        claim["evidence"] = [fetched.evidence(TEXT, "supports", checks)]
        claim["search_rounds"] = [
            {"kind": kind, "query": TEXT + suffix, "status": "complete", "pages_fetched": count, "error_codes": []}
            for kind, suffix, count in (("initial", "", 1), ("counter", " 反证 更正", 0))
        ]
    report = {**empty_report(), "claims": [claim],
              "coverage": {"extracted": 1, "checked": 1, "unverified": 0, "status": "complete", "reason": ""}}
    run = await submit(client, actors)
    async with async_session_factory() as db:
        await db.execute(update(FactCheckRun).where(FactCheckRun.id == run["id"]).values(status="SUCCESS", result_json=report))
        await db.commit()
    for path in (f"{BASE}/runs/{run['id']}", f"{BASE}/runs?record_id={actors.record.id}"):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        result = response.json()
        if isinstance(result, list):
            result = next(item for item in result if item["id"] == run["id"])
        parsed = result["result"]
        assert parsed == FactCheckReport.model_validate(report).model_dump(mode="json")
        saved = parsed["claims"][0]
        if legacy:
            assert saved["search_rounds"] == [] and saved["evidence"][0]["checks"] is None
            assert saved["evidence"][0]["body_sha256"] is None
        else:
            assert saved["search_rounds"][1]["kind"] == "counter"
            assert saved["evidence"][0]["context_before"] == "前文 "
            assert saved["evidence"][0]["body_sha256"] == hashlib.sha256(fetched.text.encode()).hexdigest()
            assert saved["evidence"][0]["checks"]["event_time"]["status"] == "match"


async def test_worker_preserves_counter_failure_snapshot_not_success(client, actors, monkeypatch):
    import json

    from app.services import fact_check, proofread

    extraction = {"claims": [{"original": TEXT, "statement": TEXT}]}
    provider = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(content=json.dumps(extraction), usage={})),
                               close=AsyncMock())
    monkeypatch.setattr(proofread, "get_llm_provider", AsyncMock(return_value=provider))
    search = AsyncMock(side_effect=[["https://example.com/news/report"],
                                   fact_check.FactCheckError("SEARCH_AUTH_ERROR", "检索鉴权失败")])
    monkeypatch.setattr(fact_check, "_search", search)
    monkeypatch.setattr(fact_check, "_fetch_page", AsyncMock(return_value=fact_check._Page(
        "", "正文标题", "https://example.com/news/report", TEXT, None, "2026-09-01T00:00:00Z", "发布方")))
    run = await submit(client, actors)
    async_fact_check.run(run["id"])
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "FAILURE" and result["error_code"] == "SEARCH_AUTH_ERROR"
    assert result["progress"] < 100 and result["result"]["coverage"]["status"] == "partial"
    claim = result["result"]["claims"][0]
    assert claim["verdict"] == "insufficient" and not claim["checked"]
    assert [item["status"] for item in claim["search_rounds"]] == ["complete", "failed"]
    assert claim["search_rounds"][1]["error_codes"] == ["SEARCH_AUTH_ERROR"]
    assert search.await_count == 2 and provider.chat.await_count == 1
    provider.close.assert_awaited_once()


async def test_options_submit_snapshot_and_idempotency(client, actors):
    options = (await client.get(f"{BASE}/options")).json()
    assert options["available"] and options["sources"] == [SOURCE]
    assert options["max_claims"] == 2 and options["daily_limit"] == DAILY_LIMIT
    request_id = str(uuid.uuid4())
    run = await submit(client, actors, request_id=request_id, mode="trusted", source_ids=[SOURCE["id"]])
    assert run["status"] == "PENDING" and run["result"] is None
    assert run["source_hash"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert "source_text" not in run and "api_key" not in run
    duplicate = await submit(client, actors, request_id=request_id, mode="trusted", source_ids=[SOURCE["id"]])
    assert duplicate == run
    assert actors.dispatch.call_count == 1
    changed = await client.post(f"{BASE}/runs", json=payload(actors.record, request_id=request_id))
    assert changed.status_code == 409
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record))).status_code == 409
    async with async_session_factory() as db:
        stored = await db.get(FactCheckRun, run["id"])
        original = await db.get(ProofreadRecord, actors.record.id)
        assert stored.source_text == TEXT and stored.sources == [SOURCE]
        assert original.result == {"issues": []} and original.original_text == TEXT
    assert (await client.get(f"{BASE}/runs", params={"record_id": actors.record.id})).json() == [run]


async def test_rbac_owner_isolation_and_auth(client, actors):
    run = await submit(client, actors)
    assert (await client.get(ADMIN)).status_code == 403
    actors.current = actors.stranger
    for path in (f"{BASE}/runs/{run['id']}", f"{BASE}/runs?record_id={actors.record.id}"):
        assert (await client.get(path)).status_code == 404
    assert (await client.post(f"{BASE}/runs/{run['id']}/cancel")).status_code == 404
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record))).status_code == 404
    app.dependency_overrides.pop(get_current_user)
    assert (await client.get(f"{BASE}/options")).status_code == 401


async def test_own_record_still_requires_proofread_permission(client, actors):
    async with async_session_factory() as db:
        await db.execute(update(ProofreadRecord).where(ProofreadRecord.id == actors.record.id).values(user_id=actors.stranger.id))
        await db.commit()
    actors.current = actors.stranger
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record))).status_code == 403


@pytest.mark.parametrize("changes", [
    {"allow_external_search": False}, {"mode": "unknown"}, {"source_ids": ["official"]},
    {"mode": "trusted", "source_ids": ["missing"]}, {"mode": "trusted", "source_ids": ["official", "official"]},
    {"request_id": "not-a-uuid"}, {"text": "不允许替换记录原文"},
])
async def test_invalid_requests_never_dispatch(client, actors, changes):
    response = await client.post(f"{BASE}/runs", json=payload(actors.record, **changes))
    assert response.status_code == 422
    actors.dispatch.assert_not_called()


async def test_missing_consent_disabled_sources_and_text_limit(client, actors):
    data = payload(actors.record)
    del data["allow_external_search"]
    assert (await client.post(f"{BASE}/runs", json=data)).status_code == 422
    async with async_session_factory() as db:
        config = await db.get(FactCheckConfig, 1)
        config.sources = [{**SOURCE, "is_enabled": False}]
        await db.commit()
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record, mode="trusted"))).status_code == 422
    async with async_session_factory() as db:
        record = await db.get(ProofreadRecord, actors.record.id)
        record.original_text = "字" * 20001
        await db.commit()
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record))).status_code == 422
    actors.dispatch.assert_not_called()


async def test_settings_secret_encryption_blank_preservation_and_disable(client, actors):
    actors.current = actors.admin
    response = await client.put(ADMIN, json={"enabled": True, "provider": "tavily", "api_key": "new-private-key", "max_claims": 4, "sources": [SOURCE]})
    assert response.status_code == 200, response.text
    assert response.json()["api_key_configured"] and "private-key" not in response.text
    assert "api_key" not in response.json() and response.json()["provider"] == "tavily"
    assert (await client.put(ADMIN, json={"enabled": False, "provider": "tavily", "api_key": "", "max_claims": 4, "sources": []})).status_code == 200
    async with async_session_factory() as db:
        config = await db.get(FactCheckConfig, 1)
        assert config.api_key != "new-private-key" and decrypt_secret(config.api_key) == "new-private-key"
    actors.current = actors.owner
    assert not (await client.get(f"{BASE}/options")).json()["available"]
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record))).status_code == 503
    actors.dispatch.assert_not_called()


@pytest.mark.parametrize("source", [
    {**SOURCE, "domain": "127.0.0.1"}, {**SOURCE, "domain": "host.internal"},
    {**SOURCE, "domain": "https://example.com"}, {**SOURCE, "domain": "*.example.com"},
    {**SOURCE, "path_prefix": "/news/../admin"}, {**SOURCE, "path_prefix": "/%2e%2e/"},
    {**SOURCE, "path_prefix": "//evil"}, {**SOURCE, "name": " "},
])
async def test_settings_reject_invalid_sources(client, actors, source):
    actors.current = actors.admin
    response = await client.put(ADMIN, json={"enabled": True, "sources": [source]})
    assert response.status_code == 422


async def test_cancel_queued_idempotent_and_worker_does_not_run(client, actors):
    run = await submit(client, actors)
    first = await client.post(f"{BASE}/runs/{run['id']}/cancel")
    assert first.json()["status"] == "CANCELLED"
    assert (await client.post(f"{BASE}/runs/{run['id']}/cancel")).json() == first.json()
    async_fact_check.run(run["id"])
    assert (await client.get(f"{BASE}/runs/{run['id']}")).json()["status"] == "CANCELLED"


async def test_publish_failure_and_expired_task_are_visible(client, actors):
    actors.dispatch.side_effect = ConnectionError("secret broker details")
    run = await submit(client, actors)
    assert run["status"] == "FAILURE" and run["error_code"] == "QUEUE_UNAVAILABLE"
    assert "secret" not in run["message"]
    actors.dispatch.side_effect = None
    pending = await submit(client, actors)
    async with async_session_factory() as db:
        await db.execute(update(FactCheckRun).where(FactCheckRun.id == pending["id"]).values(created_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
        await db.commit()
    response = (await client.get(f"{BASE}/runs/{pending['id']}")).json()
    assert response["status"] == "FAILURE" and response["error_code"] == "TASK_EXPIRED"


async def test_daily_budget_includes_cancelled_runs(client, actors):
    for _ in range(DAILY_LIMIT):
        run = await submit(client, actors)
        assert (await client.post(f"{BASE}/runs/{run['id']}/cancel")).status_code == 200
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record))).status_code == 429
    assert actors.dispatch.call_count == DAILY_LIMIT


async def test_worker_success_and_duplicate_delivery(client, actors, monkeypatch):
    from app.services import fact_check, proofread

    report = empty_report()
    provider = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(proofread, "get_llm_provider", AsyncMock(return_value=provider))
    engine = AsyncMock(return_value=report)
    monkeypatch.setattr(fact_check, "run_fact_check", engine)
    run = await submit(client, actors)
    async_fact_check.run(run["id"])
    async_fact_check.run(run["id"])
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "SUCCESS" and result["progress"] == 100 and result["result"] == report
    assert engine.await_count == 1 and provider.close.await_count == 1
    assert engine.call_args.args == (TEXT,)


async def test_worker_rejects_bad_spans_and_preserves_partial_on_failure(client, actors, monkeypatch):
    from app.services import fact_check, proofread

    provider = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(proofread, "get_llm_provider", AsyncMock(return_value=provider))
    partial = {**empty_report(), "coverage": {"extracted": 0, "checked": 0, "unverified": 0, "status": "partial", "reason": "提取中"}}

    async def failing_engine(text, **kwargs):
        await kwargs["on_progress"](20, "检索中", partial)
        raise RuntimeError("secret details")

    monkeypatch.setattr(fact_check, "run_fact_check", failing_engine)
    run = await submit(client, actors)
    async_fact_check.run(run["id"])
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "FAILURE" and result["result"] == partial and "secret" not in result["message"]
    with pytest.raises(ValueError):
        FactCheckReport.model_validate({**empty_report(), "coverage": {"extracted": 1, "checked": 0, "unverified": 1, "status": "partial"}})


async def test_worker_timeout_and_invalid_original_are_not_success(client, actors, monkeypatch):
    from app.services import fact_check, proofread

    provider = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(proofread, "get_llm_provider", AsyncMock(return_value=provider))
    monkeypatch.setattr(fact_check, "run_fact_check", AsyncMock(side_effect=TimeoutError()))
    run = await submit(client, actors)
    async_fact_check.run(run["id"])
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "FAILURE" and result["error_code"] == "TIMEOUT"
    malformed = {**empty_report(), "claims": [{"id": "c1", "original": "错误", "start": 0, "end": 2,
                  "statement": "不存在于原文的事实", "verdict": "insufficient", "reason": "未找到证据", "checked": True}],
                 "coverage": {"extracted": 1, "checked": 1, "unverified": 0, "status": "complete", "reason": ""}}
    monkeypatch.setattr(fact_check, "run_fact_check", AsyncMock(return_value=malformed))
    second = await submit(client, actors)
    async_fact_check.run(second["id"])
    result = (await client.get(f"{BASE}/runs/{second['id']}")).json()
    assert result["status"] == "FAILURE" and result["result"] is None


async def test_worker_cancellation_cannot_be_overwritten(client, actors, monkeypatch):
    from sqlalchemy.orm import Session

    from app.services import fact_check, proofread
    from app.tasks import proofread_task

    provider = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(proofread, "get_llm_provider", AsyncMock(return_value=provider))
    run = await submit(client, actors)

    async def engine(text, **kwargs):
        with Session(proofread_task._get_sync_engine()) as db:
            db.execute(update(FactCheckRun).where(FactCheckRun.id == run["id"]).values(status="CANCELLED"))
            db.commit()
        await kwargs["on_progress"](50, "不得覆盖取消状态", empty_report())
        raise AssertionError("cancel callback must abort")

    monkeypatch.setattr(fact_check, "run_fact_check", engine)
    async_fact_check.run(run["id"])
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "CANCELLED" and result["result"] is None
    provider.close.assert_awaited_once()


async def test_native_without_tavily_key_settings_options_and_worker(client, native, monkeypatch):
    from app.services import fact_check, proofread
    from app.tasks import fact_check_task

    native.current = native.admin
    saved = await client.put(ADMIN, json={"enabled": True, "sources": [SOURCE]})
    assert saved.status_code == 200, saved.text
    settings = saved.json()
    assert settings["provider"] == "model" and not settings["api_key_configured"]
    assert settings["model_search_supported"] and settings["model_search_reason"] == ""
    assert settings["model_name"] == f"{native.model.name} ({native.model.model})"
    assert (await client.get(ADMIN)).json() == settings
    native.current = native.owner
    options = (await client.get(f"{BASE}/options")).json()
    assert options["available"] and options["provider"] == "model"
    assert options["model_name"] == settings["model_name"]
    run = await submit(client, native)
    assert run["provider"] == "model"
    provider = SimpleNamespace(close=AsyncMock())
    get_provider = AsyncMock(return_value=provider)
    engine = AsyncMock(return_value=empty_report())
    decrypt = Mock(side_effect=AssertionError("native must never decrypt Tavily credentials"))
    monkeypatch.setattr(proofread, "get_llm_provider", get_provider)
    monkeypatch.setattr(fact_check, "run_fact_check", engine)
    monkeypatch.setattr(fact_check_task, "decrypt_secret", decrypt)
    async_fact_check.run(run["id"])
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "SUCCESS" and result["provider"] == "model"
    assert engine.call_args.kwargs["search_provider"] == "model" and engine.call_args.kwargs["api_key"] == ""
    assert engine.call_args.kwargs["provider"] is provider
    get_provider.assert_awaited_once_with(native.model.id)
    provider.close.assert_awaited_once()
    decrypt.assert_not_called()
    for response in (saved, await client.get(f"{BASE}/options"), await client.get(f"{BASE}/runs/{run['id']}")):
        for secret in ("test-model-secret", "test-search-key", native.model.api_key, native.model.api_base):
            assert secret not in response.text
        assert "api_key" not in response.json()


@pytest.mark.parametrize("key_field", [{}, {"api_key": ""}, {"api_key": None}])
async def test_native_settings_preserve_saved_tavily_secret(client, native, key_field):
    stored_secret = encrypt_secret("retained-search-secret")
    async with async_session_factory() as db:
        config = await db.get(FactCheckConfig, 1)
        config.search_provider, config.api_key = "tavily", stored_secret
        await db.commit()
    native.current = native.admin
    response = await client.put(ADMIN, json={"enabled": True, "provider": "model", **key_field})
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "model" and response.json()["api_key_configured"]
    assert stored_secret not in response.text and "retained-search-secret" not in response.text
    async with async_session_factory() as db:
        assert (await db.get(FactCheckConfig, 1)).api_key == stored_secret


@pytest.mark.parametrize("provider_field", [{}, {"provider": "model"}])
async def test_model_settings_reject_new_tavily_key_without_leaking_it(client, native, provider_field):
    native.current = native.admin
    before = (await client.get(ADMIN)).json()
    response = await client.put(ADMIN, json={"enabled": True, "api_key": "must-not-be-saved", **provider_field})
    assert response.status_code == 422
    assert "复用" in response.text and "must-not-be-saved" not in response.text
    assert (await client.get(ADMIN)).json() == before
    async with async_session_factory() as db:
        assert (await db.get(FactCheckConfig, 1)).api_key == ""


@pytest.mark.parametrize("change,supported", [
    ({"provider": "custom", "api_base": "https://unsupported.example.com/v1"}, False),
    ({"api_base": "https://proxy.example.com/v1"}, False),
    ({"is_active": False}, False),
    ({"is_enabled": False}, False),
    ({"api_key": ""}, True),
])
async def test_native_unavailable_guards_do_not_fallback_to_saved_tavily(client, native, change, supported):
    async with async_session_factory() as db:
        model = await db.get(LLMConfig, native.model.id)
        for name, value in change.items():
            setattr(model, name, value)
        config = await db.get(FactCheckConfig, 1)
        config.api_key = encrypt_secret("valid-but-not-selected-tavily-key")
        await db.commit()
    options = (await client.get(f"{BASE}/options")).json()
    assert not options["available"] and options["provider"] == "model"
    assert "不会自动切换" in options["unavailable_reason"]
    response = await client.post(f"{BASE}/runs", json=payload(native.record))
    assert response.status_code == 503 and response.json()["detail"] == options["unavailable_reason"]
    native.current = native.admin
    settings = (await client.get(ADMIN)).json()
    assert settings["model_search_supported"] == supported and bool(settings["model_search_reason"]) != supported
    assert settings["api_key_configured"]
    if change.get("is_active") is False or change.get("is_enabled") is False:
        assert settings["model_name"] == options["model_name"] == ""
    response = await client.put(ADMIN, json={"enabled": True, "provider": "model"})
    assert response.status_code == 422 and response.json()["detail"] == options["unavailable_reason"]
    assert (await client.put(ADMIN, json={"enabled": False, "provider": "model"})).status_code == 200
    native.dispatch.assert_not_called()


async def test_missing_config_defaults_model_and_still_reports_active_model(client, native):
    async with async_session_factory() as db:
        await db.delete(await db.get(FactCheckConfig, 1))
        await db.commit()
    native.current = native.admin
    settings = (await client.get(ADMIN)).json()
    assert not settings["enabled"] and settings["provider"] == "model" and not settings["api_key_configured"]
    assert settings["model_search_supported"] and settings["model_name"]
    options = (await client.get(f"{BASE}/options")).json()
    assert not options["available"] and options["provider"] == "model" and options["model_name"] == settings["model_name"]
    response = await client.put(ADMIN, json={"enabled": True})
    assert response.status_code == 200 and response.json()["provider"] == "model"
    async with async_session_factory() as db:
        assert (await db.get(FactCheckConfig, 1)).api_key == ""


async def test_tavily_does_not_require_native_support_but_requires_own_key(client, actors):
    actors.current = actors.admin
    settings = (await client.get(ADMIN)).json()
    assert settings["provider"] == "tavily" and not settings["model_search_supported"]
    assert settings["model_search_reason"] and actors.model.model in settings["model_name"]
    assert (await client.put(ADMIN, json={"enabled": True, "provider": "tavily"})).status_code == 200
    actors.current = actors.owner
    assert (await client.get(f"{BASE}/options")).json()["available"]
    run = await submit(client, actors)
    assert run["provider"] == "tavily"
    await client.post(f"{BASE}/runs/{run['id']}/cancel")
    async with async_session_factory() as db:
        (await db.get(FactCheckConfig, 1)).api_key = ""
        await db.commit()
    options = (await client.get(f"{BASE}/options")).json()
    assert not options["available"] and "Tavily" in options["unavailable_reason"]
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record))).status_code == 503
    actors.current = actors.admin
    response = await client.put(ADMIN, json={"enabled": True, "provider": "tavily"})
    assert response.status_code == 422 and "Tavily" in response.text
    assert actors.dispatch.call_count == 1


async def test_tavily_requires_active_enabled_model_for_options_and_runs(client, actors):
    async with async_session_factory() as db:
        (await db.get(LLMConfig, actors.model.id)).is_enabled = False
        await db.commit()
    options = (await client.get(f"{BASE}/options")).json()
    assert not options["available"] and options["model_name"] == "" and "大模型" in options["unavailable_reason"]
    assert (await client.post(f"{BASE}/runs", json=payload(actors.record))).status_code == 503
    actors.current = actors.admin
    settings = (await client.get(ADMIN)).json()
    assert not settings["model_search_supported"] and settings["model_name"] == ""
    actors.dispatch.assert_not_called()


async def test_settings_provider_enum_is_validated(client, actors):
    actors.current = actors.admin
    assert (await client.put(ADMIN, json={"provider": "automatic"})).status_code == 422


@pytest.mark.parametrize("changes,error_type,location", [
    ({"api_key": "validation-secret-" * 100}, "too_long", ["body", "api_key"]),
    ({"api_key": {"nested": ["validation-secret"]}}, "string_type", ["body", "api_key"]),
    ({"api_key": ["validation-secret"]}, "string_type", ["body", "api_key"]),
    ({"api_key": 123456789}, "string_type", ["body", "api_key"]),
    ({"extra": {"api_key": "validation-secret"}}, "extra_forbidden", ["body", "extra"]),
    ({"sources": [{**SOURCE, "api_key": {"nested": "validation-secret"}}]},
     "extra_forbidden", ["body", "sources", 0, "api_key"]),
    ({"sources": [{**SOURCE, "domain": "validation-secret.internal"}]},
     "value_error", ["body", "sources", 0, "domain"]),
    ({"sources": [SOURCE, SOURCE]}, "value_error", ["body", "sources"]),
    ({"provider": "automatic"}, "literal_error", ["body", "provider"]),
    ({"max_claims": 11}, "less_than_equal", ["body", "max_claims"]),
])
async def test_settings_validation_never_echoes_secrets(client, actors, changes, error_type, location):
    actors.current = actors.admin
    before = (await client.get(ADMIN)).json()
    async with async_session_factory() as db:
        saved_key = (await db.get(FactCheckConfig, 1)).api_key
    response = await client.put(ADMIN, json={"provider": "tavily", "api_key": "validation-secret", **changes})
    assert response.status_code == 422, response.text
    assert "validation-secret" not in response.text and "123456789" not in response.text
    errors = response.json()["detail"]
    assert errors and all(set(error) == {"type", "loc", "msg"} for error in errors)
    assert any(error["type"] == error_type and error["loc"] == location for error in errors)
    assert (await client.get(ADMIN)).json() == before
    async with async_session_factory() as db:
        assert (await db.get(FactCheckConfig, 1)).api_key == saved_key


def test_settings_openapi_preserves_validation_schema():
    from fastapi.routing import APIRoute

    from app.api.v1.fact_check import admin_router, router

    schema = app.openapi()
    operation = schema["paths"][ADMIN]["put"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/FactCheckSettingsUpdate",
    }
    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HTTPValidationError",
    }
    settings = schema["components"]["schemas"]["FactCheckSettingsUpdate"]
    assert settings["additionalProperties"] is False
    properties = settings["properties"]
    key = next(item for item in properties["api_key"]["anyOf"] if item["type"] == "string")
    assert key["maxLength"] == 1024 and key["format"] == "password" and key["writeOnly"]
    assert properties["provider"]["enum"] == ["model", "tavily"]
    assert properties["max_claims"]["minimum"] == 1 and properties["max_claims"]["maximum"] == 10
    assert all(type(route) is APIRoute for route in router.routes)
    assert all(type(route) is not APIRoute and isinstance(route, APIRoute) for route in admin_router.routes)


@pytest.mark.parametrize("selected", ["model", "tavily"])
@pytest.mark.parametrize("change", [{"api_key": ""}, {"api_key": " \t\n"}, {"is_active": False}, {"is_enabled": False}])
async def test_both_providers_require_model_credentials_without_consuming_quota(client, native, selected, change):
    async with async_session_factory() as db:
        config = await db.get(FactCheckConfig, 1)
        config.search_provider, config.api_key = selected, encrypt_secret("valid-tavily-key")
        model = await db.get(LLMConfig, native.model.id)
        for name, value in change.items():
            setattr(model, name, value)
        await db.commit()
    options = (await client.get(f"{BASE}/options")).json()
    assert not options["available"] and options["provider"] == selected
    assert "大模型" in options["unavailable_reason"]
    if "api_key" in change:
        assert "API 密钥" in options["unavailable_reason"]
    response = await client.post(f"{BASE}/runs", json=payload(native.record))
    assert response.status_code == 503 and response.json()["detail"] == options["unavailable_reason"]
    native.dispatch.assert_not_called()
    async with async_session_factory() as db:
        assert (await db.scalars(select(FactCheckRun.id).where(FactCheckRun.user_id == native.owner.id))).all() == []


@pytest.mark.parametrize("selected", ["model", "tavily"])
async def test_run_snapshots_provider_and_model_across_settings_changes(client, native, monkeypatch, selected):
    from app.services import fact_check, proofread

    native.current = native.admin
    initial = {"enabled": True, "provider": selected, "sources": [SOURCE]}
    if selected == "tavily":
        initial["api_key"] = "selected-tavily-key"
    assert (await client.put(ADMIN, json=initial)).status_code == 200
    native.current = native.owner
    request_id = str(uuid.uuid4())
    run = await submit(client, native, request_id=request_id)
    async with async_session_factory() as db:
        stored = await db.get(FactCheckRun, run["id"])
        assert stored.search_provider == selected and stored.config_id == native.model.id
        model = await db.get(LLMConfig, native.model.id)
        model.is_active = False
        await db.flush()
        db.add(LLMConfig(name=uuid.uuid4().hex, provider="volcengine", api_base=native.model.api_base,
                         model=native.model.model, api_key=encrypt_secret("other-model-secret"), is_active=True, is_enabled=True))
        await db.commit()
    native.current = native.admin
    changed = {"enabled": True, "provider": "tavily" if selected == "model" else "model"}
    if selected == "model":
        changed["api_key"] = "current-tavily-key"
    response = await client.put(ADMIN, json=changed)
    assert response.status_code == 200, response.text
    native.current = native.owner
    assert (await client.get(f"{BASE}/options")).json()["provider"] == changed["provider"]
    assert await submit(client, native, request_id=request_id) == run
    assert native.dispatch.call_count == 1
    provider = SimpleNamespace(close=AsyncMock())
    get_provider = AsyncMock(return_value=provider)
    engine = AsyncMock(return_value=empty_report())
    monkeypatch.setattr(proofread, "get_llm_provider", get_provider)
    monkeypatch.setattr(fact_check, "run_fact_check", engine)
    async_fact_check.run(run["id"])
    get_provider.assert_awaited_once_with(native.model.id)
    assert engine.call_args.kwargs["search_provider"] == selected
    assert engine.call_args.kwargs["api_key"] == ("selected-tavily-key" if selected == "tavily" else "")
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "SUCCESS" and result["provider"] == selected
    provider.close.assert_awaited_once()


@pytest.mark.parametrize("selected,change,error_code", [
    ("model", "disabled", "FACT_CHECK_DISABLED"),
    ("tavily", "disabled", "FACT_CHECK_DISABLED"),
    ("model", "unsupported", "MODEL_SEARCH_UNSUPPORTED"),
    ("model", "model_disabled", "MODEL_CONFIG_UNAVAILABLE"),
    ("model", "model_key_missing", "MODEL_API_KEY_MISSING"),
    ("tavily", "tavily_key_missing", "TAVILY_API_KEY_MISSING"),
])
async def test_worker_rechecks_snapshot_requirements_without_fallback(client, native, monkeypatch, selected, change, error_code):
    from app.services import fact_check, proofread

    async with async_session_factory() as db:
        config = await db.get(FactCheckConfig, 1)
        config.search_provider, config.api_key = selected, encrypt_secret("available-tavily-secret")
        await db.commit()
    run = await submit(client, native)
    async with async_session_factory() as db:
        config = await db.get(FactCheckConfig, 1)
        config.search_provider = "model" if selected == "tavily" else "tavily"
        model = await db.get(LLMConfig, native.model.id)
        if change == "disabled":
            config.enabled = False
        elif change == "unsupported":
            model.provider, model.api_base = "custom", "https://unsupported.example.com/v1"
        elif change == "model_disabled":
            model.is_enabled = False
        elif change == "model_key_missing":
            model.api_key = ""
        elif change == "tavily_key_missing":
            config.api_key = ""
        await db.commit()
    get_provider = AsyncMock()
    engine = AsyncMock()
    monkeypatch.setattr(proofread, "get_llm_provider", get_provider)
    monkeypatch.setattr(fact_check, "run_fact_check", engine)
    async_fact_check.run(run["id"])
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "FAILURE" and result["error_code"] == error_code and result["provider"] == selected
    assert "secret" not in result["message"]
    if change != "disabled":
        assert "不会自动切换" in result["message"]
    get_provider.assert_not_awaited()
    engine.assert_not_awaited()


@pytest.mark.parametrize("selected", ["model", "tavily"])
async def test_worker_runtime_error_closes_provider_and_never_retries_another_service(client, native, monkeypatch, selected):
    from app.services import fact_check, proofread

    async with async_session_factory() as db:
        config = await db.get(FactCheckConfig, 1)
        config.search_provider, config.api_key = selected, encrypt_secret("saved-tavily-secret")
        await db.commit()
    run = await submit(client, native)
    provider = SimpleNamespace(close=AsyncMock())
    get_provider = AsyncMock(return_value=provider)
    engine = AsyncMock(side_effect=RuntimeError("secret vendor response"))
    monkeypatch.setattr(proofread, "get_llm_provider", get_provider)
    monkeypatch.setattr(fact_check, "run_fact_check", engine)
    async_fact_check.run(run["id"])
    result = (await client.get(f"{BASE}/runs/{run['id']}")).json()
    assert result["status"] == "FAILURE" and result["error_code"] == "FACT_CHECK_FAILED"
    assert result["provider"] == selected and "secret" not in result["message"]
    assert engine.await_count == 1 and engine.call_args.kwargs["search_provider"] == selected
    get_provider.assert_awaited_once_with(native.model.id)
    provider.close.assert_awaited_once()
