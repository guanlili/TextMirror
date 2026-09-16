"""Collaboration API/worker contracts; no external broker, Redis, or model calls."""
import asyncio
import copy
import json
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import fakeredis
import fakeredis.aioredis
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1 import proofread as proofread_api
from app.api.v1 import tasks as tasks_api
from app.celery_app import celery_app
from app.core import rate_limit
from app.core import redis as redis_module
from app.core.database import async_session_factory, get_db
from app.core.dependencies import get_current_user, get_current_user_optional
from app.core.secret_crypto import encrypt_secret
from app.core.security import create_access_token
from app.main import app
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.proofread_task import ProofreadTask
from app.models.role import Permission, Role, RolePermission
from app.models.user import User
from app.schemas.collaboration import CollaborationReport, CollaborationResult
from app.services import collaboration
from app.tasks import collaboration_task as worker
from app.tasks import proofread_task

BASE = "/api/v1/proofread/collaborate"
TASKS = "/api/v1/tasks"
TEXT = "原文快照：甲\U0001f600错词，乙正确。"
RUN_COLLABORATION = collaboration.run_collaboration

# fakeredis is installed without lupa. Pin the actual Lua contract independently
# and emulate its atomic operations against a shared FakeServer, never real Redis.
REFUND_SCRIPT = (
    "if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end "
    "local c = tonumber(redis.call('GET', KEYS[1]) or '0') "
    "if c > 0 then redis.call('DECRBY', KEYS[1], 1) end "
    "redis.call('SET', KEYS[2], '1', 'EX', 172800) return 1"
)


class RefundRedis:
    def __init__(self, backend):
        self.backend = backend
        self.lock = threading.Lock()
        self.eval = Mock(side_effect=self._eval)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def _eval(self, script, numkeys, quota_key, marker):
        assert " ".join(script.split()) == " ".join(REFUND_SCRIPT.split())
        assert numkeys == 2
        with self.lock:
            if self.backend.exists(marker):
                return 0
            if int(self.backend.get(quota_key) or 0) > 0:
                self.backend.decrby(quota_key, 1)
            self.backend.set(marker, "1", ex=172800)
            return 1


@pytest.fixture
async def actors(client, monkeypatch):
    server = fakeredis.FakeServer()
    await redis_module.redis_client.aclose()
    redis_module.redis_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    sync_redis = fakeredis.FakeRedis(server=server, decode_responses=True)
    refund = RefundRedis(sync_redis)
    monkeypatch.setattr(proofread_task, "_get_sync_redis", Mock(return_value=refund))
    dispatch = Mock()
    revoke = Mock()
    charge = AsyncMock(wraps=proofread_api.charge_user_daily_quota)
    engine = AsyncMock(side_effect=AssertionError("test must explicitly configure the engine result"))
    monkeypatch.setattr(worker.async_collaboration, "apply_async", dispatch)
    monkeypatch.setattr(celery_app.control, "revoke", revoke)
    monkeypatch.setattr(proofread_api, "charge_user_daily_quota", charge)
    monkeypatch.setattr(collaboration, "run_collaboration", engine)
    async with async_session_factory() as db:
        users = []
        for name, code in (("owner", "proofread:text"), ("peer", "proofread:text"),
                           ("denied", "proofread:document")):
            role = Role(name=name, code=uuid.uuid4().hex)
            db.add(role)
            await db.flush()
            permission = await db.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(name=code, code=code, type="button")
                db.add(permission)
                await db.flush()
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))
            user = User(employee_id=uuid.uuid4().hex, username=name, password_hash="unused",
                        role_id=role.id, daily_quota=5)
            db.add(user)
            await db.flush()
            users.append(user)
        # Explicit selection in most tests; isolate the active default as well.
        await db.execute(update(LLMConfig).values(is_active=False))
        model = LLMConfig(name=uuid.uuid4().hex, provider="openai", model="collaboration-test-model",
                          api_base="https://model.invalid/v1", api_key=encrypt_secret("private-model-key"),
                          is_enabled=True, is_active=True)
        db.add(model)
        await db.commit()
    state = SimpleNamespace(owner=users[0], peer=users[1], denied=users[2], current=users[0],
                            model=model, dispatch=dispatch, revoke=revoke, charge=charge,
                            engine=engine, redis=sync_redis, refund=refund)
    app.dependency_overrides[get_current_user] = lambda: state.current
    app.dependency_overrides[get_current_user_optional] = lambda: state.current
    try:
        yield state
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_optional, None)
        sync_redis.close()


def payload(actors, **changes):
    return {"text": TEXT, "domain": "general", "config_id": actors.model.id,
            "request_id": str(uuid.uuid4()), **changes}


