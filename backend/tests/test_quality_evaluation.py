"""质量评测：全程 SQLite/fakeredis + mock LLM，不读生产库、不调用模型。"""
import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.v1.admin import quality_evaluation as api
from app.core import redis as redis_module
from app.core.database import async_session_factory, get_db
from app.core.dependencies import get_current_user
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.role import Role
from app.models.user import User
from app.schemas.quality_evaluation import EvaluationModel, EvaluationRequest, EvaluationSnapshot
from app.services import quality_evaluation as service
from eval import eval as fixed_eval

SOURCE = "甲\U00020000错词，乙错词。尾"
URL = "/api/v1/admin/global-dict/quality-feedback/evaluate"
SECRET_ERROR = "provider /private/internal/file api_key=TEST_NOT_A_SECRET"


def snapshot(id_=1, expectation="report", **changes):
    sample = dict(text=SOURCE, domain="general", start=6, end=8, original="错词",
                  expectation=expectation, issue_type="typo")
    sample.update(changes)
    return EvaluationSnapshot(id=id_, revision=3, sample=sample)


def issue(**changes):
    value = dict(original="错词", start=6, end=8, type="typo")
    value.update(changes)
    return value


def result(issues=None, config_id=1, **changes):
    value = {"coverage": {"status": "complete", "total_chunks": 1, "completed_chunks": 1, "failed_chunks": []},
             "issues": issues or [], "config_id": config_id}
    value.update(changes)
    return value


def model(config_id=1):
    return EvaluationModel(config_id=config_id, config_name=f"测试模型{config_id}", model="mock-model")


@pytest.mark.parametrize("issues,detected", [
    ([issue()], True),
    ([issue(start=2, end=4)], False),  # 同词另一位置不算目标
    ([issue(type="grammar")], False),
    ([issue(original="乙错词", start=5, end=8)], True),  # issue 包含目标
    ([issue(original="错", start=6, end=7)], True),  # 目标包含 issue
    ([issue(original="词。", start=7, end=9)], False),  # 仅相交，不包含
    ([issue(original="尾", start=9, end=10)], False),
    ([], False),
])
def test_exact_unicode_ranges_and_types(issues, detected):
    assert service.detect_target(snapshot().sample, result(issues)) is detected


def test_untyped_sample_accepts_type_and_unique_unpositioned_original():
    sample = snapshot(text="甲\U00020000错词。", start=2, end=4, issue_type="").sample
    assert service.detect_target(sample, result([issue(start=None, end=None, type="grammar")])) is True


@pytest.mark.parametrize("bad_issue", [
    issue(start=None, end=None), issue(start=6.0), issue(start=True), issue(start=-1),
    issue(start=6, end=None), issue(start=3, end=5), issue(end=200), issue(original="不存在"),
    issue(original=""), issue(original=None), issue(start=8, end=6),
])
def test_unreliable_location_is_error(bad_issue):
    with pytest.raises(ValueError):
        service.detect_target(snapshot().sample, result([bad_issue]))


def test_overlapping_occurrences_are_not_unique():
    sample = snapshot(text="aaa", start=0, end=2, original="aa").sample
    with pytest.raises(ValueError):
        service.detect_target(sample, result([issue(original="aa", start=None, end=None)]))


def test_located_match_does_not_hide_unlocated_issue():
    with pytest.raises(ValueError):
        service.detect_target(snapshot().sample, result([issue(), issue(start=None, end=None)]))


@pytest.mark.parametrize("changes", [
    {"coverage": None}, {"coverage": {}}, {"coverage": {"status": "unknown"}},
    {"coverage": {"status": "partial"}}, {"success": False}, {"complete": False},
    {"coverage": {"status": "complete", "failed_chunks": [{}]}},
    {"coverage": {"status": "complete", "total_chunks": 2, "completed_chunks": 1}},
    {"issues": None}, {"issues": [None]}, {"error": SECRET_ERROR},
])
async def test_partial_and_failed_results_never_enter_denominators(monkeypatch, changes):
    value = result([issue()])
    value.update(changes)
    mock = AsyncMock(return_value=value)
    monkeypatch.setattr(service, "proofread_text", mock)
    report = await service.run_evaluation([snapshot(), snapshot(2, "no_report")], [model()])
    entry = report.results[0]
    assert entry.errors == 2 and entry.report_evaluated == entry.no_report_evaluated == 0
    assert entry.missed == entry.false_positives == 0
    assert all(case.status == "error" and case.detected is None and case.error == service.EVALUATION_ERROR
               for case in entry.cases)
    assert SECRET_ERROR not in report.model_dump_json()
    assert mock.await_count == 2  # 不重试


