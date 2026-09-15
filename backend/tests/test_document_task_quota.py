"""普通文档退款闭环：SQLite + 共享 fakeredis，禁用 broker/模型/真实 Redis。"""
import asyncio
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import fakeredis
import fakeredis.aioredis
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1 import document as document_api
from app.api.v1 import tasks as tasks_api
from app.celery_app import celery_app
from app.core import rate_limit, task_quota
from app.core import redis as redis_module
from app.core.database import async_session_factory
from app.core.security import create_access_token, hash_api_key
from app.models.api_key import ApiKey
from app.models.proofread import ProofreadRecord
from app.models.proofread_task import ProofreadTask
from app.models.role import Role
from app.models.uploaded_document import UploadedDocument
from app.models.user import User
from app.services import proofread
from app.tasks import proofread_task as worker

BASE = "/api/v1/document/proofread"
TASKS = "/api/v1/tasks"
RESULT = {"issues": [], "total_issues": 0, "chunks_count": 1,
          "usage": {"total_tokens": 5}, "domain": "general"}
# 独立固定 Lua 契约；无 lupa 的日常测试使用同锁模拟，另有真实 EVAL 测试。
REFUND_SCRIPT = (
    "if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end "
    "local ttl = redis.call('PTTL', KEYS[1]) "
    "local c = tonumber(redis.call('GET', KEYS[1]) or '0') "
    "if c > 0 then redis.call('DECRBY', KEYS[1], 1) end "
    "if ttl == -1 then redis.call('SET', KEYS[2], '1') "
    "else redis.call('SET', KEYS[2], '1', 'PX', math.max(ttl, 172800000)) end "
    "return 1"
)


class RefundRedis:
    def __init__(self, backend):
        self.backend = backend
        self.lock = threading.Lock()
        self.eval = Mock(side_effect=self.evaluate)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def evaluate(self, script, numkeys, key, marker):
        assert script == REFUND_SCRIPT and numkeys == 2
        with self.lock:
            if self.backend.exists(marker):
                return 0
            ttl = self.backend.pttl(key)
            if int(self.backend.get(key) or 0) > 0:
                self.backend.decrby(key, 1)
            self.backend.set(marker, "1", px=None if ttl == -1 else max(ttl, 172800000))
            return 1


@pytest.fixture
async def state(client, monkeypatch):
    server = fakeredis.FakeServer()
    await redis_module.redis_client.aclose()
    redis_module.redis_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    redis = fakeredis.FakeRedis(server=server, decode_responses=True)
    refund = RefundRedis(redis)
    monkeypatch.setattr(redis_module.redis_client, "eval", AsyncMock(side_effect=refund.eval))
    monkeypatch.setattr(worker, "_get_sync_redis", lambda: refund)
    dispatch, revoke, key_refund = Mock(), Mock(), Mock()
    engine = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(worker.async_proofread_document, "apply_async", dispatch)
    monkeypatch.setattr(celery_app.control, "revoke", revoke)
    monkeypatch.setattr(worker, "_refund_key_daily_quota", key_refund)
    monkeypatch.setattr(proofread, "proofread_text", engine)
    monkeypatch.setattr(document_api, "proofread_text", engine)
    async with async_session_factory() as db:
        role = Role(name="quota", code=uuid.uuid4().hex)
        admin_role = await db.scalar(select(Role).where(Role.code == "super_admin"))
        if admin_role is None:
            admin_role = Role(name="admin", code="super_admin")
            db.add(admin_role)
        db.add(role)
        await db.flush()
        users = [User(employee_id=uuid.uuid4().hex, username=name, password_hash="unused",
                      role_id=admin_role.id if name == "admin" else role.id,
                      daily_quota=None if name == "admin" else 10)
                 for name in ("owner", "peer", "admin")]
        db.add_all(users)
        await db.flush()
        doc = UploadedDocument(file_id=str(uuid.uuid4()), filename="test.txt", file_ext=".txt",
                               file_size=12, file_path="/tmp/not-read-quota-test.txt", text_length=4,
                               extracted_text="测试文本", owner_kind="user", user_id=users[0].id)
        plaintext = "tm_" + uuid.uuid4().hex
        api_key = ApiKey(user_id=users[0].id, name="test", key_hash=hash_api_key(plaintext),
                         key_prefix=plaintext[:13], key_suffix=plaintext[-4:], is_active=True)
        db.add_all([doc, api_key])
        await db.commit()
    result = SimpleNamespace(owner=users[0], peer=users[1], admin=users[2], doc=doc,
                             headers={"Authorization": f"Bearer {create_access_token(users[0].id)}"},
                             key_headers={"Authorization": f"Bearer {plaintext}"}, api_key=api_key,
                             redis=redis, refund=refund, dispatch=dispatch, revoke=revoke,
                             engine=engine, key_refund=key_refund)
    try:
        yield result
    finally:
        redis.close()


