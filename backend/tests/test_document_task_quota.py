"""普通文档退款闭环：SQLite + 共享 fakeredis，禁用 broker/模型/真实 Redis。"""
import asyncio
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, call

import fakeredis
import fakeredis.aioredis
import pytest
from billiard.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from celery.worker.request import Request
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
from app.services import proofread, webhook
from app.tasks import proofread_task as worker

_REAL_RUN_ASYNC = worker._run_async
_REAL_KEY_REFUND = worker._refund_key_daily_quota
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
    dispatch, revoke, key_refund, webhook_dispatch = Mock(), Mock(), Mock(), Mock()
    engine = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(worker.async_proofread_document, "apply_async", dispatch)
    monkeypatch.setattr(celery_app.control, "revoke", revoke)
    monkeypatch.setattr(worker, "_refund_key_daily_quota", key_refund)
    monkeypatch.setattr(webhook, "dispatch_webhook", webhook_dispatch)
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
                             engine=engine, key_refund=key_refund, webhook=webhook_dispatch)
    try:
        yield result
    finally:
        redis.close()


@pytest.fixture
def task_callbacks(monkeypatch):
    task = worker.async_proofread_document
    callbacks = SimpleNamespace(retry=Mock(wraps=task.retry), failure=Mock(wraps=task.on_failure))
    monkeypatch.setattr(task, "retry", callbacks.retry)
    monkeypatch.setattr(task, "on_failure", callbacks.failure)
    return callbacks


@pytest.fixture
def timeout_thread(monkeypatch):
    thread = Mock()
    # 只替换 worker 的模块引用，不影响 SQLite/asyncio 使用的真实 threading.Thread。
    monkeypatch.setattr(worker, "threading", SimpleNamespace(Thread=thread))
    return thread


def document_request(task):
    celery_task = worker.async_proofread_document
    assert celery_task.Request is worker.ProofreadDocumentRequest
    message = SimpleNamespace(headers={"id": task.task_id, "task": celery_task.name},
                              body=((task.id,), {}, None), delivery_info={}, properties={})
    request = celery_task.Request(message, app=celery_app, task=celery_task, decoded=True)
    assert isinstance(request, Request)
    return request


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


async def test_time_budget_cancels_model_and_apply_refunds_once_before_next_task(
    client, state, monkeypatch, task_callbacks,
):
    task = await submit(client, state)
    next_task = await submit(client, state)
    key = task.params_json["quota_key"]
    events = []

    async def slow_model(**kwargs):
        events.append("started")
        try:
            await asyncio.sleep(1)
            return RESULT
        except asyncio.CancelledError:
            events.append("cancelled")
            raise
        finally:
            await asyncio.sleep(0)
            events.append("cleaned")

    state.engine.side_effect = slow_model
    monkeypatch.setattr(worker, "_DOCUMENT_TIME_BUDGET_S", 0.25)
    result = worker.async_proofread_document.apply(args=(task.id,), task_id=task.task_id, throw=False)
    assert result.state == "FAILURE"
    assert isinstance(result.result, TimeoutError)
    assert not isinstance(result.result, worker.ProofreadRetryableError)
    assert events == ["started", "cancelled", "cleaned"]
    fresh = await load_task(task.task_id)
    assert fresh.status == "FAILURE" and fresh.error_code == "TIMEOUT"
    assert fresh.finished_at is not None and fresh.finished_at >= fresh.started_at
    state.engine.assert_awaited_once()
    task_callbacks.failure.assert_called_once_with(ANY, task.task_id, (task.id,), {}, ANY)
    task_callbacks.retry.assert_not_called()
    assert state.redis.get(key) == "1"
    assert state.redis.get(f"textmirror:document_refund:{task.task_id}") == "1"

    fail(task)
    fail(task)
    assert state.redis.get(key) == "1"
    assert (await load_task(task.task_id)).finished_at == fresh.finished_at
    state.engine.side_effect = None
    next_result = worker.async_proofread_document.apply(
        args=(next_task.id,), task_id=next_task.task_id, throw=False,
    )
    assert next_result.state == "SUCCESS"
    completed = await load_task(next_task.task_id)
    assert completed.status == "SUCCESS" and completed.finished_at is not None
    assert completed.result_json["usage"] == RESULT["usage"]
    assert state.engine.await_count == 2
    assert events == ["started", "cancelled", "cleaned"]
    assert state.redis.get(key) == "1"
    assert state.dispatch.call_count == 2
    task_callbacks.retry.assert_not_called()
    state.key_refund.assert_not_called()


