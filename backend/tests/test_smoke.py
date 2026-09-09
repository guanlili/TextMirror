"""冒烟用例：应用可启动、核心契约不回归。"""


async def test_app_lifespan_startup(monkeypatch):
    """ASGITransport 不执行 lifespan，日志配置等启动逻辑需单独覆盖（曾漏过 stderr sink 误传 rotation 的 TypeError）。"""
    from app import main as app_main

    async def _noop():
        return None

    monkeypatch.setattr(app_main, "init_db", _noop)
    monkeypatch.setattr(app_main, "init_redis", _noop)
    monkeypatch.setattr(app_main, "close_db", _noop)
    monkeypatch.setattr(app_main, "close_redis", _noop)
    async with app_main.lifespan(app_main.app):
        pass


async def test_health(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_login_rejects_wrong_credentials(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"employee_id": "no-such-user", "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_open_api_requires_credentials(client):
    resp = await client.post("/api/v1/open/proofread", json={"text": "测试文本"})
    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert detail["code"] == "UNAUTHORIZED"
    assert "message" in detail


async def test_open_api_rejects_invalid_key(client):
    resp = await client.post(
        "/api/v1/open/proofread",
        json={"text": "测试文本"},
        headers={"Authorization": "Bearer tm_invalid_key_for_test"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "INVALID_API_KEY"