async def load_task(task_id):
    async with async_session_factory() as db:
        return await db.scalar(select(ProofreadTask).where(ProofreadTask.task_id == task_id))


async def submit(client, state, *, headers=None, idempotency=None):
    headers = dict(state.headers if headers is None else headers)
    if idempotency:
        headers["Idempotency-Key"] = idempotency
    response = await client.post(BASE + "/async", json={"file_id": state.doc.file_id}, headers=headers)
    assert response.status_code == 200, response.text
    return await load_task(response.json()["task_id"])


async def cancel(client, state, task, headers=None):
    return await client.post(f"{TASKS}/{task.task_id}/cancel", headers=headers or state.headers)


def fail(task):
    worker.async_proofread_document.on_failure(RuntimeError("final failure"), task.task_id, (task.id,), {}, None)


async def update_task(task, **values):
    async with async_session_factory() as db:
        await db.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(**values))
        await db.commit()


async def test_submission_persists_actual_charge_before_dispatch_and_replay(client, state):
    def inspect(*, args, task_id):
        with Session(worker._get_sync_engine()) as db:
            task = db.get(ProofreadTask, args[0])
            assert task.task_id == task_id and task.status == "PENDING"
            assert state.redis.get(task.params_json["quota_key"]) == "1"
    state.dispatch.side_effect = inspect
    task = await submit(client, state, idempotency="same-request")
    assert (await submit(client, state, idempotency="same-request")).id == task.id
    state.dispatch.assert_called_once()
    response = await client.get(f"{TASKS}/{task.task_id}", headers=state.headers)
    assert "quota_key" not in response.text and "params_json" not in response.text


async def test_pending_cancel_worker_skip_and_failure_callbacks_refund_once(client, state):
    task = await submit(client, state)
    key = task.params_json["quota_key"]
    state.redis.incr(key)  # 另一笔消费不能被重复回调退掉。
    for _ in range(2):
        assert (await cancel(client, state, task)).status_code == 200
        assert worker.async_proofread_document.run(task.id)["skipped"]
        fail(task)
    assert (await load_task(task.task_id)).status == "CANCELLED"
    assert state.redis.get(key) == "1"
    state.revoke.assert_called_once_with(task.task_id, terminate=False)
    state.engine.assert_not_awaited()
    state.key_refund.assert_not_called()