async def submit(client, actors, **changes):
    response = await client.post(BASE, json=payload(actors, **changes))
    assert response.status_code == 202, response.text
    async with async_session_factory() as db:
        task = await db.scalar(select(ProofreadTask).where(ProofreadTask.task_id == response.json()["task_id"]))
    assert task is not None
    return task


async def status(client, task):
    response = await client.get(f"{TASKS}/{task.task_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    CollaborationReport.model_validate(body["collaboration"])
    assert "params_json" not in body and "text" not in body and "original_text" not in body
    assert "quota_key" not in response.text and "request_hash" not in response.text
    assert "private-model-key" not in response.text and TEXT not in response.text
    return body


async def records(actors):
    async with async_session_factory() as db:
        return list((await db.scalars(select(ProofreadRecord).where(
            ProofreadRecord.user_id == actors.owner.id))).all())


def result_for(actors, *, language="success", consistency="success", reviewer="success"):
    issue = {"original": "错词", "suggestion": "正词", "type": "typo", "severity": "warning",
             "explanation": "请人工确认", "start": TEXT.index("错词"), "end": TEXT.index("错词") + 2}
    report = {
        "status": "complete" if language == consistency == reviewer == "success" else "partial",
        "roles": [{"id": role, "name": role, "status": outcome, "message": "已结束"}
                  for role, outcome in (("rules", "success"), ("language", language),
                                        ("consistency", consistency), ("reviewer", reviewer))],
        "findings": [{**issue, "review_status": "confirmed", "review_note": "与原文核对"}],
        "reviewed_count": 1, "review_limit": 20, "config_id": actors.model.id,
        "model_name": actors.model.model,
    }
    return CollaborationResult.model_validate({
        "issues": [issue], "total_issues": 1, "usage": {"total_tokens": 17}, "domain": "general",
        "config_id": actors.model.id, "collaboration": report,
        "coverage": {"status": "complete", "total_chunks": 1, "completed_chunks": 1, "failed_chunks": []},
    }).model_dump(mode="json")


def set_engine_result(actors, result=None):
    actors.engine.side_effect = None
    actors.engine.return_value = result if result is not None else result_for(actors)


async def test_submission_committed_before_dispatch_and_one_unit_quota(client, actors):
    seen = []

    def inspect_committed(*, args, task_id, retry):
        assert len(args) == 1 and type(args[0]) is int and retry is False
        with Session(proofread_task._get_sync_engine()) as db:
            task = db.get(ProofreadTask, args[0])
            assert task is not None and task.task_id == task_id and task.status == "PENDING"
            assert task.document_id is None and task.owner_user_id == actors.owner.id
            assert task.owner_kind == "user" and task.params_json["kind"] == "collaboration"
            assert task.params_json["text"] == TEXT and task.params_json["config_id"] == actors.model.id
            assert db.scalar(select(func.count()).select_from(ProofreadRecord).where(
                ProofreadRecord.user_id == actors.owner.id)) == 0
            seen.append(task.params_json["quota_key"])

    actors.dispatch.side_effect = inspect_committed
    task = await submit(client, actors)
    assert seen == [rate_limit._daily_key("user_daily", str(actors.owner.id))]
    assert actors.redis.get(seen[0]) == "1"
    assert 0 < actors.redis.ttl(seen[0]) <= 172800
    actors.charge.assert_awaited_once_with(actors.owner)
    actors.engine.assert_not_awaited()
    body = await status(client, task)
    assert body["status"] == "PENDING" and "result" not in body
    assert {role["status"] for role in body["collaboration"]["roles"]} == {"pending"}
    restored = await client.get(f"{BASE}/{task.task_id}")
    assert restored.json() == {"task_id": task.task_id, "text": TEXT, "domain": "general",
                               "config_id": actors.model.id}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"},
                                      {"Authorization": "Bearer tm_not_a_login_token"}])
async def test_submission_requires_login(client, actors, headers):
    app.dependency_overrides.pop(get_current_user)
    response = await client.post(BASE, json=payload(actors), headers=headers)
    assert response.status_code == 401
    actors.dispatch.assert_not_called()
    actors.charge.assert_not_awaited()


async def test_document_permission_does_not_authorize_text_collaboration(client, actors):
    actors.current = actors.denied
    assert (await client.post(BASE, json=payload(actors))).status_code == 403
    actors.dispatch.assert_not_called()
    actors.charge.assert_not_awaited()


