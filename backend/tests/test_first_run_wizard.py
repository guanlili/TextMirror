"""首次部署向导最小版：首启管理员改密提醒 + 种子密码策略。

must_change_password 信号：Redis system:initial_admin_password_pending 标记
（仅生产 seed 创建随机密码管理员时写入），admin 登录时返回 true、改密成功后清除。
"""
import uuid as _uuid

from app.core import redis as redis_module
from app.core.database import async_session_factory
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User

PASSWORD = "InitPass123!"


async def _get_or_create_user(employee_id: str) -> User:
    """admin 工号被多个测试文件共用（共享 SQLite 库），get-or-create 防唯一冲突"""
    async with async_session_factory() as session:
        from sqlalchemy import select

        existing = (await session.execute(
            select(User).where(User.employee_id == employee_id)
        )).scalar_one_or_none()
        if existing:
            # 复用共享库里的同名用户：密码统一重置为本文件的已知值
            existing.password_hash = hash_password(PASSWORD)
            await session.commit()
            return existing
        role = Role(name="测试角色", code=f"r_{_uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=employee_id,
            username="测试用户",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _login(client, employee_id: str, password: str = PASSWORD):
    resp = await client.post(
        "/api/v1/auth/login", json={"employee_id": employee_id, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_admin_login_flags_must_change_password_with_marker(client):
    await _get_or_create_user("admin")
    await redis_module.redis_client.set("system:initial_admin_password_pending", "1")

    data = await _login(client, "admin")
    assert data["must_change_password"] is True

    # 改密成功后标记清除，再次登录（新密码）不再提醒
    resp = await client.put(
        "/api/v1/auth/password",
        json={"old_password": PASSWORD, "new_password": "NewPass456!"},
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert await redis_module.redis_client.get("system:initial_admin_password_pending") is None

    data = await _login(client, "admin", password="NewPass456!")
    assert data["must_change_password"] is False


async def test_non_admin_never_flagged_even_with_marker(client):
    await _get_or_create_user("emp_001")
    await redis_module.redis_client.set("system:initial_admin_password_pending", "1")

    data = await _login(client, "emp_001")
    assert data["must_change_password"] is False


async def test_admin_login_not_flagged_without_marker(client):
    await _get_or_create_user("admin")
    data = await _login(client, "admin")
    assert data["must_change_password"] is False