@pytest.mark.parametrize("elapsed", [240, 300])
async def test_expired_retry_keeps_started_at_without_awaiting_model(
    client, state, task_callbacks, elapsed,
):
    assert worker._DOCUMENT_TIME_BUDGET_S == 240
    task = await submit(client, state)
    await update_task(task, status="RETRYING", error_code="PROOFREAD_RETRYABLE",
                      started_at=datetime.now(timezone.utc) - timedelta(seconds=elapsed))
    started_at = (await load_task(task.task_id)).started_at
    result = worker.async_proofread_document.apply(
        args=(task.id,), task_id=task.task_id, retries=1, throw=False,
    )
    assert result.state == "FAILURE" and isinstance(result.result, TimeoutError)
    fresh = await load_task(task.task_id)
    assert fresh.status == "FAILURE" and fresh.error_code == "TIMEOUT"
    assert fresh.started_at == started_at
    assert fresh.finished_at is not None and fresh.finished_at >= started_at
    state.engine.assert_not_awaited()
    task_callbacks.retry.assert_not_called()
    task_callbacks.failure.assert_called_once()
    assert state.redis.get(task.params_json["quota_key"]) == "0"
    state.dispatch.assert_called_once()


async def test_successful_autoretry_preserves_first_started_at(client, state, task_callbacks):
    task = await submit(client, state)
    starts = []

    async def transient_model(**kwargs):
        with Session(worker._get_sync_engine()) as db:
            current = db.get(ProofreadTask, task.id)
            assert current.status == "STARTED" and current.finished_at is None
            starts.append(current.started_at)
        assert state.redis.get(task.params_json["quota_key"]) == "1"
        state.refund.eval.assert_not_called()
        if len(starts) == 1:
            raise ConnectionError("temporary model outage")
        return RESULT

    state.engine.side_effect = transient_model
    result = worker.async_proofread_document.apply(args=(task.id,), task_id=task.task_id, throw=False)
    assert result.state == "SUCCESS"
    assert len(starts) == 2 and starts[0] is not None and starts[0] == starts[1]
    fresh = await load_task(task.task_id)
    assert fresh.status == "SUCCESS" and fresh.started_at == starts[0]
    assert fresh.finished_at is not None and fresh.finished_at >= fresh.started_at
    assert fresh.result_json["issues"] == RESULT["issues"]
    assert state.engine.await_count == 2
    task_callbacks.retry.assert_called_once()
    assert isinstance(task_callbacks.retry.call_args.kwargs["exc"], worker.ProofreadRetryableError)
    task_callbacks.failure.assert_not_called()
    state.refund.eval.assert_not_called()
    assert state.redis.get(task.params_json["quota_key"]) == "1"
    state.dispatch.assert_called_once()


async def test_first_claim_does_not_count_queue_time_against_budget(client, state, task_callbacks):
    task = await submit(client, state)
    await update_task(task, created_at=datetime.now(timezone.utc) - timedelta(days=1))
    queued = await load_task(task.task_id)
    assert queued.started_at is None
    before_claim = datetime.now(timezone.utc)
    result = worker.async_proofread_document.apply(args=(task.id,), task_id=task.task_id, throw=False)
    assert result.state == "SUCCESS"
    fresh = await load_task(task.task_id)
    assert fresh.status == "SUCCESS" and fresh.finished_at is not None
    assert fresh.started_at.replace(tzinfo=timezone.utc) >= before_claim
    assert fresh.created_at == queued.created_at
    assert (fresh.started_at - fresh.created_at).total_seconds() > worker._DOCUMENT_TIME_BUDGET_S
    state.engine.assert_awaited_once()
    task_callbacks.retry.assert_not_called()
    task_callbacks.failure.assert_not_called()
    state.refund.eval.assert_not_called()
    assert state.redis.get(task.params_json["quota_key"]) == "1"