@pytest.mark.parametrize("state", ["PENDING", "STARTED", "PROGRESS", "SUCCESS", "FAILURE", "CANCELLED"])
async def test_owner_isolation_in_every_state(client, actors, state):
    task = await submit(client, actors)
    async with async_session_factory() as db:
        await db.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(status=state))
        await db.commit()
    actors.current = actors.peer
    for suffix in ("", "/stream"):
        assert (await client.get(f"{TASKS}/{task.task_id}{suffix}")).status_code == 404
    assert (await client.get(f"{BASE}/{task.task_id}")).status_code == 404
    assert (await client.post(f"{TASKS}/{task.task_id}/cancel")).status_code == 404
    app.dependency_overrides.pop(get_current_user)
    app.dependency_overrides.pop(get_current_user_optional)
    assert (await client.get(f"{BASE}/{task.task_id}")).status_code == 401
    assert (await client.get(f"{TASKS}/{task.task_id}", params={"access_token": task.task_id})).status_code == 404
    assert (await client.post(f"{TASKS}/{task.task_id}/cancel")).status_code == 404
    actors.refund.eval.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"text": ""}, {"text": " \t\n"}, {"text": "字" * 8001}, {"text": None},
    {"domain": "unknown"}, {"config_id": 0}, {"config_id": -1},
    {"request_id": "not-a-uuid"}, {"request_id": None}, {"depth": "deep"},
    {"collaboration": {"status": "complete"}},
])
async def test_validation_does_not_charge_or_dispatch(client, actors, changes):
    response = await client.post(BASE, json=payload(actors, **changes))
    assert response.status_code == 422, response.text
    actors.charge.assert_not_awaited()
    actors.dispatch.assert_not_called()
    assert await records(actors) == []


async def test_request_id_required_and_exact_unicode_limit_accepted(client, actors):
    body = payload(actors)
    body.pop("request_id")
    assert (await client.post(BASE, json=body)).status_code == 422
    task = await submit(client, actors, text="\U0001f600" * 8000)
    assert task.params_json["text"] == "\U0001f600" * 8000
    actors.charge.assert_awaited_once()


@pytest.mark.parametrize("change", [
    "missing", "disabled", "empty_key", "blank_key", "encrypted_blank_key", "unreadable_key", "no_default",
])
async def test_model_validation_precedes_quota(client, actors, change):
    config_id = actors.model.id
    async with async_session_factory() as db:
        model = await db.get(LLMConfig, config_id)
        if change == "missing":
            config_id += 1000000
        elif change == "disabled":
            model.is_enabled = False
        elif change in ("empty_key", "blank_key"):
            model.api_key = "" if change == "empty_key" else " \t\n"
        elif change == "encrypted_blank_key":
            model.api_key = encrypt_secret(" \t\n")
        elif change == "unreadable_key":
            model.api_key = "enc:no-longer-decryptable"
        else:
            model.is_active = False
            config_id = None
        await db.commit()
    assert (await client.post(BASE, json=payload(actors, config_id=config_id))).status_code == 422
    actors.charge.assert_not_awaited()
    actors.dispatch.assert_not_called()
    assert actors.redis.keys("textmirror:user_daily:*") == []


@pytest.mark.parametrize("changed", ["text", "domain", "config_id"])
async def test_idempotency_rejects_changed_parameters_without_recharge(client, actors, changed):
    body = payload(actors)
    first = await client.post(BASE, json=body)
    assert first.status_code == 202
    duplicate = await client.post(BASE, json={**body, "request_id": body["request_id"].upper()})
    assert duplicate.status_code == 202 and duplicate.json()["task_id"] == first.json()["task_id"]
    body[changed] = {"text": TEXT + "改变", "domain": "legal", "config_id": actors.model.id + 1}[changed]
    assert (await client.post(BASE, json=body)).status_code == 409
    actors.dispatch.assert_called_once()
    actors.charge.assert_awaited_once()


async def test_request_id_scoped_per_user_and_only_one_active_per_user(client, actors):
    request_id = str(uuid.uuid4())
    first = await submit(client, actors, request_id=request_id)
    assert (await client.post(BASE, json=payload(actors))).status_code == 409
    actors.current = actors.peer
    peer = await submit(client, actors, request_id=request_id)
    assert peer.task_id != first.task_id and peer.idempotency_key != first.idempotency_key
    actors.current = actors.owner
    assert (await client.post(f"{TASKS}/{first.task_id}/cancel")).status_code == 200
    assert (await submit(client, actors)).id != first.id
    assert actors.charge.await_count == 3


@pytest.mark.parametrize("state", ["SUCCESS", "FAILURE", "CANCELLED"])
async def test_idempotent_replay_survives_terminal_state_and_disabled_model(client, actors, state):
    request_id = str(uuid.uuid4())
    task = await submit(client, actors, request_id=request_id)
    async with async_session_factory() as db:
        await db.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(status=state))
        (await db.get(LLMConfig, actors.model.id)).is_enabled = False
        await db.commit()
    replay = await submit(client, actors, request_id=request_id)
    assert replay.id == task.id and replay.status == state
    actors.charge.assert_awaited_once()
    actors.dispatch.assert_called_once()