async def test_totals_only_measure_target_and_snapshot_is_safe(monkeypatch):
    samples = [snapshot(i, "report" if i <= 3 else "no_report", text=SOURCE + str(i)) for i in range(1, 7)]

    async def mock_proofread(**kwargs):
        assert kwargs["user_id"] is None and kwargs["depth"] == "standard"
        index = int(kwargs["text"][-1])
        if index in (3, 6):
            raise RuntimeError(SECRET_ERROR)
        return result([issue()] if index in (1, 5) else [issue(original="尾", start=9, end=10)],
                      raw_provider_error=SECRET_ERROR, api_key="TEST_NOT_A_SECRET")

    monkeypatch.setattr(service, "proofread_text", mock_proofread)
    report = await service.run_evaluation(samples, [model()])
    entry = report.results[0]
    assert (entry.report_total, entry.report_evaluated, entry.missed) == (3, 2, 1)
    assert (entry.no_report_total, entry.no_report_evaluated, entry.false_positives) == (3, 2, 1)
    assert entry.errors == 2
    assert [case.status for case in entry.cases] == ["pass", "fail", "error", "pass", "fail", "error"]
    assert all(case.revision == 3 and case.elapsed_ms >= 0 for case in entry.cases)
    assert set(report.model_dump()) == {"generated_at", "samples", "results"}
    assert set(entry.cases[0].model_dump()) == {
        "feedback_id", "revision", "expectation", "status", "detected", "error", "elapsed_ms",
        "detection_status", "suggestion_status", "suggestion_reason",
    }
    assert entry.suggestion_evaluated == 0 and entry.suggestion_not_evaluated == 6
    assert all(case.suggestion_status == "not_evaluated" for case in entry.cases)
    assert SECRET_ERROR not in report.model_dump_json() and "api_key" not in report.model_dump_json()
    samples[0].revision = 99
    assert report.samples[0].revision == 3


@pytest.mark.parametrize("config_id", [999, True, 1.0, "1", None])
async def test_different_model_is_not_attributed_to_requested_model(monkeypatch, config_id):
    monkeypatch.setattr(service, "proofread_text", AsyncMock(return_value=result([issue()], config_id=config_id)))
    report = await service.run_evaluation([snapshot()], [model()])
    assert report.results[0].errors == 1


@pytest.mark.parametrize("issues,accepted,rejected,status,reason", [
    ([issue(suggestion="正词")], ["正词"], [], "pass", "all_accepted"),
    ([issue(suggestion="好词")], ["正词", "好词"], [], "pass", "all_accepted"),
    ([issue(suggestion="坏词")], ["正词"], ["坏词"], "fail", "rejected"),
    ([issue(suggestion="另一错误")], ["正词"], [], "fail", "not_accepted"),
    ([issue(suggestion=" 正词 ")], ["正词"], [], "fail", "not_accepted"),
    ([issue(suggestion="")], [""], [], "pass", "all_accepted"),
    ([issue(suggestion="")], [], [""], "fail", "rejected"),
    ([issue(suggestion="未知")], [], ["坏词"], "not_evaluated", "no_accepted_golden"),
    ([issue(suggestion=None)], ["正词"], [], "not_evaluated", "invalid_suggestion"),
    ([issue(suggestion=42)], ["正词"], [], "not_evaluated", "invalid_suggestion"),
    ([issue()], ["正词"], [], "not_evaluated", "invalid_suggestion"),
    # 较大 original 保留两侧上下文，可与较短的人工目标改法精确比较。
    ([issue(original="乙错词。", start=5, end=9, suggestion="乙正词。")], ["正词"], [], "pass", "all_accepted"),
    ([issue(original="乙错词。", start=5, end=9, suggestion="乙坏词。")], ["正词"], ["坏词"], "fail", "rejected"),
    ([issue(original="乙错词。", start=5, end=9, suggestion="乙。")], [""], [], "pass", "all_accepted"),
    # 目标内更短片段的替换也必须产生同一目标上下文。
    ([issue(original="错", start=6, end=7, suggestion="正")], ["正词"], [], "pass", "all_accepted"),
    ([issue(original="错", start=6, end=7, suggestion="正词")], ["正词"], [], "fail", "not_accepted"),
    # 相同 suggestion 文本但坐标跨度不同，不能直接按字符串认定为正确/禁止。
    ([issue(original="乙错词", start=5, end=8, suggestion="正词")], ["正词"], [], "not_evaluated", "incomparable_context"),
    ([issue(original="乙错词。", start=5, end=9, suggestion="甲正词。")], ["正词"], [], "not_evaluated", "incomparable_context"),
    ([issue(original="乙错词。", start=5, end=9, suggestion="乙正词！")], ["正词"], [], "not_evaluated", "incomparable_context"),
    ([issue(suggestion="正词"), issue(suggestion="坏词")], ["正词"], ["坏词"], "fail", "rejected"),
    ([issue(suggestion="坏词"), issue(suggestion="正词")], ["正词"], ["坏词"], "fail", "rejected"),
    ([issue(suggestion="正词"), issue(suggestion=None)], ["正词"], [], "not_evaluated", "invalid_suggestion"),
    ([issue(suggestion=None), issue(suggestion="坏词")], ["正词"], ["坏词"], "fail", "rejected"),
    ([issue(suggestion="正词"), issue(original="错", start=6, end=7, suggestion="正")], ["正词"], [], "pass", "all_accepted"),
])
async def test_suggestion_golden_context_and_all_target_matches(monkeypatch, issues, accepted, rejected, status, reason):
    mock = AsyncMock(return_value=result(issues))
    monkeypatch.setattr(service, "proofread_text", mock)
    report = await service.run_evaluation(
        [snapshot(accepted_suggestions=accepted, rejected_suggestions=rejected)], [model()],
    )
    entry = report.results[0]
    case = entry.cases[0]
    assert case.detected is True and case.detection_status == "pass"
    assert case.suggestion_status == case.status == status and case.suggestion_reason == reason
    assert entry.report_evaluated == 1 and entry.errors == entry.missed == 0
    assert entry.suggestion_evaluated == int(status != "not_evaluated")
    assert entry.suggestion_passed == int(status == "pass")
    assert entry.suggestion_failed == int(status == "fail")
    assert entry.suggestion_not_evaluated == int(status == "not_evaluated")
    mock.assert_awaited_once()  # 不增加 LLM 判官调用。


