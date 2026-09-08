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

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402

from app.core import redis as redis_module  # noqa: E402
from app.core.database import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402


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