async def test_quota_exhaustion_creates_no_task_and_no_record(client, actors):
    actors.owner.daily_quota = 0
    response = await client.post(BASE, json=payload(actors))
    assert response.status_code == 429
    assert actors.redis.get(rate_limit._daily_key("user_daily", str(actors.owner.id))) == "0"
    actors.dispatch.assert_not_called()
    assert await records(actors) == []
    async with async_session_factory() as db:
        assert not (await db.scalars(select(ProofreadTask.id).where(
            ProofreadTask.owner_user_id == actors.owner.id))).all()


async def test_unlimited_quota_returns_none_and_never_attempts_refund(client, actors):
    actors.owner.daily_quota = None
    task = await submit(client, actors)
    assert task.params_json["quota_key"] is None
    await client.post(f"{TASKS}/{task.task_id}/cancel")
    await status(client, task)
    assert actors.redis.keys("textmirror:user_daily:*") == []
    actors.refund.eval.assert_not_called()


async def test_dispatch_failure_is_persisted_sanitized_and_refunded_once(client, actors):
    actors.dispatch.side_effect = ConnectionError("private-model-key secret broker address")
    request_id = str(uuid.uuid4())
    task = await submit(client, actors, request_id=request_id)
    assert task.status == "FAILURE" and task.finished_at is not None
    body = await status(client, task)
    assert body["error"] == "QUEUE_UNAVAILABLE" and "secret" not in body["message"]
    assert {role["status"] for role in body["collaboration"]["roles"]} == {"failed"}
    assert (await submit(client, actors, request_id=request_id)).id == task.id
    await status(client, task)
    assert actors.redis.get(task.params_json["quota_key"]) == "0"
    assert await records(actors) == []
    actors.dispatch.assert_called_once()
    actors.charge.assert_awaited_once()


@pytest.mark.parametrize("integrity_error", [False, True])
async def test_commit_failure_rolls_back_and_refunds_without_dispatch(client, actors, monkeypatch, integrity_error):
    async def failing_db():
        async with async_session_factory() as db:
            async def fail_commit():
                assert any(isinstance(row, ProofreadTask) for row in db.new)
                if integrity_error:
                    raise IntegrityError("INSERT", {}, RuntimeError("duplicate"))
                raise RuntimeError("fake commit unavailable")

            monkeypatch.setattr(db, "commit", fail_commit)
            yield db

    app.dependency_overrides[get_db] = failing_db
    try:
        if integrity_error:
            assert (await client.post(BASE, json=payload(actors))).status_code == 409
        else:
            with pytest.raises(RuntimeError, match="fake commit unavailable"):
                await client.post(BASE, json=payload(actors))
    finally:
        app.dependency_overrides.pop(get_db)
    assert actors.redis.get(rate_limit._daily_key("user_daily", str(actors.owner.id))) == "0"
    actors.dispatch.assert_not_called()
    actors.charge.assert_awaited_once()
    assert await records(actors) == []
    async with async_session_factory() as db:
        assert not (await db.scalars(select(ProofreadTask.id).where(
            ProofreadTask.owner_user_id == actors.owner.id))).all()


async def test_dispatch_exception_after_delivery_cannot_overwrite_success_or_refund(client, actors):
    set_engine_result(actors)

    def delivered_then_disconnected(*, args, task_id, retry):
        worker.async_collaboration.run(args[0])
        raise ConnectionError("fake broker acknowledgement lost")

    actors.dispatch.side_effect = delivered_then_disconnected
    task = await submit(client, actors)
    assert (await status(client, task))["status"] == "SUCCESS"
    assert len(await records(actors)) == 1
    assert actors.redis.get(task.params_json["quota_key"]) == "1"
    actors.refund.eval.assert_not_called()


@pytest.mark.parametrize("default_selection", [False, True])
async def test_worker_uses_selected_config_id_snapshot(client, actors, default_selection):
    task = await submit(client, actors, config_id=None if default_selection else actors.model.id)
    async with async_session_factory() as db:
        (await db.get(LLMConfig, actors.model.id)).is_active = False
        await db.flush()
        db.add(LLMConfig(name=uuid.uuid4().hex, provider="openai", api_base="https://other.invalid/v1",
                         model="new-default", api_key=encrypt_secret("other-key"),
                         is_enabled=True, is_active=True))
        await db.commit()
    set_engine_result(actors)
    worker.async_collaboration.run(task.id)
    call = actors.engine.call_args
    assert call.args == (TEXT,)
    assert call.kwargs["config_id"] == actors.model.id
    assert call.kwargs["domain"] == "general" and call.kwargs["user_id"] == actors.owner.id
    assert asyncio.iscoroutinefunction(call.kwargs["on_progress"])
    assert (await status(client, task))["status"] == "SUCCESS"