async def test_soft_time_limit_is_terminal_without_autoretry(client, state, task_callbacks):
    task = await submit(client, state)
    state.engine.side_effect = SoftTimeLimitExceeded()
    result = worker.async_proofread_document.apply(args=(task.id,), task_id=task.task_id, throw=False)
    assert result.state == "FAILURE" and isinstance(result.result, SoftTimeLimitExceeded)
    assert not isinstance(result.result, worker.ProofreadRetryableError)
    fresh = await load_task(task.task_id)
    assert fresh.status == "FAILURE" and fresh.error_code == "TIMEOUT"
    assert fresh.finished_at is not None and fresh.finished_at >= fresh.started_at
    state.engine.assert_awaited_once()
    task_callbacks.retry.assert_not_called()
    task_callbacks.failure.assert_called_once_with(ANY, task.task_id, (task.id,), {}, ANY)
    assert isinstance(task_callbacks.failure.call_args.args[0], SoftTimeLimitExceeded)
    assert state.redis.get(task.params_json["quota_key"]) == "0"
    state.dispatch.assert_called_once()


@pytest.mark.parametrize("status", ["STARTED", "PROGRESS", "RETRYING"])
async def test_real_request_only_hard_timeout_finishes_and_refunds(
    client, state, monkeypatch, task_callbacks, timeout_thread, status,
):
    task = await submit(client, state)
    await update_task(task, status=status, error_code="PROOFREAD_RETRYABLE", message="retry pending",
                      started_at=datetime.now(timezone.utc) - timedelta(seconds=100))
    started_at = (await load_task(task.task_id)).started_at
    key = task.params_json["quota_key"]
    state.redis.incr(key)
    parent = Mock()
    monkeypatch.setattr(Request, "on_timeout", parent)
    order = Mock()
    order.attach_mock(parent, "parent")
    order.attach_mock(timeout_thread, "thread")
    request = document_request(task)

    request.on_timeout(soft=True, timeout=300)
    assert order.mock_calls == [call.parent(True, 300)]
    timeout_thread.assert_not_called()
    fresh = await load_task(task.task_id)
    assert fresh.status == status and fresh.finished_at is None
    assert fresh.error_code == "PROOFREAD_RETRYABLE" and fresh.started_at == started_at
    assert state.redis.get(key) == "2"
    state.refund.eval.assert_not_called()
    task_callbacks.failure.assert_not_called()

    request.on_timeout(soft=False, timeout=360)
    assert order.mock_calls == [
        call.parent(True, 300), call.parent(False, 360),
        call.thread(target=task_callbacks.failure, args=(ANY, task.task_id, (task.id,), {}, None), daemon=True),
        call.thread().start(),
    ]
    task_callbacks.failure.assert_not_called()
    assert (await load_task(task.task_id)).status == status
    assert state.redis.get(key) == "2"
    thread_kwargs = timeout_thread.call_args.kwargs
    error = thread_kwargs["args"][0]
    assert isinstance(error, TimeLimitExceeded) and error.args == (360,)
    thread_kwargs["target"](*thread_kwargs["args"])
    task_callbacks.failure.assert_called_once_with(error, task.task_id, (task.id,), {}, None)
    fresh = await load_task(task.task_id)
    assert fresh.status == "FAILURE" and fresh.error_code == "TIMEOUT"
    assert fresh.started_at == started_at
    assert fresh.finished_at is not None and fresh.finished_at >= started_at
    assert fresh.message != "retry pending"
    assert state.redis.get(key) == "1"
    assert state.redis.get(f"textmirror:document_refund:{task.task_id}") == "1"

    request.on_timeout(soft=False, timeout=360)
    thread_kwargs = timeout_thread.call_args.kwargs
    thread_kwargs["target"](*thread_kwargs["args"])
    assert timeout_thread.return_value.start.call_count == 2
    assert task_callbacks.failure.call_count == 2
    assert state.redis.get(key) == "1"
    assert (await load_task(task.task_id)).finished_at == fresh.finished_at
    task_callbacks.retry.assert_not_called()
    state.engine.assert_not_awaited()
    state.key_refund.assert_not_called()
    state.dispatch.assert_called_once()