async def test_running_cancel_refunds_only_after_worker_exits(client, state):
    task = await submit(client, state)
    key = task.params_json["quota_key"]
    state.redis.incr(key)
    entered, release = threading.Event(), threading.Event()

    async def engine(**kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        return RESULT

    state.engine.side_effect = engine
    running = asyncio.create_task(asyncio.to_thread(worker.async_proofread_document.run, task.id))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert (await cancel(client, state, task)).status_code == 200
        assert state.redis.get(key) == "2"
    finally:
        release.set()
        result = await asyncio.wait_for(asyncio.shield(running), 7)
    assert result["cancelled"]
    fail(task)
    assert (await cancel(client, state, task)).status_code == 200
    assert state.redis.get(key) == "1"
    async with async_session_factory() as db:
        assert not (await db.scalars(select(ProofreadRecord).where(
            ProofreadRecord.user_id == state.owner.id))).all()
    state.revoke.assert_not_called()


async def test_retrying_cancel_keeps_charge_until_retry_worker_checks_flag(client, state):
    task = await submit(client, state)
    await update_task(task, status="RETRYING")
    assert (await cancel(client, state, task)).status_code == 200
    assert state.redis.get(task.params_json["quota_key"]) == "1"
    worker.async_proofread_document.push_request(retries=1, id=task.task_id)
    try:
        assert worker.async_proofread_document.run(task.id)["cancelled"]
    finally:
        worker.async_proofread_document.pop_request()
    fail(task)
    assert state.redis.get(task.params_json["quota_key"]) == "0"
    state.engine.assert_not_awaited()


@pytest.mark.parametrize("status,code", [("FAILURE", "INVALID_CONFIG"), ("FAILURE", "DOCUMENT_MISSING"),
                                         ("FAILURE", "MODEL_NOT_CONFIGURED"), ("RETRYING", "PROOFREAD_RETRYABLE")])
async def test_final_failure_repeated_callbacks_only_refund_one_charge(client, state, status, code):
    task = await submit(client, state)
    state.redis.incr(task.params_json["quota_key"])
    await update_task(task, status=status, error_code=code)
    fail(task)
    fail(task)
    assert (await load_task(task.task_id)).status == "FAILURE"
    assert state.redis.get(task.params_json["quota_key"]) == "1"


async def test_success_late_failure_callback_or_cancel_does_not_refund(client, state):
    task = await submit(client, state)
    worker.async_proofread_document.run(task.id)
    fail(task)
    await cancel(client, state, task)
    assert (await load_task(task.task_id)).status == "SUCCESS"
    assert state.redis.get(task.params_json["quota_key"]) == "1"
    state.refund.eval.assert_not_called()


@pytest.mark.parametrize("path", ["pending", "running", "failure", "dispatch", "sync"])
async def test_refund_uses_original_day_not_current_day(client, state, monkeypatch, path):
    old_key = f"textmirror:user_daily:{state.owner.id}:20000101"
    today_key = rate_limit._daily_key("user_daily", str(state.owner.id))
    state.redis.set(old_key, 1, ex=172800)
    state.redis.set(today_key, 4, ex=172800)
    monkeypatch.setattr(rate_limit, "_daily_key", lambda *args: old_key)

    def new_day():
        monkeypatch.setattr(rate_limit, "_daily_key", lambda *args: today_key)

    if path in ("dispatch", "sync"):
        def failure(*args, **kwargs):
            new_day()
            raise RuntimeError("unavailable")
        if path == "dispatch":
            state.dispatch.side_effect = failure
        else:
            state.engine.side_effect = failure
        response = await client.post(BASE + ("/async" if path == "dispatch" else ""),
                                     json={"file_id": state.doc.file_id}, headers=state.headers)
        assert response.status_code == 503
    else:
        task = await submit(client, state)
        assert task.params_json["quota_key"] == old_key
        new_day()
        if path == "pending":
            await cancel(client, state, task)
        elif path == "running":
            await update_task(task, cancel_requested=True)
            assert worker.async_proofread_document.run(task.id)["cancelled"]
        else:
            await update_task(task, status="RETRYING")
            fail(task)
        fail(task)
        await cancel(client, state, task)
    assert state.redis.get(old_key) == "1" and state.redis.get(today_key) == "4"


@pytest.mark.parametrize("unbilled", ["unlimited", "admin", "redis_down", "guest"])
async def test_unbilled_task_never_refunds_an_existing_counter(client, state, monkeypatch, unbilled):
    user = state.admin if unbilled == "admin" else state.owner
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    key = rate_limit._daily_key("user_daily", str(user.id))
    state.redis.set(key, 4, ex=172800)
    async with async_session_factory() as db:
        if unbilled == "unlimited":
            await db.execute(update(User).where(User.id == user.id).values(daily_quota=None))
        if unbilled == "guest":
            await db.execute(update(UploadedDocument).where(UploadedDocument.id == state.doc.id).values(
                owner_kind="guest", user_id=None))
            headers = {}
        await db.commit()
    if unbilled == "redis_down":
        monkeypatch.setattr(redis_module.redis_client, "incrby", AsyncMock(side_effect=ConnectionError("down")))
    task = await submit(client, state, headers=headers)
    assert task.params_json["quota_key"] is None
    # 管理员按现有权限取消游客任务；无扣费凭据时不能碰计数。
    admin_headers = {"Authorization": f"Bearer {create_access_token(state.admin.id)}"}
    assert (await cancel(client, state, task, headers=admin_headers)).status_code == 200
    fail(task)
    worker.async_proofread_document.run(task.id)
    assert state.redis.get(key) == "4"
    state.refund.eval.assert_not_called()


@pytest.mark.parametrize("context", ["missing", "none", "wrong_owner"])
async def test_legacy_or_invalid_receipt_never_guesses_from_created_at(client, state, context):
    task = await submit(client, state)
    params = dict(task.params_json)
    if context == "missing":
        params.pop("quota_key")
    else:
        params["quota_key"] = None if context == "none" else f"textmirror:user_daily:{state.peer.id}:20000101"
    old_key = f"textmirror:user_daily:{state.owner.id}:20000101"
    state.redis.set(old_key, 3)
    await update_task(task, params_json=params, created_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    await cancel(client, state, task)
    fail(task)
    assert state.redis.get(old_key) == "3"
    assert state.redis.get(task.params_json["quota_key"]) == "1"
    state.refund.eval.assert_not_called()


async def test_dispatch_failure_replay_and_cancel_share_refund_marker(client, state):
    state.dispatch.side_effect = ConnectionError("broker unavailable")
    response = await client.post(BASE + "/async", headers={**state.headers, "Idempotency-Key": "retry"},
                                 json={"file_id": state.doc.file_id})
    assert response.status_code == 503
    async with async_session_factory() as db:
        task = await db.scalar(select(ProofreadTask).where(ProofreadTask.owner_user_id == state.owner.id))
    assert task.status == "FAILURE"
    key = task.params_json["quota_key"]
    assert state.redis.get(key) == "0"
    state.redis.incr(key)
    fail(task)
    state.dispatch.side_effect = None
    retried = await submit(client, state, idempotency="retry")
    assert retried.id == task.id
    await cancel(client, state, task)
    worker.async_proofread_document.run(task.id)
    fail(task)
    assert state.redis.get(key) == "1"


@pytest.mark.parametrize("delivered_status", ["STARTED", "SUCCESS"])
async def test_dispatch_acknowledgement_loss_does_not_refund_claimed_task(client, state, delivered_status):
    def delivered(*, args, task_id):
        with Session(worker._get_sync_engine()) as db:
            db.execute(update(ProofreadTask).where(ProofreadTask.id == args[0]).values(status=delivered_status))
            db.commit()
        raise ConnectionError("ack lost")
    state.dispatch.side_effect = delivered
    response = await client.post(BASE + "/async", json={"file_id": state.doc.file_id}, headers=state.headers)
    assert response.status_code == 503
    assert state.redis.get(rate_limit._daily_key("user_daily", str(state.owner.id))) == "1"
    state.refund.eval.assert_not_called()


@pytest.mark.parametrize("retry_by", ["cancel", "status", "stream", "worker", "failure"])
async def test_transient_refund_failure_can_be_retried_safely(client, state, retry_by):
    task = await submit(client, state)
    state.refund.eval.side_effect = ConnectionError("Redis unavailable")
    assert (await cancel(client, state, task)).status_code == 200
    assert state.redis.get(task.params_json["quota_key"]) == "1"
    state.refund.eval.side_effect = state.refund.evaluate
    if retry_by == "cancel":
        await cancel(client, state, task)
    elif retry_by in ("status", "stream"):
        suffix = "/stream" if retry_by == "stream" else ""
        response = await client.get(f"{TASKS}/{task.task_id}{suffix}", headers=state.headers)
        assert response.status_code == 200
    elif retry_by == "worker":
        worker.async_proofread_document.run(task.id)
    else:
        fail(task)
    assert state.redis.get(task.params_json["quota_key"]) == "0"


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_document_ownership_is_checked_before_charge(client, state, asynchronous):
    response = await client.post(BASE + ("/async" if asynchronous else ""),
                                 json={"file_id": state.doc.file_id},
                                 headers={"Authorization": f"Bearer {create_access_token(state.peer.id)}"})
    assert response.status_code == 404
    assert state.redis.get(rate_limit._daily_key("user_daily", str(state.peer.id))) is None
    state.dispatch.assert_not_called()
    state.engine.assert_not_awaited()


@pytest.mark.parametrize("task_status", ["PENDING", "FAILURE", "CANCELLED"])
async def test_unauthorized_cancel_and_status_cannot_trigger_refund(client, state, task_status):
    task = await submit(client, state)
    await update_task(task, status=task_status)
    headers = {"Authorization": f"Bearer {create_access_token(state.peer.id)}"}
    assert (await cancel(client, state, task, headers=headers)).status_code == 404
    assert (await client.get(f"{TASKS}/{task.task_id}", headers=headers)).status_code == 404
    state.refund.eval.assert_not_called()


@pytest.mark.parametrize("auth_kind", ["jwt", "api_key"])
async def test_open_document_records_user_receipt_without_expanding_cancel_auth(client, state, auth_kind):
    headers = state.headers if auth_kind == "jwt" else state.key_headers
    response = await client.post("/api/v1/open/documents", headers=headers,
                                 files={"file": ("open.txt", "测试文本".encode(), "text/plain")})
    assert response.status_code == 202, response.text
    task = await load_task(response.json()["job_id"])
    assert task.params_json["quota_key"] == rate_limit._daily_key("user_daily", str(state.owner.id))
    state.redis.incr(task.params_json["quota_key"])
    if auth_kind == "api_key":
        # 密钥本身和归属用户 JWT 均不获得 Web 取消接口授权。
        assert (await cancel(client, state, task)).status_code == 404
        assert (await cancel(client, state, task, headers=state.key_headers)).status_code == 404
        headers = {"Authorization": f"Bearer {create_access_token(state.admin.id)}"}
    assert (await cancel(client, state, task, headers=headers)).status_code == 200
    fail(task)
    worker.async_proofread_document.run(task.id)
    assert state.redis.get(task.params_json["quota_key"]) == "1"
    if auth_kind == "api_key":
        assert state.redis.get(rate_limit._api_key_daily_redis_key(state.api_key)) == "1"
    state.key_refund.assert_not_called()


@pytest.mark.parametrize("rejected", [False, True])
async def test_ttl_failure_preserves_actual_charge_receipt_and_limit(client, state, monkeypatch, rejected):
    monkeypatch.setattr(redis_module.redis_client, "ttl", AsyncMock(side_effect=ConnectionError("ttl down")))
    state.owner.daily_quota = 0 if rejected else 10
    if rejected:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await rate_limit.charge_user_daily_quota(state.owner)
        assert error.value.status_code == 429
    else:
        key = await rate_limit.charge_user_daily_quota(state.owner)
        assert key == rate_limit._daily_key("user_daily", str(state.owner.id))
        await task_quota.refund_document_quota(str(uuid.uuid4()), key)
    assert state.redis.get(rate_limit._daily_key("user_daily", str(state.owner.id))) == "0"


@pytest.mark.parametrize("winner_status", ["STARTED", "SUCCESS", "CANCELLED"])
async def test_pending_cancel_cas_loser_does_not_refund_running_or_successful_task(client, state, monkeypatch, winner_status):
    task = await submit(client, state)
    raced = False

    @asynccontextmanager
    async def racing_session():
        nonlocal raced
        async with async_session_factory() as db:
            execute = db.execute

            async def race(statement, *args, **kwargs):
                nonlocal raced
                if not raced and statement.is_update and statement.compile().params.get("status") == "CANCELLED":
                    raced = True
                    with Session(worker._get_sync_engine()) as other:
                        other.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(status=winner_status))
                        other.commit()
                return await execute(statement, *args, **kwargs)

            monkeypatch.setattr(db, "execute", race)
            yield db

    monkeypatch.setattr(tasks_api, "async_session_factory", racing_session)
    assert (await cancel(client, state, task)).status_code == 200
    assert raced
    fresh = await load_task(task.task_id)
    assert fresh.status == winner_status
    assert fresh.cancel_requested is (winner_status == "STARTED")
    assert state.redis.get(task.params_json["quota_key"]) == ("0" if winner_status == "CANCELLED" else "1")
    state.revoke.assert_not_called()


@pytest.mark.parametrize("failure", ["database", "integrity", "idempotent_race"])
async def test_task_save_failure_refunds_only_its_own_precharge(client, state, monkeypatch, failure):
    winner_id = None
    losing_id = None

    @asynccontextmanager
    async def failing_session():
        async with async_session_factory() as db:
            commit = db.commit

            async def fail_commit():
                nonlocal winner_id, losing_id
                task = next((item for item in db.new if isinstance(item, ProofreadTask)), None)
                if task is None:
                    return await commit()
                losing_id = task.task_id
                if failure == "idempotent_race":
                    winner_id = str(uuid.uuid4())
                    state.redis.incr(task.params_json["quota_key"])
                    with Session(worker._get_sync_engine()) as other:
                        other.add(ProofreadTask(task_id=winner_id, document_id=task.document_id,
                                               owner_kind="user", owner_user_id=state.owner.id,
                                               status="PENDING", params_json=dict(task.params_json),
                                               idempotency_key=task.idempotency_key))
                        other.commit()
                if failure == "database":
                    raise RuntimeError("database unavailable")
                raise IntegrityError("INSERT", {}, RuntimeError("duplicate"))

            monkeypatch.setattr(db, "commit", fail_commit)
            yield db

    monkeypatch.setattr(document_api, "async_session_factory", failing_session)
    if failure == "idempotent_race":
        task = await submit(client, state, idempotency="racing-key")
        assert task.task_id == winner_id
    else:
        with pytest.raises(RuntimeError if failure == "database" else IntegrityError):
            await submit(client, state, idempotency="racing-key")
    key = rate_limit._daily_key("user_daily", str(state.owner.id))
    assert state.redis.get(key) == ("1" if failure == "idempotent_race" else "0")
    assert state.redis.get(f"textmirror:document_refund:{losing_id}") == "1"
    state.dispatch.assert_not_called()
    if winner_id:
        await cancel(client, state, await load_task(winner_id))
        assert state.redis.get(key) == "0"


@pytest.mark.parametrize("phase", ["running_cancel", "dispatch_failure"])
async def test_open_user_refund_shares_marker_without_changing_key_contract(client, state, phase):
    if phase == "dispatch_failure":
        state.dispatch.side_effect = ConnectionError("broker unavailable")
    response = await client.post("/api/v1/open/documents", headers=state.key_headers,
                                 files={"file": ("open.txt", "测试文本".encode(), "text/plain")})
    assert response.status_code == (503 if phase == "dispatch_failure" else 202)
    async with async_session_factory() as db:
        task = await db.scalar(select(ProofreadTask).where(ProofreadTask.owner_user_id == state.owner.id))
    key = task.params_json["quota_key"]
    state.redis.incr(key)
    if phase == "running_cancel":
        await update_task(task, cancel_requested=True)
        assert worker.async_proofread_document.run(task.id)["cancelled"]
    # 重复通知不再退用户额度；现有失败密钥退款仍由既有回调处理。
    fail(task)
    fail(task)
    assert state.redis.get(key) == "1"
    assert state.redis.get(rate_limit._api_key_daily_redis_key(state.api_key)) == (
        "0" if phase == "dispatch_failure" else "1")
    if phase == "running_cancel":
        state.key_refund.assert_not_called()


@pytest.mark.parametrize("initial,ttl", [(None, None), (0, 10), (1, 10), (3, 300000), (3, None)])
async def test_real_lua_shared_sync_async_atomic_refund_and_ttl(client, monkeypatch, initial, ttl):
    pytest.importorskip("lupa", reason="实际 EVAL 需 fakeredis[lua]；容器针对性验证已安装")
    server = fakeredis.FakeServer()
    await redis_module.redis_client.aclose()
    redis_module.redis_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(worker, "_get_sync_redis", lambda: fakeredis.FakeRedis(server=server, decode_responses=True))
    key, task_id = "textmirror:user_daily:123:20000101", str(uuid.uuid4())
    marker = f"textmirror:document_refund:{task_id}"
    if initial is not None:
        sync.set(key, initial, ex=ttl)
    await asyncio.gather(*[task_quota.refund_document_quota(task_id, key) for _ in range(8)],
                         *[asyncio.to_thread(task_quota.refund_document_quota_sync, task_id, key) for _ in range(8)])
    assert sync.get(key) == (None if initial is None else str(max(0, initial - 1)))
    assert sync.get(marker) == "1"
    if initial is not None and ttl is None:
        assert sync.pttl(marker) == -1
    else:
        assert sync.pttl(marker) > 172790000
        if initial is not None:
            assert 0 < sync.pttl(key) <= ttl * 1000
            assert sync.pttl(marker) >= sync.pttl(key)
    sync.close()