async def test_suggestion_never_uses_other_occurrence_type_or_unreliable_span(monkeypatch):
    samples = [snapshot(i, accepted_suggestions=["正词"]) for i in range(1, 5)]
    mock = AsyncMock(side_effect=[
        result([issue(start=2, end=4, suggestion="正词")]),
        result([issue(type="grammar", suggestion="正词")]),
        result([issue(start=None, end=None, suggestion="正词")]),
        result([issue(suggestion="正词"), issue(start=None, end=None, suggestion="坏词")]),
    ])
    monkeypatch.setattr(service, "proofread_text", mock)
    entry = (await service.run_evaluation(samples, [model()])).results[0]
    assert [case.detection_status for case in entry.cases] == ["fail", "fail", "error", "error"]
    assert [case.suggestion_reason for case in entry.cases] == ["not_detected", "not_detected", "detection_error", "detection_error"]
    assert entry.suggestion_evaluated == 0 and entry.suggestion_not_evaluated == 4
    assert entry.missed == 2 and entry.errors == 2


async def test_unique_unpositioned_and_unicode_replacement_context(monkeypatch):
    value = snapshot(text="甲\U00020000错词。", start=2, end=4, accepted_suggestions=["\U00020000正词\n"])
    monkeypatch.setattr(service, "proofread_text", AsyncMock(return_value=result([
        issue(original="\U00020000错词。", start=None, end=None, suggestion="\U00020000\U00020000正词\n。"),
    ])))
    entry = (await service.run_evaluation([value], [model()])).results[0]
    assert entry.suggestion_passed == 1 and entry.missed == 0


async def test_maximum_forty_cases_and_four_concurrent(monkeypatch):
    active = peak = calls = 0
    full = asyncio.Event()
    release = asyncio.Event()

    async def mock_proofread(**kwargs):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        if active == 4:
            full.set()
        try:
            await release.wait()
            return result(config_id=kwargs["config_id"])
        finally:
            active -= 1

    monkeypatch.setattr(service, "proofread_text", mock_proofread)
    task = asyncio.create_task(service.run_evaluation([snapshot(i) for i in range(1, 11)],
                                                      [model(i) for i in range(1, 5)]))
    await asyncio.wait_for(full.wait(), 1)
    assert calls == peak == 4
    release.set()
    report = await asyncio.wait_for(task, 1)
    assert calls == 40 and peak == 4
    assert [entry.config_id for entry in report.results] == [1, 2, 3, 4]
    assert all(len(entry.cases) == 10 for entry in report.results)


@pytest.mark.parametrize("limit_name", ["CASE_TIMEOUT_SECONDS", "REQUEST_TIMEOUT_SECONDS"])
async def test_timeout_cancels_requests_without_retry(monkeypatch, limit_name):
    cancelled = 0

    async def blocked(**kwargs):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled += 1

    monkeypatch.setattr(service, "proofread_text", blocked)
    monkeypatch.setattr(service, limit_name, 0.01)
    report = await asyncio.wait_for(service.run_evaluation([snapshot()], [model()]), 1)
    assert report.results[0].errors == 1 and cancelled == 1
    assert report.results[0].cases[0].detected is None
    assert service._provider_slots.get() is None
    assert service._provider_models.get() is None
    assert service._response_checks.get() is None


async def test_actual_provider_calls_share_four_slots_and_no_retry(monkeypatch):
    from app.services.llm.openai_compat import OpenAICompatProvider

    calls = active = peak = 0
    full, release = asyncio.Event(), asyncio.Event()

    async def transport_handler(request):
        nonlocal calls, active, peak
        calls += 1
        active += 1
        peak = max(peak, active)
        if active == 4:
            full.set()
        try:
            await release.wait()
            return httpx.Response(500, text="test provider failure")
        finally:
            active -= 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport_handler), base_url="https://mock.invalid") as client:
        monkeypatch.setattr(OpenAICompatProvider, "_get_shared_client", lambda self: client)
        normal = OpenAICompatProvider("test-key", "https://mock.invalid", "mock", max_retries=3)
        service.configure_evaluation_provider(normal)
        assert normal.max_retries == 3 and len(normal._endpoints) == 2
        token = service._provider_slots.set(asyncio.Semaphore(4))
        models_token = service._provider_models.set({1: "mock"})
        try:
            provider = OpenAICompatProvider("test-key", "https://mock.invalid", "mock", max_retries=3)
            provider.config_id = 1
            service.configure_evaluation_provider(provider)
            assert provider.max_retries == 1 and len(provider._endpoints) == 1
            tasks = [asyncio.create_task(provider.chat([])) for _ in range(9)]
            await asyncio.wait_for(full.wait(), 1)
            assert calls == peak == 4
            release.set()
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(response, RuntimeError) for response in responses)
            assert calls == 9 and peak == 4
        finally:
            service._provider_models.reset(models_token)
            service._provider_slots.reset(token)


@pytest.mark.parametrize("field,values", [
    ("feedback_ids", []), ("config_ids", []), ("feedback_ids", list(range(1, 12))),
    ("config_ids", [1, 2, 3, 4, 5]), ("feedback_ids", [1, 1]), ("config_ids", [1, 1]),
    ("feedback_ids", [True]), ("config_ids", [False]), ("feedback_ids", [1.0]),
    ("config_ids", ["1"]), ("feedback_ids", [0]), ("config_ids", [-1]),
])
def test_request_ids_strict_unique_and_bounded(field, values):
    data = {"feedback_ids": [1], "config_ids": [1], field: values}
    with pytest.raises(ValidationError):
        EvaluationRequest(**data)