@pytest.mark.parametrize("status", ["SUCCESS", "REVOKED"])
async def test_real_request_late_hard_timeout_does_not_refund_terminal_api_key_task(
    client, state, monkeypatch, task_callbacks, timeout_thread, status,
):
    task = await submit(client, state)
    await update_task(task, status=status, owner_kind="api_key", owner_api_key_id=state.api_key.id,
                      result_json=RESULT, finished_at=datetime.now(timezone.utc))
    completed = await load_task(task.task_id)
    key = task.params_json["quota_key"]
    api_key_quota = rate_limit._api_key_daily_redis_key(state.api_key)
    state.redis.set(api_key_quota, 1)
    parent = Mock()
    monkeypatch.setattr(Request, "on_timeout", parent)
    request = document_request(task)
    request.on_timeout(soft=True, timeout=300)
    timeout_thread.assert_not_called()
    task_callbacks.failure.assert_not_called()
    for _ in range(2):
        request.on_timeout(soft=False, timeout=360)
        thread_kwargs = timeout_thread.call_args.kwargs
        assert thread_kwargs["daemon"] is True
        thread_kwargs["target"](*thread_kwargs["args"])
    assert timeout_thread.return_value.start.call_count == 2
    assert parent.call_args_list == [call(True, 300), call(False, 360), call(False, 360)]
    assert task_callbacks.failure.call_count == 2
    fresh = await load_task(task.task_id)
    assert fresh.status == status and fresh.error_code is None
    assert fresh.finished_at == completed.finished_at and fresh.result_json == RESULT
    assert state.redis.get(key) == "1" and state.redis.get(api_key_quota) == "1"
    state.refund.eval.assert_not_called()
    state.key_refund.assert_not_called()
    state.webhook.assert_not_called()
    state.engine.assert_not_awaited()
    task_callbacks.retry.assert_not_called()