@pytest.mark.parametrize("language,consistency,reviewer", [
    ("success", "success", "success"), ("success", "failed", "success"),
    ("failed", "success", "success"), ("success", "success", "failed"),
])
async def test_worker_complete_and_partial_success_count_one(client, actors, language, consistency, reviewer):
    result = result_for(actors, language=language, consistency=consistency, reviewer=reviewer)
    set_engine_result(actors, result)
    task = await submit(client, actors)
    assert await records(actors) == []
    worker.async_collaboration.run(task.id)
    worker.async_collaboration.run(task.id)
    body = await status(client, task)
    assert body["status"] == "SUCCESS" and body["progress"] == 100
    assert body["collaboration"] == result["collaboration"]
    assert body["result"]["collaboration"] == result["collaboration"]
    saved = await records(actors)
    assert len(saved) == 1 and saved[0].id == body["result"]["record_id"]
    assert saved[0].original_text == TEXT and saved[0].modified_text is None
    assert saved[0].type == "text" and saved[0].quota_weight == 1
    assert saved[0].result["collaboration"] == result["collaboration"]
    assert saved[0].token_usage == result["usage"]
    assert actors.redis.get(task.params_json["quota_key"]) == "1"
    actors.engine.assert_awaited_once()
    actors.charge.assert_awaited_once()
    actors.refund.eval.assert_not_called()
    await client.post(f"{TASKS}/{task.task_id}/cancel")
    assert (await status(client, task))["status"] == "SUCCESS"
    assert actors.redis.get(task.params_json["quota_key"]) == "1"


async def test_progress_is_saved_without_record_or_input_leakage(client, actors):
    task = await submit(client, actors)
    progress = copy.deepcopy(task.result_json["collaboration"])
    progress["roles"][1]["status"] = "running"
    observed = []

    async def engine(text, **kwargs):
        await kwargs["on_progress"]({"collaboration": progress, "progress": 47, "message": "检测中"})
        with Session(proofread_task._get_sync_engine()) as db:
            current = db.get(ProofreadTask, task.id)
            assert current.status == "PROGRESS" and current.progress == 47
            assert current.result_json == {"collaboration": progress}
            assert db.scalar(select(func.count()).select_from(ProofreadRecord).where(
                ProofreadRecord.user_id == actors.owner.id)) == 0
            from app.api.v1.tasks import _build_status_payload
            observed.append(_build_status_payload(task.task_id, current))
        return result_for(actors)

    actors.engine.side_effect = engine
    worker.async_collaboration.run(task.id)
    assert observed[0]["collaboration"] == progress and "result" not in observed[0]
    assert TEXT not in json.dumps(observed, ensure_ascii=False)
    assert (await status(client, task))["status"] == "SUCCESS"


@pytest.mark.parametrize("failure", ["both_detectors", "runtime", "timeout", "invalid_result"])
async def test_worker_failure_refunds_and_preserves_safe_progress(client, actors, failure):
    task = await submit(client, actors)
    report = result_for(actors, language="failed", consistency="failed", reviewer="skipped")["collaboration"]

    async def engine(text, **kwargs):
        await kwargs["on_progress"]({"collaboration": report, "progress": 60, "message": "检测未完成"})
        if failure == "both_detectors":
            raise collaboration.CollaborationFailed(report, {}, {})
        if failure == "timeout":
            raise TimeoutError("secret upstream address")
        if failure == "runtime":
            raise RuntimeError("private-model-key")
        return {"collaboration": {"status": "invalid"}}

    actors.engine.side_effect = engine
    worker.async_collaboration.run(task.id)
    worker.async_collaboration.run(task.id)
    body = await status(client, task)
    assert body["status"] == "FAILURE" and "result" not in body
    assert body["error"] == ("TIMEOUT" if failure == "timeout" else "COLLABORATION_FAILED")
    assert body["collaboration"] == report
    assert await records(actors) == []
    assert actors.redis.get(task.params_json["quota_key"]) == "0"
    await status(client, task)
    assert actors.redis.get(task.params_json["quota_key"]) == "0"
    actors.engine.assert_awaited_once()


async def test_real_engine_with_no_successful_detector_is_not_billed(client, actors, monkeypatch):
    from app.services import proofread as service

    provider = SimpleNamespace(
        config_id=actors.model.id, model=actors.model.model, default_temperature=0,
        chat=AsyncMock(side_effect=RuntimeError("model unavailable")), close=AsyncMock(),
    )
    monkeypatch.setattr(service, "_gather_preparation", AsyncMock(return_value=(({}, {}, ""), provider)))
    actors.engine.side_effect = RUN_COLLABORATION
    task = await submit(client, actors)
    worker.async_collaboration.run(task.id)
    body = await status(client, task)
    assert body["status"] == "FAILURE"
    assert await records(actors) == []
    assert actors.redis.get(task.params_json["quota_key"]) == "0"
    assert provider.chat.await_count == 2
    provider.close.assert_awaited_once()


