"""
测试夹具：SQLite 文件库 + fakeredis，全程不依赖外部服务。

环境变量必须在导入 app 之前设置——settings 单例与数据库 engine
均在模块导入时初始化（app/core/config.py、app/core/database.py）。
"""
import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="textmirror-test-")
# 内存 sqlite 每个连接是独立库，会出现 no such table，必须用文件库
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DIR}/test.db"
os.environ["DEBUG"] = "true"
os.environ["UPLOAD_DIR"] = f"{_TMP_DIR}/uploads"

import asyncio  # noqa: E402
import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402

from app.celery_app import celery_app  # noqa: E402
from app.core import redis as redis_module  # noqa: E402
from app.core.database import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402

# 确保所有模型都注册到 Base.metadata，init_db 才能创建对应表
from app.models import (  # noqa: E402,F401
    api_key,
    audit_log,
    dictionary,
    global_word,
    issue_feedback,
    llm_config,
    proofread,
    proofread_task,
    role,
    uploaded_document,
    user,
)

# 测试环境同步执行 Celery 任务，避免启动真实 worker/broker
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


@pytest.fixture(autouse=True)
def _patch_refund_for_fakeredis(monkeypatch):
    """fakeredis 不支持 Lua EVAL，退款改用原子性要求较低的 DECRBY。"""
    from app.core import rate_limit as rate_limit_module
    from app.core.redis import get_redis

    async def _async_refund(api_key_obj, weight: int = 1) -> None:
        if weight <= 0:
            return
        try:
            redis = get_redis()
            await redis.decrby(rate_limit_module._api_key_daily_redis_key(api_key_obj), weight)
        except Exception as e:
            rate_limit_module.logger.error(f"退还密钥日配额 Redis 异常: {e}")

    monkeypatch.setattr(rate_limit_module, "refund_api_key_daily_usage", _async_refund)
    # open.py 在模块导入时直接绑定 refund_api_key_daily_usage，必须同时 patch 该命名空间
    from app.api.v1 import open as open_module

    monkeypatch.setattr(open_module, "refund_api_key_daily_usage", _async_refund)


@pytest.fixture(autouse=True)
def _disable_audit_log_background_writes(monkeypatch):
    """审计日志后台任务会额外占用 SQLite 连接，单测中禁用以避免 database is locked。"""
    from app.services import audit_log as audit_log_module

    def _noop(*args, **kwargs):
        pass

    monkeypatch.setattr(audit_log_module, "record_audit_log", _noop)
    monkeypatch.setattr(audit_log_module, "record_audit_log_sync", _noop)


@pytest.fixture(autouse=True)
def _patch_run_async_for_eager_celery(monkeypatch):
    """Celery eager 模式下测试已在事件循环中，_run_async 需在新线程运行协程。"""
    import threading
    from app.tasks import proofread_task as task_module

    def _run_in_thread(coro):
        result = []
        exception = []

        def runner():
            try:
                result.append(asyncio.run(coro))
            except Exception as e:
                exception.append(e)

        t = threading.Thread(target=runner)
        t.start()
        t.join()
        if exception:
            raise exception[0]
        return result[0]

    monkeypatch.setattr(task_module, "_run_async", _run_in_thread)


@pytest.fixture(autouse=True)
def _reset_celery_sync_engine():
    """每个用例结束后释放 Celery 任务同步引擎，避免 SQLite 连接跨测试泄漏。"""
    yield
    from app.tasks import proofread_task as task_module

    if task_module._sync_engine is not None:
        try:
            task_module._sync_engine.dispose()
        except Exception:
            pass
        task_module._sync_engine = None
        task_module._sync_engine_pid = None


@pytest.fixture
async def client():
    await init_db()
    redis_module.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    await redis_module.redis_client.flushall()
    redis_module.redis_client = None
    # 每个用例一个事件循环，必须释放绑定在旧循环上的连接
    await engine.dispose()
    # 给 aiosqlite 后台线程留出释放 SQLite 文件锁的时间
    await asyncio.sleep(0.05)