@pytest.fixture
async def api_client(client):
    local_app = FastAPI()
    local_app.include_router(api.router, prefix="/api/v1/admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app), base_url="http://testserver") as http:
        yield http, local_app


@pytest.fixture
async def admin(api_client):
    _, local_app = api_client
    async with async_session_factory() as db:
        role = await db.scalar(select(Role).where(Role.code == "super_admin"))
        if role is None:
            role = Role(name="评测超级管理员", code="super_admin")
            db.add(role)
            await db.flush()
        user = User(employee_id=uuid.uuid4().hex, username="评测管理员", password_hash="unused", role_id=role.id)
        db.add(user)
        await db.commit()
    local_app.dependency_overrides[get_current_user] = lambda: user
    return user


@pytest.fixture
async def config(client):
    async with async_session_factory() as db:
        config = LLMConfig(name=uuid.uuid4().hex, model="mock", provider="custom",
                           api_base="https://mock.invalid", api_key="test-key", is_enabled=True)
        db.add(config)
        await db.commit()
        return config


@pytest.fixture
async def mocked_loader(monkeypatch):
    mock = AsyncMock(return_value=[snapshot().model_dump()])
    monkeypatch.setattr(service, "load_confirmed_samples", mock)
    return mock


async def test_api_safe_response_releases_db_and_creates_no_record_or_quota(
    api_client, admin, config, mocked_loader, monkeypatch,
):
    http, local_app = api_client
    sessions = []

    async def tracked_db():
        async with async_session_factory() as db:
            sessions.append(db)
            yield db

    local_app.dependency_overrides[get_db] = tracked_db
    async with async_session_factory() as db:
        before = await db.scalar(select(func.count()).select_from(ProofreadRecord))

    async def mock_proofread(**kwargs):
        assert not sessions[0].in_transaction()
        assert kwargs == dict(text=SOURCE, domain="general", config_id=config.id, user_id=None, depth="standard")
        # loader 后原始数据变动不会污染本次 revision 快照。
        mocked_loader.return_value[0]["revision"] = 8
        return result([issue()], config_id=config.id, raw_error=SECRET_ERROR)

    monkeypatch.setattr(service, "proofread_text", mock_proofread)
    response = await http.post(URL, headers={"Authorization": "Bearer mock"},
                               json={"feedback_ids": [1], "config_ids": [config.id]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["samples"][0]["revision"] == 3 and body["results"][0]["cases"][0]["revision"] == 3
    assert body["results"][0]["cases"][0]["status"] == "pass"
    assert SECRET_ERROR not in response.text
    async with async_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ProofreadRecord)) == before
    assert not await redis_module.redis_client.keys("textmirror:user_daily:*")
    assert await redis_module.redis_client.get(f"textmirror:quality_evaluation:{admin.id}") is None


@pytest.mark.parametrize("reason", ["missing", "disabled", "unconfirmed", "incomplete_loader", "no_samples"])
async def test_preflight_rejects_entire_batch_before_llm(api_client, admin, config, mocked_loader, monkeypatch, reason):
    http, _ = api_client
    ids = [config.id]
    if reason == "missing":
        ids += [9999999]
    elif reason == "disabled":
        async with async_session_factory() as db:
            stored = await db.get(LLMConfig, config.id)
            stored.is_enabled = False
            await db.commit()
    elif reason == "unconfirmed":
        mocked_loader.side_effect = HTTPException(422, SECRET_ERROR)
    elif reason == "incomplete_loader":
        mocked_loader.return_value = [snapshot(2).model_dump()]
    else:
        mocked_loader.return_value = []
    proofread = AsyncMock()
    monkeypatch.setattr(service, "proofread_text", proofread)
    response = await http.post(URL, headers={"Authorization": "Bearer mock"},
                               json={"feedback_ids": [1], "config_ids": ids})
    assert response.status_code == 422, response.text
    assert SECRET_ERROR not in response.text
    proofread.assert_not_called()
    assert await redis_module.redis_client.get(f"textmirror:quality_evaluation:{admin.id}") is None


async def test_anonymous_and_ordinary_user_forbidden(api_client, monkeypatch):
    http, local_app = api_client
    mock = AsyncMock()
    monkeypatch.setattr(service, "proofread_text", mock)
    payload = {"feedback_ids": [1], "config_ids": [1]}
    assert (await http.post(URL, json=payload)).status_code == 403
    async with async_session_factory() as db:
        role = Role(name="评测普通用户", code=uuid.uuid4().hex)
        db.add(role)
        await db.commit()
    local_app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=99, role_id=role.id)
    response = await http.post(URL, json=payload, headers={"Authorization": "Bearer mock"})
    assert response.status_code == 403
    mock.assert_not_called()


