"""一键登录：演示账号免密入口（登录页按钮触发）。

- admin / demo 两个演示账号；站点配置 quick_login_enabled=off 时整体关闭
- 每 IP 每分钟 10 次限流；账号未初始化 404；审计记录 quick_login
"""
import uuid as _uuid

from app.core.database import async_session_factory
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User

PASSWORD = "Passw0rd!"


async def _ensure_user(employee_id: str, role_code: str = "user") -> None:
    async with async_session_factory() as session:
        from sqlalchemy import select

        existing = (await session.execute(
            select(User).where(User.employee_id == employee_id)
        )).scalar_one_or_none()
        if existing:
            return
        role = Role(name=f"角色{employee_id}", code=f"{role_code}_{_uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        session.add(User(
            employee_id=employee_id,
            username=employee_id,
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        ))
        await session.commit()


async def _quick_login(client, account: str):
    return await client.post(f"/api/v1/auth/quick-login?account={account}")


async def test_quick_login_admin_returns_tokens(client):
    await _ensure_user("admin", "super_admin")
    resp = await _quick_login(client, "admin")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["access_token"]
    assert data["refresh_token"]

    # token 可用：拉取当前用户信息
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200
    assert me.json()["employee_id"] == "admin"


async def test_quick_login_demo_returns_tokens(client):
    await _ensure_user("demo")
    resp = await _quick_login(client, "demo")
    assert resp.status_code == 200
    data = resp.json()
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.json()["employee_id"] == "demo"


async def test_quick_login_unknown_account_404(client):
    resp = await _quick_login(client, "someoneelse")
    assert resp.status_code == 404


async def test_quick_login_disabled_by_site_config(client):
    from app.core import redis as redis_module

    await _ensure_user("demo")
    await redis_module.redis_client.set("site:config:quick_login_enabled", "off")
    try:
        resp = await _quick_login(client, "demo")
        assert resp.status_code == 403
        assert "已关闭" in resp.json()["detail"]
    finally:
        await redis_module.redis_client.delete("site:config:quick_login_enabled")


async def test_quick_login_rate_limited_per_ip(client):
    from app.core import redis as redis_module

    await _ensure_user("demo")
    await redis_module.redis_client.flushall()
    codes = []
    for _ in range(12):
        resp = await _quick_login(client, "demo")
        codes.append(resp.status_code)
    assert codes[:10] == [200] * 10
    assert 429 in codes[10:]


async def test_quick_login_uninitialized_account_404(client):
    # demo 未创建时给清晰报错（admin 由共享库的既有测试创建过，用 demo 验证未初始化分支需先确保不存在）
    from sqlalchemy import delete, select

    async with async_session_factory() as session:
        result = (await session.execute(select(User).where(User.employee_id == "demo"))).scalar_one_or_none()
        if result is not None:
            await session.execute(delete(User).where(User.employee_id == "demo"))
            await session.commit()
    resp = await _quick_login(client, "demo")
    assert resp.status_code == 404
    assert "未初始化" in resp.json()["detail"]
