"""管理端安全守卫回归：
1. PUT /admin/users/{id} 的 role_id/is_active 为特权字段，仅超级管理员可修改
   （防止持 admin:users:edit 的普通管理员自我提权或停用超管）
2. GET /admin/system-config/security 只回掩码，不回明文默认密码；
   PUT 收到掩码表示「不修改」，收到新值才落库
"""
import uuid as _uuid

import pytest
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.security import hash_password
from app.models.role import Permission, Role, RolePermission
from app.models.user import User

PASSWORD = "AdminPass123!"


async def _get_or_create_role(name: str, code: str) -> Role:
    async with async_session_factory() as session:
        existing = (await session.execute(select(Role).where(Role.code == code))).scalar_one_or_none()
        if existing:
            return existing
        role = Role(name=name, code=code)
        session.add(role)
        await session.commit()
        await session.refresh(role)
        return role


async def _grant_permission(role_id: int, code: str) -> None:
    async with async_session_factory() as session:
        perm = (await session.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
        if perm is None:
            perm = Permission(name=code, code=code, type="api")
            session.add(perm)
            await session.flush()
        session.add(RolePermission(role_id=role_id, permission_id=perm.id))
        await session.commit()


async def _create_user(role: Role, username: str = "测试用户") -> User:
    async with async_session_factory() as session:
        user = User(
            employee_id=f"emp_{_uuid.uuid4().hex[:8]}",
            username=username,
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _headers(client, user: User) -> dict:
    resp = await client.post(
        "/api/v1/auth/login", json={"employee_id": user.employee_id, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def super_admin():
    role = await _get_or_create_role("超管", "super_admin")
    return await _create_user(role, "超级管理员")


@pytest.fixture
async def users_admin():
    """持 admin:users:edit / admin:settings:edit 但非超管的管理员"""
    role = await _get_or_create_role("用户管理员", f"users_admin_{_uuid.uuid4().hex[:6]}")
    await _grant_permission(role.id, "admin:users:view")
    await _grant_permission(role.id, "admin:users:edit")
    await _grant_permission(role.id, "admin:settings:edit")
    return await _create_user(role, "用户管理员")


@pytest.fixture
async def target_user():
    role = await _get_or_create_role("普通角色", f"user_{_uuid.uuid4().hex[:8]}")
    return await _create_user(role, "目标用户")


# ========== 特权字段守卫 ==========

async def test_non_super_admin_cannot_change_role_id(client, users_admin, target_user):
    super_role = await _get_or_create_role("超管", "super_admin")
    resp = await client.put(
        f"/api/v1/admin/users/{target_user.id}",
        json={"role_id": super_role.id},
        headers=await _headers(client, users_admin),
    )
    assert resp.status_code == 403, resp.text


async def test_non_super_admin_cannot_change_is_active(client, super_admin, users_admin):
    resp = await client.put(
        f"/api/v1/admin/users/{super_admin.id}",
        json={"is_active": False},
        headers=await _headers(client, users_admin),
    )
    assert resp.status_code == 403, resp.text
    # 超管未被停用
    async with async_session_factory() as session:
        fresh = (await session.execute(select(User).where(User.id == super_admin.id))).scalar_one()
        assert fresh.is_active is True


async def test_non_super_admin_can_update_normal_fields(client, users_admin, target_user):
    resp = await client.put(
        f"/api/v1/admin/users/{target_user.id}",
        json={"username": "改名成功", "remark": "普通字段"},
        headers=await _headers(client, users_admin),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == "改名成功"


async def test_super_admin_can_change_role_id(client, super_admin, target_user):
    other_role = await _get_or_create_role("另一角色", f"other_{_uuid.uuid4().hex[:6]}")
    resp = await client.put(
        f"/api/v1/admin/users/{target_user.id}",
        json={"role_id": other_role.id},
        headers=await _headers(client, super_admin),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role_id"] == other_role.id


async def test_super_admin_change_role_id_validates_existence(client, super_admin, target_user):
    resp = await client.put(
        f"/api/v1/admin/users/{target_user.id}",
        json={"role_id": 999999},
        headers=await _headers(client, super_admin),
    )
    assert resp.status_code == 400, resp.text


# ========== 默认密码掩码 ==========

async def test_security_settings_never_returns_plaintext(client, super_admin):
    headers = await _headers(client, super_admin)
    # 先设置真实密码
    resp = await client.put(
        "/api/v1/admin/system-config/security",
        json={"default_password": "RealSecret123!"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_password"] == "******"

    # GET 只回掩码
    resp = await client.get("/api/v1/admin/system-config/security", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["default_password"] == "******"


async def test_security_settings_mask_put_keeps_existing(client, super_admin):
    from app.api.v1.admin.system_config import get_current_default_password

    headers = await _headers(client, super_admin)
    await client.put(
        "/api/v1/admin/system-config/security",
        json={"default_password": "RealSecret123!"},
        headers=headers,
    )
    # 掩码提交 = 不修改
    resp = await client.put(
        "/api/v1/admin/system-config/security",
        json={"default_password": "******"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert await get_current_default_password() == "RealSecret123!"

    # 新值提交 = 生效
    await client.put(
        "/api/v1/admin/system-config/security",
        json={"default_password": "NewSecret456!"},
        headers=headers,
    )
    assert await get_current_default_password() == "NewSecret456!"