async def test_duplicate_click_blocked_but_next_explicit_click_allowed(
    api_client, admin, config, mocked_loader, monkeypatch,
):
    http, _ = api_client
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def blocked(**kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return result(config_id=config.id)

    monkeypatch.setattr(service, "proofread_text", blocked)
    kwargs = dict(headers={"Authorization": "Bearer mock"}, json={"feedback_ids": [1], "config_ids": [config.id]})
    first = asyncio.create_task(http.post(URL, **kwargs))
    await asyncio.wait_for(entered.wait(), 1)
    ttl = await redis_module.redis_client.ttl(f"textmirror:quality_evaluation:{admin.id}")
    assert 0 < ttl <= 300
    second = await http.post(URL, **kwargs)
    assert second.status_code == 409 and calls == 1
    release.set()
    assert (await first).status_code == 200
    assert (await http.post(URL, **kwargs)).status_code == 200 and calls == 2


async def test_lock_release_cannot_delete_new_owner(client):
    redis = redis_module.redis_client
    await redis.set("test-eval-lock", "new-owner", ex=300)
    await api._release_lock(redis, "test-eval-lock", "old-owner")
    assert await redis.get("test-eval-lock") == "new-owner"
    await api._release_lock(redis, "test-eval-lock", "new-owner")
    assert await redis.get("test-eval-lock") is None


async def test_redis_unavailable_fails_closed(api_client, admin, config, monkeypatch):
    http, _ = api_client
    mock = AsyncMock()
    monkeypatch.setattr(service, "proofread_text", mock)

    def broken_redis():
        raise RuntimeError(SECRET_ERROR)

    monkeypatch.setattr(api, "get_redis", broken_redis)
    response = await http.post(URL, headers={"Authorization": "Bearer mock"},
                               json={"feedback_ids": [1], "config_ids": [config.id]})
    assert response.status_code == 503 and SECRET_ERROR not in response.text
    mock.assert_not_called()


async def test_api_provider_failure_only_exposes_fixed_friendly_error(
    api_client, admin, config, mocked_loader, monkeypatch,
):
    http, _ = api_client
    monkeypatch.setattr(service, "proofread_text", AsyncMock(side_effect=RuntimeError(SECRET_ERROR)))
    response = await http.post(URL, headers={"Authorization": "Bearer mock"},
                               json={"feedback_ids": [1], "config_ids": [config.id]})
    assert response.status_code == 200
    assert SECRET_ERROR not in response.text
    case = response.json()["results"][0]["cases"][0]
    assert case["error"] == service.EVALUATION_ERROR and case["status"] == "error" and case["detected"] is None


@pytest.mark.parametrize("value", [
    RuntimeError(SECRET_ERROR), result(coverage={"status": "partial"}), result(coverage=None),
    result(success=False), result(coverage={"status": "unknown"}),
])
async def test_fixed_clean_error_or_partial_never_pass(monkeypatch, capsys, value):
    clean = {"id": "clean", "dim": "零误报", "text": "正确文本", "expect": []}
    mock = AsyncMock(side_effect=value) if isinstance(value, Exception) else AsyncMock(return_value=value)
    monkeypatch.setattr(fixed_eval, "proofread_text", mock)
    monkeypatch.setattr(fixed_eval, "SAMPLES", [clean])
    monkeypatch.delenv("EVAL_CONFIG_ID", raising=False)
    case = await fixed_eval.run_sample(clean, None)
    assert case["status"] == "ERROR"
    assert await fixed_eval.main([]) == 0
    output = capsys.readouterr().out
    assert "[PASS]" not in output and "零误报通过: 0/1" in output and SECRET_ERROR not in output


async def test_fixed_default_complete_sample_keeps_pass(monkeypatch, capsys):
    monkeypatch.setattr(fixed_eval, "proofread_text", AsyncMock(return_value=result()))
    monkeypatch.setattr(fixed_eval, "SAMPLES", [{"id": "clean", "dim": "零误报", "text": "正确", "expect": []}])
    monkeypatch.setenv("EVAL_CONFIG_ID", "7")
    assert await fixed_eval.main([]) == 0
    output = capsys.readouterr().out
    assert "[PASS]" in output and "零误报通过: 1/1" in output and "id=7" in output


@pytest.mark.parametrize("args", [
    ["--feedback-only"], ["--feedback-only", "--config-ids", "1,1"],
    ["--feedback-only", "--config-ids", "1,2,3,4,5"], ["--feedback-only", "--config-ids", "0"],
    ["--feedback-only", "--config-ids", "1.0"], ["--feedback-only", "--config-ids", "1", "--feedback-ids", "1,1"],
    ["--config-ids", "1"],
])
def test_cli_rejects_invalid_ids(monkeypatch, args):
    monkeypatch.delenv("EVAL_CONFIG_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        fixed_eval.parse_args(args)
    assert exc.value.code == 2


def test_cli_single_env_config_and_optional_feedback_ids(monkeypatch):
    monkeypatch.setenv("EVAL_CONFIG_ID", "7")
    args = fixed_eval.parse_args(["--feedback-only"])
    assert args.config_ids == [7] and args.feedback_ids is None
    args = fixed_eval.parse_args(["--feedback-only", "--config-ids", "2", "3", "--feedback-ids", "4,5"])
    assert args.config_ids == [2, 3] and args.feedback_ids == [4, 5]


async def test_cli_feedback_json_only_and_never_runs_fixed_set(monkeypatch, capsys):
    from loguru import logger

    monkeypatch.setattr(service, "proofread_text", AsyncMock(return_value=result([issue(suggestion="坏词")])))
    report = await service.run_evaluation([snapshot(accepted_suggestions=["正词"], rejected_suggestions=["坏词"])], [model()])
    assert report.results[0].suggestion_failed == 1 and report.results[0].missed == 0
    monkeypatch.setattr(fixed_eval, "run_feedback_cli", AsyncMock(return_value=report))
    fixed = AsyncMock(side_effect=AssertionError("fixed set must not run"))
    monkeypatch.setattr(fixed_eval, "run_sample", fixed)
    try:
        assert await fixed_eval.main(["--feedback-only", "--config-ids", "1"]) == 0
        assert json.loads(capsys.readouterr().out) == report.model_dump()
        fixed.assert_not_called()
        monkeypatch.setattr(fixed_eval, "run_feedback_cli", AsyncMock(side_effect=RuntimeError(SECRET_ERROR)))
        assert await fixed_eval.main(["--feedback-only", "--config-ids", "1"]) == 1
        output = capsys.readouterr()
        assert output.out == "" and SECRET_ERROR not in output.err
    finally:
        logger.enable("app")


async def test_cli_prepares_then_closes_transaction_before_model(client, config, mocked_loader, monkeypatch):
    sessions = []
    from app.core import database

    factory = database.async_session_factory

    def tracked_factory():
        db = factory()
        sessions.append(db)
        return db

    monkeypatch.setattr(database, "async_session_factory", tracked_factory)

    async def mock_proofread(**kwargs):
        assert not sessions[0].in_transaction()
        return result(config_id=config.id)

    monkeypatch.setattr(service, "proofread_text", mock_proofread)
    report = await fixed_eval.run_feedback_cli(SimpleNamespace(feedback_ids=None, config_ids=[config.id]))
    assert report.samples[0].revision == 3
    mocked_loader.assert_awaited_once_with(sessions[0], None)


async def test_prepare_preserves_order_and_strips_unapproved_fields(client, config, mocked_loader):
    raw = snapshot(2).model_dump()
    raw["internal_path"] = SECRET_ERROR
    raw["sample"]["raw_error"] = SECRET_ERROR
    mocked_loader.return_value = [snapshot().model_dump(), raw]
    async with async_session_factory() as db:
        samples, models = await service.prepare_evaluation(db, [2, 1], [config.id])
    assert [entry.id for entry in samples] == [2, 1]
    assert SECRET_ERROR not in json.dumps([entry.model_dump() for entry in samples])
    assert models[0].model_dump()["config_name"] == config.name


async def test_all_confirmed_cli_still_enforces_ten_sample_limit(client, config, mocked_loader):
    mocked_loader.return_value = [snapshot(i).model_dump() for i in range(1, 12)]
    async with async_session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            await service.prepare_evaluation(db, None, [config.id])
    assert exc.value.status_code == 422


@pytest.mark.parametrize("accepted,expected_status", [([], "pass"), (["正词"], "fail")])
async def test_real_feedback_loader_requires_confirmation_and_preserves_review_revision(
    api_client, admin, config, monkeypatch, accepted, expected_status,
):
    from app.schemas.quality_feedback import FeedbackReviewRequest, QualityFeedbackCreate
    from app.services.quality_feedback import create_quality_feedback, review_quality_feedback

    http, _ = api_client
    async with async_session_factory() as db:
        record = ProofreadRecord(user_id=admin.id, type="text", domain="general", original_text=SOURCE,
                                 result=result(config_id=config.id))
        db.add(record)
        await db.flush()
        candidate = await create_quality_feedback(db, admin.id, QualityFeedbackCreate(
            record_id=record.id, kind="missed", original="错词", suggestion="正词", issue_type="typo",
            start=6, end=8, note="人工待确认",
        ))
        await db.commit()
        assert candidate.status == "pending" and candidate.sample is None
        feedback_id = candidate.id
    golden = snapshot(accepted_suggestions=accepted).sample.model_dump()
    mock = AsyncMock(return_value=result([issue(suggestion="坏词")], config_id=config.id))
    monkeypatch.setattr(service, "proofread_text", mock)
    kwargs = dict(headers={"Authorization": "Bearer mock"},
                  json={"feedback_ids": [feedback_id], "config_ids": [config.id]})
    assert (await http.post(URL, **kwargs)).status_code == 422
    mock.assert_not_called()
    async with async_session_factory() as db:
        confirmed = await review_quality_feedback(db, feedback_id, admin.id, FeedbackReviewRequest(
            revision=0, status="confirmed", sample=golden, review_note="确认位置",
        ))
        await db.commit()
        assert confirmed.revision == 1
    response = await http.post(URL, **kwargs)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["samples"][0] == {"id": feedback_id, "revision": 1, "sample": golden}
    entry = body["results"][0]
    assert entry["cases"][0]["revision"] == 1
    assert entry["cases"][0]["status"] == expected_status
    assert entry["cases"][0]["suggestion_status"] == ("fail" if accepted else "not_evaluated")
    assert entry["missed"] == 0 and entry["suggestion_failed"] == bool(accepted)
    assert mock.await_count == 1


async def test_disabled_after_snapshot_cannot_fallback_to_active_model(client, config, mocked_loader, monkeypatch):
    from app.services import proofread

    async with async_session_factory() as db:
        samples, models = await service.prepare_evaluation(db, [1], [config.id])
    async with async_session_factory() as db:
        stored = await db.get(LLMConfig, config.id)
        stored.is_enabled = False
        active = LLMConfig(name=uuid.uuid4().hex, model="different-model", provider="custom",
                           api_base="https://mock.invalid", api_key="test-key", is_enabled=True, is_active=True)
        db.add(active)
        await db.commit()
    factory = AsyncMock(side_effect=AssertionError("must not construct fallback provider"))
    monkeypatch.setattr(proofread, "OpenAICompatProvider", factory)
    report = await service.run_evaluation(samples, models)
    assert report.results[0].config_id == config.id and report.results[0].errors == 1
    assert report.results[0].cases[0].detected is None
    factory.assert_not_called()


@pytest.fixture
async def provider_http(client, monkeypatch):
    """走真实 Provider/审校路径，只替换 HTTP 传输和无关词库准备。"""
    from app.services import proofread

    state = SimpleNamespace(requests=[], choices=[{"message": {"content": "[]"}, "finish_reason": "stop"}])

    def handler(request):
        state.requests.append(json.loads(request.content))
        choice = state.choices[min(len(state.requests) - 1, len(state.choices) - 1)]
        if isinstance(choice, Exception):
            raise choice
        return httpx.Response(200, json={"choices": [choice]})

    monkeypatch.setattr(proofread, "decrypt_secret", lambda value: "test-key")
    monkeypatch.setattr("app.services.proofread.provider.decrypt_secret", lambda value: "test-key")
    words = {"sensitive": [], "banned": [], "correction": [], "whitelist": []}
    monkeypatch.setattr(proofread, "_load_all_words", AsyncMock(return_value=(words, words)))
    monkeypatch.setattr("app.services.proofread.words._load_all_words", AsyncMock(return_value=(words, words)))
    monkeypatch.setattr(proofread, "_get_domain_rules", AsyncMock(return_value="测试规则"))
    monkeypatch.setattr("app.services.proofread.prompts._get_domain_rules", AsyncMock(return_value="测试规则"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://mock.invalid") as http:
        monkeypatch.setattr(proofread.OpenAICompatProvider, "_get_shared_client", lambda self: http)
        yield state


@pytest.mark.parametrize("changed_field", ["model", "name"])
async def test_config_change_after_snapshot_checks_actual_provider_before_http(
    config, mocked_loader, provider_http, changed_field,
):
    from app.services import proofread

    mocked_loader.return_value = [snapshot(expectation="no_report").model_dump()]
    async with async_session_factory() as db:
        samples, models = await service.prepare_evaluation(db, [1], [config.id])
    async with async_session_factory() as db:
        stored = await db.get(LLMConfig, config.id)
        setattr(stored, changed_field, "changed-after-snapshot")
        await db.commit()
    report = await service.run_evaluation(samples, models)
    entry = report.results[0]
    assert entry.config_id == config.id and entry.model == config.model and entry.config_name == config.name
    if changed_field == "model":
        assert provider_http.requests == []  # 同 ID 换模型也必须在真实请求之前拒绝。
        assert entry.errors == 1 and entry.no_report_evaluated == 0
        assert entry.cases[0].status == "error" and entry.cases[0].detected is None
        assert entry.cases[0].error == service.EVALUATION_ERROR
    else:
        assert entry.cases[0].status == "pass" and len(provider_http.requests) == 1
    assert service._provider_slots.get() is None
    assert service._provider_models.get() is None
    assert service._response_checks.get() is None

    ordinary = await proofread.proofread_text(SOURCE, config_id=config.id)
    assert service.has_complete_result(ordinary)
    assert provider_http.requests[-1]["model"] == ("changed-after-snapshot" if changed_field == "model" else config.model)


@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter", "tool_calls", None, "", "missing",
                                         SECRET_ERROR])
async def test_evaluation_empty_json_requires_explicit_stop_but_ordinary_is_unchanged(
    config, mocked_loader, provider_http, finish_reason,
):
    from app.services import proofread

    choice = provider_http.choices[0]
    if finish_reason == "missing":
        choice.pop("finish_reason")
    else:
        choice["finish_reason"] = finish_reason
    mocked_loader.return_value = [snapshot(expectation="no_report").model_dump()]
    async with async_session_factory() as db:
        samples, models = await service.prepare_evaluation(db, [1], [config.id])
    report = await service.run_evaluation(samples, models)
    entry = report.results[0]
    assert len(provider_http.requests) == 1  # 不重试截断的合法 []。
    if finish_reason == "stop":
        assert entry.errors == 0 and entry.no_report_evaluated == 1
        assert entry.cases[0].status == "pass"
    else:
        assert entry.errors == 1 and entry.no_report_evaluated == entry.false_positives == 0
        assert entry.cases[0].status == "error" and entry.cases[0].detected is None
        assert entry.cases[0].error == service.EVALUATION_ERROR
    assert SECRET_ERROR not in report.model_dump_json()
    assert service._provider_models.get() is None
    assert service._response_checks.get() is None

    ordinary = await proofread.proofread_text(SOURCE, config_id=config.id)
    assert service.has_complete_result(ordinary) and ordinary["issues"] == []
    assert len(provider_http.requests) == 2


@pytest.mark.parametrize("self_check", [
    {"message": {"content": "[]"}, "finish_reason": "length"},
    RuntimeError("test provider unavailable"),
])
async def test_incomplete_self_check_still_errors_only_its_evaluation_case(
    config, mocked_loader, provider_http, monkeypatch, self_check,
):
    issues = [{"o": original, "t": "typo", "s": "正", "e": "测试", "sv": "error"}
              for original in ("甲", "乙", "尾")]
    provider_http.choices = [
        {"message": {"content": json.dumps(issues)}, "finish_reason": "stop"},
        self_check,
        {"message": {"content": "[]"}, "finish_reason": "stop"},
    ]
    # 固定调用顺序：首 case 首轮完整、自检截断，第二个 case 完整。
    monkeypatch.setattr(service, "MAX_CONCURRENCY", 1)
    mocked_loader.return_value = [snapshot(i, "no_report").model_dump() for i in (1, 2)]
    async with async_session_factory() as db:
        samples, models = await service.prepare_evaluation(db, [1, 2], [config.id])
    report = await service.run_evaluation(samples, models)
    assert len(provider_http.requests) == 3
    assert [case.status for case in report.results[0].cases] == ["error", "pass"]
    assert report.results[0].cases[0].error == service.EVALUATION_ERROR
    assert report.results[0].no_report_evaluated == 1 and report.results[0].errors == 1
    again = await service.run_evaluation(samples, models)
    assert all(case.status == "pass" for case in again.results[0].cases)


@pytest.mark.parametrize("actual_id,actual_model", [(1, "changed-model"), (2, "mock-model")])
async def test_provider_identity_mismatch_never_calls_chat(monkeypatch, actual_id, actual_model):
    chat = AsyncMock()
    provider = SimpleNamespace(config_id=actual_id, model=actual_model, max_retries=3,
                               _endpoints=["/chat/completions"], chat=chat)

    async def mock_proofread(**kwargs):
        service.configure_evaluation_provider(provider)
        await provider.chat([])
        return result()

    monkeypatch.setattr(service, "proofread_text", mock_proofread)
    report = await service.run_evaluation([snapshot(expectation="no_report")], [model()])
    chat.assert_not_called()
    assert report.results[0].cases[0].status == "error"


async def test_parallel_evaluations_and_ordinary_provider_keep_separate_contexts(monkeypatch):
    from app.services.llm.base import LLMResponse

    entered, release = asyncio.Event(), asyncio.Event()
    active = 0

    def provider_for(name, finish_reason="stop"):
        return SimpleNamespace(config_id=1, model=name, max_retries=3, _endpoints=["/one", "/two"],
                               chat=AsyncMock(return_value=LLMResponse("[]", name, {}, finish_reason)))

    async def mock_proofread(**kwargs):
        nonlocal active
        active += 1
        if active == 2:
            entered.set()
        await release.wait()
        provider = provider_for(kwargs["text"])
        service.configure_evaluation_provider(provider)
        await provider.chat([])
        return result()

    monkeypatch.setattr(service, "proofread_text", mock_proofread)
    tasks = [asyncio.create_task(service.run_evaluation(
        [snapshot(expectation="no_report", text=name, start=0, end=1, original=name[0])],
        [model().model_copy(update={"model": name})],
    )) for name in ("first-model", "second-model")]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        ordinary = provider_for("ordinary-model", "length")
        service.configure_evaluation_provider(ordinary)
        assert (await ordinary.chat([])).finish_reason == "length"
        assert ordinary.max_retries == 3 and len(ordinary._endpoints) == 2
        release.set()
        reports = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert all(report.results[0].cases[0].status == "pass" for report in reports)
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert service._provider_models.get() is None
    assert service._response_checks.get() is None


async def test_cancelled_evaluation_restores_context_in_its_own_task(monkeypatch):
    entered = asyncio.Event()
    restored = []

    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    async def evaluate():
        try:
            await service.run_evaluation([snapshot()], [model()])
        finally:
            restored.append((service._provider_slots.get(), service._provider_models.get(),
                             service._response_checks.get()))

    monkeypatch.setattr(service, "proofread_text", blocked)
    task = asyncio.create_task(evaluate())
    try:
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert restored == [(None, None, None)]


async def test_real_provider_factory_applies_limits_only_with_evaluation_context(client, config, monkeypatch):
    from app.services import proofread

    made = []

    def factory(**kwargs):
        value = SimpleNamespace(model=kwargs["model"], max_retries=kwargs["max_retries"],
                                _endpoints=["/v1/chat/completions", "/chat/completions"], chat=AsyncMock())
        made.append(value)
        return value

    monkeypatch.setattr(proofread, "OpenAICompatProvider", factory)
    monkeypatch.setattr(proofread, "decrypt_secret", lambda value: "test-key")
    monkeypatch.setattr("app.services.proofread.provider.OpenAICompatProvider", factory)
    monkeypatch.setattr("app.services.proofread.provider.decrypt_secret", lambda value: "test-key")
    ordinary = await proofread.get_llm_provider(config.id)
    assert ordinary.max_retries == config.max_retries and len(ordinary._endpoints) == 2
    token = service._provider_slots.set(asyncio.Semaphore(4))
    models_token = service._provider_models.set({config.id: config.model})
    try:
        evaluated = await proofread.get_llm_provider(config.id)
        assert evaluated.max_retries == 1 and len(evaluated._endpoints) == 1
        assert evaluated.config_id == config.id and evaluated.model == config.model
    finally:
        service._provider_models.reset(models_token)
        service._provider_slots.reset(token)
    again = await proofread.get_llm_provider(config.id)
    assert again.max_retries == config.max_retries and len(again._endpoints) == 2


@pytest.mark.parametrize("payload", [
    {"feedback_ids": [1], "config_ids": [True]},
    {"feedback_ids": [1.0], "config_ids": [1]},
    {"feedback_ids": [1], "config_ids": [1, 1]},
    {"feedback_ids": list(range(1, 12)), "config_ids": [1]},
    {"feedback_ids": [1], "config_ids": [1, 2, 3, 4, 5]},
    {"feedback_ids": [1], "config_ids": [2147483648]},
])
async def test_http_invalid_boundaries_never_load_samples(api_client, admin, mocked_loader, payload):
    http, _ = api_client
    response = await http.post(URL, headers={"Authorization": "Bearer mock"}, json=payload)
    assert response.status_code == 422
    mocked_loader.assert_not_called()


async def test_cancelled_request_releases_duplicate_click_lock(api_client, admin, config, mocked_loader, monkeypatch):
    http, _ = api_client
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(service, "proofread_text", blocked)
    task = asyncio.create_task(http.post(URL, headers={"Authorization": "Bearer mock"},
                                        json={"feedback_ids": [1], "config_ids": [config.id]}))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert await redis_module.redis_client.get(f"textmirror:quality_evaluation:{admin.id}") is None