async def test_real_request_hard_timeout_returns_while_failure_callback_is_blocked(
    client, state, monkeypatch, task_callbacks,
):
    task = await submit(client, state)
    await update_task(task, status="STARTED", started_at=datetime.now(timezone.utc))
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    threads, errors = [], []
    parent = Mock()
    monkeypatch.setattr(Request, "on_timeout", parent)

    def thread_factory(**kwargs):
        thread = threading.Thread(**kwargs)
        threads.append(thread)
        return thread

    def blocked_failure(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(5), "测试未释放失败回调"
            worker.ProofreadDocumentTask.on_failure(worker.async_proofread_document, *args, **kwargs)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(worker, "threading", SimpleNamespace(Thread=thread_factory))
    task_callbacks.failure.side_effect = blocked_failure
    request = document_request(task)

    def invoke_timeout():
        try:
            request.on_timeout(soft=False, timeout=360)
        except BaseException as exc:
            errors.append(exc)
        finally:
            returned.set()

    caller = threading.Thread(target=invoke_timeout)
    caller.start()
    try:
        assert entered.wait(2), "失败回调未启动"
        assert returned.wait(0.5), "on_timeout 等待阻塞回调，会延迟 Billiard 终止子进程"
        assert len(threads) == 1 and threads[0].daemon and threads[0].is_alive()
        assert (await load_task(task.task_id)).status == "STARTED"
        assert state.redis.get(task.params_json["quota_key"]) == "1"
        state.refund.eval.assert_not_called()
    finally:
        release.set()
        caller.join(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
    assert not caller.is_alive() and all(not thread.is_alive() for thread in threads)
    assert not errors
    parent.assert_called_once_with(False, 360)
    task_callbacks.failure.assert_called_once_with(ANY, task.task_id, (task.id,), {}, None)
    fresh = await load_task(task.task_id)
    assert fresh.status == "FAILURE" and fresh.error_code == "TIMEOUT" and fresh.finished_at is not None
    assert state.redis.get(task.params_json["quota_key"]) == "0"


@pytest.mark.parametrize("exception_type", [SoftTimeLimitExceeded, KeyboardInterrupt])
async def test_real_run_async_drains_interrupted_root_and_child_before_reusing_loop(monkeypatch, exception_type):
    monkeypatch.setattr(worker, "_run_async_state", threading.local())
    events, tasks = [], {}
    interruption = exception_type()

    def interrupt():
        events.append("interrupted")
        raise interruption

    def propagate_callback_error(context):
        assert context["exception"] is interruption
        raise context["exception"]

    async def child():
        tasks["child"] = asyncio.current_task()
        asyncio.get_running_loop().call_soon(interrupt)
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            events.append("child cleaned")

    async def root():
        loop = asyncio.get_running_loop()
        # asyncio 默认吞掉 callback 中的普通异常；让软超时像 worker 信号一样中断 run_until_complete。
        monkeypatch.setattr(loop, "call_exception_handler", propagate_callback_error)
        tasks["root"] = asyncio.current_task()
        try:
            return await asyncio.wait_for(child(), timeout=10)
        finally:
            await asyncio.sleep(0)
            events.append("root cleaned")

    async def next_call(loop):
        assert asyncio.get_running_loop() is loop
        assert asyncio.all_tasks(loop) == {asyncio.current_task()}
        await asyncio.sleep(0)
        return RESULT

    def run():
        try:
            with pytest.raises(exception_type) as caught:
                _REAL_RUN_ASYNC(root())
            assert caught.value is interruption
            loop = worker._run_async_state.loop
            assert events == ["interrupted", "child cleaned", "root cleaned"]
            assert tasks["root"].cancelled() and tasks["child"].cancelled()
            assert not asyncio.all_tasks(loop)
            assert not loop.is_closed()
            assert _REAL_RUN_ASYNC(next_call(loop)) == RESULT
            assert worker._run_async_state.loop is loop
            assert not asyncio.all_tasks(loop)
            assert events == ["interrupted", "child cleaned", "root cleaned"]
        finally:
            loop = getattr(worker._run_async_state, "loop", None)
            if loop is not None:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
                del worker._run_async_state.loop
                asyncio.set_event_loop(None)

    await asyncio.to_thread(run)


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


@pytest.mark.parametrize("redis_down_first", [False, True])
async def test_api_key_failure_commits_before_refund_and_refunds_and_notifies_once(
    client, state, monkeypatch, redis_down_first,
):
    task = await submit(client, state)
    await update_task(task, status="RETRYING", owner_kind="api_key", owner_api_key_id=state.api_key.id,
                      error_code="PROOFREAD_RETRYABLE", started_at=datetime.now(timezone.utc))
    user_key = task.params_json["quota_key"]
    key_quota = rate_limit._api_key_daily_redis_key(state.api_key)
    state.redis.incr(user_key)
    state.redis.set(key_quota, 2, ex=172800)
    user_marker = f"textmirror:document_refund:{task.task_id}"
    key_marker = f"textmirror:document_key_refund:{task.task_id}"
    snapshots, outcomes, locked_reads = [], [], []
    scalar = Session.scalar

    def track_locked_read(session, statement, *args, **kwargs):
        locked_reads.append(statement._for_update_arg is not None)
        return scalar(session, statement, *args, **kwargs)

    def inspect_refund(script, numkeys, key, marker):
        with Session(worker._get_sync_engine()) as db:
            fresh = db.get(ProofreadTask, task.id)
            snapshots.append((fresh.status, fresh.error_code, fresh.finished_at))
        if redis_down_first and key == key_quota and not outcomes:
            raise ConnectionError("Redis unavailable on first key refund")
        return state.refund.evaluate(script, numkeys, key, marker)

    def refund_key(*args):
        result = _REAL_KEY_REFUND(*args)
        outcomes.append(result)
        return result

    key_refund = Mock(side_effect=refund_key)
    monkeypatch.setattr(worker, "_refund_key_daily_quota", key_refund)
    state.refund.eval.side_effect = inspect_refund
    celery_ids = [str(uuid.uuid4()) for _ in range(3)]
    with monkeypatch.context() as scoped:
        scoped.setattr(Session, "scalar", track_locked_read)
        for index, celery_id in enumerate(celery_ids):
            worker.async_proofread_document.on_failure(
                TimeLimitExceeded(360), celery_id, (task.id,), {}, None,
            )
            if index == 0 and redis_down_first:
                assert state.redis.get(user_key) == "1" and state.redis.get(key_quota) == "2"
                assert state.redis.get(key_marker) is None
                state.webhook.assert_not_called()
    assert locked_reads == [True, True, True]
    assert outcomes == ([False, True, False] if redis_down_first else [True, False, False])
    assert key_refund.call_args_list == [call(state.api_key.id, task.task_id)] * 3
    assert len(snapshots) == 6
    fresh = await load_task(task.task_id)
    assert fresh.status == "FAILURE" and fresh.error_code == "TIMEOUT" and fresh.finished_at is not None
    assert snapshots == [("FAILURE", "TIMEOUT", fresh.finished_at)] * 6
    assert state.redis.get(user_key) == "1" and state.redis.get(key_quota) == "1"
    assert state.redis.get(user_marker) == "1" and state.redis.get(key_marker) == "1"
    assert all(state.redis.get(f"textmirror:document_key_refund:{celery_id}") is None for celery_id in celery_ids)
    state.webhook.assert_called_once_with(state.api_key.id, {
        "event": "document.failed", "api_version": "v1", "job_id": task.task_id, "timestamp": ANY,
        "data": {"error_code": "TIMEOUT", "message": fresh.message},
    })


@pytest.mark.parametrize("status,code", [("CANCELLED", "USER_CANCELLED"), ("FAILURE", "INVALID_CONFIG")])
async def test_api_key_cancel_or_invalid_config_refunds_only_user(client, state, status, code):
    task = await submit(client, state)
    await update_task(task, status=status, error_code=code, owner_kind="api_key", owner_api_key_id=state.api_key.id,
                      finished_at=datetime.now(timezone.utc))
    terminal = await load_task(task.task_id)
    user_key = task.params_json["quota_key"]
    key_quota = rate_limit._api_key_daily_redis_key(state.api_key)
    state.redis.incr(user_key)
    state.redis.set(key_quota, 2)
    fail(task)
    fail(task)
    fresh = await load_task(task.task_id)
    assert fresh.status == status and fresh.error_code == code and fresh.finished_at == terminal.finished_at
    assert state.redis.get(user_key) == "1" and state.redis.get(key_quota) == "2"
    assert state.redis.get(f"textmirror:document_refund:{task.task_id}") == "1"
    assert state.redis.get(f"textmirror:document_key_refund:{task.task_id}") is None
    state.key_refund.assert_not_called()
    state.webhook.assert_not_called()


@pytest.mark.parametrize("owner_kind", ["user", "api_key"])
async def test_success_late_failure_callback_or_cancel_does_not_refund(client, state, owner_kind):
    task = await submit(client, state)
    if owner_kind == "api_key":
        await update_task(task, owner_kind=owner_kind, owner_api_key_id=state.api_key.id)
        state.redis.set(rate_limit._api_key_daily_redis_key(state.api_key), 1)
    worker.async_proofread_document.run(task.id)
    completed = await load_task(task.task_id)
    state.webhook.reset_mock()
    fail(task)
    fail(task)
    await cancel(client, state, task)
    fresh = await load_task(task.task_id)
    assert fresh.status == "SUCCESS" and fresh.result_json == completed.result_json
    assert fresh.finished_at == completed.finished_at
    assert state.redis.get(task.params_json["quota_key"]) == "1"
    if owner_kind == "api_key":
        assert state.redis.get(rate_limit._api_key_daily_redis_key(state.api_key)) == "1"
    state.refund.eval.assert_not_called()
    state.key_refund.assert_not_called()
    state.webhook.assert_not_called()


async def test_success_cas_loses_to_failure_and_rolls_back_proofread_record(client, state, monkeypatch):
    task = await submit(client, state)
    await update_task(task, owner_kind="api_key", owner_api_key_id=state.api_key.id)
    finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    execute, flush = Session.execute, Session.flush
    raced, inserted_record_ids = False, []

    def defer_record_flush(session, *args, **kwargs):
        # SQLite 单写锁：将记录 flush 延到竞争方提交后，但仍在真实 CAS UPDATE 前 INSERT。
        if not raced and any(isinstance(item, ProofreadRecord) for item in session.new):
            return
        return flush(session, *args, **kwargs)

    def race(session, statement, *args, **kwargs):
        nonlocal raced
        if not raced and statement.is_update and statement.compile().params.get("status") == "SUCCESS":
            raced = True
            with Session(worker._get_sync_engine()) as other:
                other.execute(update(ProofreadTask).where(ProofreadTask.id == task.id).values(
                    status="FAILURE", error_code="TIMEOUT", message="hard timeout won", finished_at=finished_at,
                ))
                other.commit()
            records = [item for item in session.new if isinstance(item, ProofreadRecord)]
            assert len(records) == 1
            flush(session)
            inserted_record_ids.extend(record.id for record in records)
        return execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", defer_record_flush)
    monkeypatch.setattr(Session, "execute", race)
    result = worker.async_proofread_document.run(task.id)
    assert raced and len(inserted_record_ids) == 1 and inserted_record_ids[0] is not None
    assert result["skipped"]
    fresh = await load_task(task.task_id)
    assert fresh.status == "FAILURE" and fresh.error_code == "TIMEOUT"
    assert fresh.message == "hard timeout won" and fresh.finished_at == finished_at
    assert fresh.result_json is None
    async with async_session_factory() as db:
        assert not (await db.scalars(select(ProofreadRecord).where(
            ProofreadRecord.user_id == state.owner.id))).all()
    state.engine.assert_awaited_once()
    state.webhook.assert_not_called()


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