async def test_pending_cancel_refunds_once_and_stops_queued_worker(client, actors):
    task = await submit(client, actors)
    assert (await client.post(f"{TASKS}/{task.task_id}/cancel")).status_code == 200
    assert (await client.post(f"{TASKS}/{task.task_id}/cancel")).status_code == 200
    worker.async_collaboration.run(task.id)
    body = await status(client, task)
    assert body["status"] == "CANCELLED" and body["error"] == "USER_CANCELLED"
    assert {role["status"] for role in body["collaboration"]["roles"]} == {"cancelled"}
    assert actors.redis.get(task.params_json["quota_key"]) == "0"
    assert await records(actors) == []
    actors.engine.assert_not_awaited()
    actors.revoke.assert_called_once_with(task.task_id, terminate=False)


@pytest.mark.parametrize("callback_after_cancel", [False, True])
async def test_running_cancel_cannot_publish_record(client, actors, callback_after_cancel):
    task = await submit(client, actors)

    async def engine(text, **kwargs):
        with Session(proofread_task._get_sync_engine()) as db:
            db.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(cancel_requested=True))
            db.commit()
        if callback_after_cancel:
            await kwargs["on_progress"]({"collaboration": task.result_json["collaboration"],
                                         "progress": 80, "message": "不得覆盖取消"})
            pytest.fail("progress callback must stop a cancelled task")
        return result_for(actors)

    actors.engine.side_effect = engine
    worker.async_collaboration.run(task.id)
    assert (await status(client, task))["status"] == "CANCELLED"
    assert await records(actors) == []
    assert actors.redis.get(task.params_json["quota_key"]) == "0"


async def test_cancel_monitor_interrupts_engine_without_progress_callback(client, actors):
    task = await submit(client, actors)
    entered, cancelled = threading.Event(), threading.Event()

    async def engine(text, **kwargs):
        entered.set()
        try:
            # Bounded fallback prevents a broken monitor from leaving a live thread.
            await asyncio.wait_for(asyncio.Future(), timeout=5)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    actors.engine.side_effect = engine
    running = asyncio.create_task(asyncio.to_thread(worker.async_collaboration.run, task.id))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert (await status(client, task))["status"] == "PROGRESS"
        assert (await client.post(f"{TASKS}/{task.task_id}/cancel")).status_code == 200
        await asyncio.wait_for(asyncio.shield(running), timeout=7)
        assert cancelled.is_set(), "cancel monitor must cancel the active engine coroutine"
    finally:
        await asyncio.wait_for(asyncio.shield(running), timeout=7)
    assert (await status(client, task))["status"] == "CANCELLED"
    assert await records(actors) == []
    assert actors.redis.get(task.params_json["quota_key"]) == "0"
    actors.revoke.assert_not_called()


async def test_duplicate_delivery_while_running_claims_only_once(client, actors):
    task = await submit(client, actors)
    entered, release = threading.Event(), threading.Event()

    async def engine(text, **kwargs):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return result_for(actors)

    actors.engine.side_effect = engine
    running = asyncio.create_task(asyncio.to_thread(worker.async_collaboration.run, task.id))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await asyncio.wait_for(asyncio.to_thread(worker.async_collaboration.run, task.id), timeout=3)
        actors.engine.assert_awaited_once()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(running), timeout=5)
    assert (await status(client, task))["status"] == "SUCCESS"
    assert len(await records(actors)) == 1
    assert actors.redis.get(task.params_json["quota_key"]) == "1"


@pytest.mark.parametrize("state,minutes,expired", [
    ("PENDING", 16, True), ("STARTED", 7, True), ("PROGRESS", 7, True),
    ("PENDING", 14, False), ("STARTED", 5, False),
])
async def test_status_poll_expires_only_stale_tasks(client, actors, state, minutes, expired):
    task = await submit(client, actors)
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    async with async_session_factory() as db:
        await db.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(
            status=state, created_at=since, started_at=None if state == "PENDING" else since))
        await db.commit()
    body = await status(client, task)
    assert body["status"] == ("FAILURE" if expired else "PENDING" if state == "PENDING" else "PROGRESS")
    if expired:
        assert body["error"] == "TASK_EXPIRED"
        worker.async_collaboration.run(task.id)
        await status(client, task)
        actors.engine.assert_not_awaited()
    assert actors.redis.get(task.params_json["quota_key"]) == ("0" if expired else "1")
    assert await records(actors) == []


async def test_stale_submission_does_not_block_new_request(client, actors):
    task = await submit(client, actors)
    async with async_session_factory() as db:
        await db.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(
            created_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
        await db.commit()
    fresh = await submit(client, actors)
    assert fresh.id != task.id
    assert (await status(client, task))["error"] == "TASK_EXPIRED"
    assert actors.redis.get(fresh.params_json["quota_key"]) == "1"


@pytest.mark.parametrize("terminal", ["cancel", "failure", "stale"])
async def test_refund_uses_original_submission_date_exactly_once(client, actors, monkeypatch, terminal):
    old_key = f"textmirror:user_daily:{actors.owner.id}:20000101"
    today_key = rate_limit._daily_key("user_daily", str(actors.owner.id))
    monkeypatch.setattr(rate_limit, "_daily_key", lambda prefix, subject: old_key)
    task = await submit(client, actors)
    assert task.params_json["quota_key"] == old_key
    monkeypatch.setattr(rate_limit, "_daily_key", lambda prefix, subject: today_key)
    actors.redis.set(today_key, 4)
    # Another workflow on the old date must not be refunded by duplicate delivery/polls.
    actors.redis.incr(old_key)
    if terminal == "cancel":
        await client.post(f"{TASKS}/{task.task_id}/cancel")
    elif terminal == "failure":
        actors.engine.side_effect = RuntimeError("model unavailable")
        worker.async_collaboration.run(task.id)
    else:
        async with async_session_factory() as db:
            await db.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(
                created_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
            await db.commit()
    for _ in range(2):
        await status(client, task)
        worker.finish_failure(task.id, "REPEATED", "duplicate completion")
        worker.async_collaboration.run(task.id)
    assert actors.redis.get(old_key) == "1" and actors.redis.get(today_key) == "4"
    marker = f"textmirror:collaboration_refund:{task.task_id}"
    assert actors.redis.get(marker) == "1" and 0 < actors.redis.ttl(marker) <= 172800
    assert all(call.args[2] == old_key for call in actors.refund.eval.call_args_list)


@pytest.mark.parametrize("initial", [None, 0, 1, 3])
async def test_refund_lua_contract_once_clamped_and_marker_ttl(client, actors, initial):
    key, task_id = "textmirror:user_daily:unit:20000101", str(uuid.uuid4())
    if initial is not None:
        actors.redis.set(key, initial, ex=100)
    worker.refund_collaboration_quota(task_id, key)
    worker.refund_collaboration_quota(task_id, key)
    assert actors.redis.get(key) == (None if initial is None else str(max(0, initial - 1)))
    assert actors.redis.get(f"textmirror:collaboration_refund:{task_id}") == "1"
    assert 0 < actors.redis.ttl(f"textmirror:collaboration_refund:{task_id}") <= 172800
    if initial is not None:
        assert 0 < actors.redis.ttl(key) <= 100
    assert actors.refund.eval.call_count == 2
    worker.refund_collaboration_quota(str(uuid.uuid4()), None)
    assert actors.refund.eval.call_count == 2


async def test_terminal_poll_retries_transient_refund_failure(client, actors):
    task = await submit(client, actors)
    actors.refund.eval.side_effect = ConnectionError("fake Redis unavailable")
    assert (await client.post(f"{TASKS}/{task.task_id}/cancel")).status_code == 200
    assert actors.redis.get(task.params_json["quota_key"]) == "1"
    actors.refund.eval.side_effect = actors.refund._eval
    assert (await status(client, task))["status"] == "CANCELLED"
    assert actors.redis.get(task.params_json["quota_key"]) == "0"


async def test_terminal_sse_includes_report_without_submission_params(client, actors):
    task = await submit(client, actors)
    await client.post(f"{TASKS}/{task.task_id}/cancel")
    response = await client.get(f"{TASKS}/{task.task_id}/stream")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert len(events) == 1 and events[0]["status"] == "CANCELLED"
    CollaborationReport.model_validate(events[0]["collaboration"])
    assert TEXT not in response.text and "params_json" not in response.text and "quota_key" not in response.text


async def test_sse_releases_auth_session_before_first_event(client, actors, monkeypatch):
    task = await submit(client, actors)
    assert (await client.post(f"{TASKS}/{task.task_id}/cancel")).status_code == 200
    app.dependency_overrides.pop(get_current_user)
    app.dependency_overrides.pop(get_current_user_optional)
    sessions, connections, finished, observed = [], [], [], []

    def capture_connection(session, transaction, connection):
        connections.append(connection)

    async def capture_db():
        async with asynccontextmanager(get_db)() as db:
            sessions.append(db)
            event.listen(db.sync_session, "after_begin", capture_connection)
            try:
                yield db
            finally:
                finished.append(db)
                event.remove(db.sync_session, "after_begin", capture_connection)

    load_task = tasks_api._load_task_with_auth

    async def inspect_ownership(*args, **kwargs):
        assert len(sessions) == 1 and connections
        assert not sessions[0].in_transaction(), "release auth before acquiring a second connection"
        assert all(connection.closed for connection in connections)
        return await load_task(*args, **kwargs)

    monkeypatch.setattr(tasks_api, "_load_task_with_auth", inspect_ownership)
    streaming_response = tasks_api.StreamingResponse

    def inspect_response(content, **kwargs):
        async def inspect_stream():
            async for chunk in content:
                assert len(sessions) == 1 and connections
                assert not finished, "check before request dependency cleanup"
                assert not sessions[0].in_transaction(), "auth transaction must end before the first SSE event"
                assert all(connection.closed for connection in connections)
                observed.append(json.loads(chunk[6:]))
                yield chunk

        return streaming_response(inspect_stream(), **kwargs)

    monkeypatch.setitem(app.dependency_overrides, get_db, capture_db)
    monkeypatch.setattr(tasks_api, "StreamingResponse", inspect_response)
    response = await client.get(
        f"{TASKS}/{task.task_id}/stream",
        headers={"Authorization": f"Bearer {create_access_token(actors.owner.id)}"},
    )
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert len(observed) == 1 and observed[0]["status"] == "CANCELLED"
    CollaborationReport.model_validate(observed[0]["collaboration"])
    assert actors.redis.get(task.params_json["quota_key"]) == "0"


@pytest.mark.parametrize("suffix", ["", "/stream"])
async def test_partial_success_preserves_owner_coverage_and_rejects_other_users(client, actors, suffix):
    result = result_for(actors, consistency="failed")
    result["coverage"] = {
        "status": "partial", "total_chunks": 1, "completed_chunks": 0,
        "failed_chunks": [{"chunk_index": 0, "start": 0, "end": len(TEXT), "text": TEXT,
                           "error_code": "MODEL_ERROR"}],
    }
    set_engine_result(actors, result)
    task = await submit(client, actors)
    worker.async_collaboration.run(task.id)
    assert len(await records(actors)) == 1
    response = await client.get(f"{TASKS}/{task.task_id}{suffix}")
    assert response.status_code == 200
    body = (json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
            if suffix else response.json())
    assert body["status"] == "SUCCESS" and body["result"]["record_id"] is not None
    CollaborationReport.model_validate(body["collaboration"])
    assert body["result"]["coverage"] == result["coverage"]
    assert "params_json" not in response.text and "quota_key" not in response.text
    actors.current = actors.peer
    assert (await client.get(f"{TASKS}/{task.task_id}{suffix}")).status_code == 404


async def test_review_save_versions_and_restore_preserve_readonly_report(client, actors):
    set_engine_result(actors)
    task = await submit(client, actors)
    worker.async_collaboration.run(task.id)
    record = (await records(actors))[0]
    original_result = copy.deepcopy(record.result)
    root = f"/api/v1/history/{record.id}"
    first_response = await client.get(root + "/review")
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    assert first["collaboration"] == original_result["collaboration"]
    accepted = [{**issue, "_accepted": True} for issue in first["issues"]]
    saved = await client.post(root + "/versions", json={"revision": 0, "issues": accepted, "label": "采纳"})
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    assert saved["modified_text"] == TEXT.replace("错词", "正词")
    assert saved["collaboration"] == first["collaboration"]
    version = saved["versions"][0]
    rejected = await client.put(root + "/review", json={"revision": 1, "issues": first["issues"]})
    assert rejected.status_code == 200 and rejected.json()["modified_text"] == TEXT
    restored = await client.put(root + "/review", json={"revision": 2, "issues": version["issues"]})
    assert restored.status_code == 200
    assert restored.json()["collaboration"] == first["collaboration"]
    assert restored.json()["modified_text"] == version["modified_text"]
    assert restored.json()["versions"] == [version]
    assert (await client.get(root + "/review")).json() == restored.json()
    assert (await records(actors))[0].result == original_result
    assert len(await records(actors)) == 1
    assert actors.redis.get(task.params_json["quota_key"]) == "1"
    actors.engine.assert_awaited_once()
    actors.charge.assert_awaited_once()


@pytest.mark.parametrize("endpoint,method", [("review", "put"), ("versions", "post")])
async def test_client_cannot_write_collaboration_report(client, actors, endpoint, method):
    set_engine_result(actors)
    task = await submit(client, actors)
    worker.async_collaboration.run(task.id)
    record = (await records(actors))[0]
    root = f"/api/v1/history/{record.id}"
    before = (await client.get(root + "/review")).json()
    response = await getattr(client, method)(root + "/" + endpoint, json={
        "revision": 0, "issues": before["issues"], "collaboration": before["collaboration"],
    })
    assert response.status_code == 422
    assert (await client.get(root + "/review")).json() == before
    assert (await records(actors))[0].result == record.result
