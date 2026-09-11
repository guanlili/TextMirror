"""管理端 API 密钥全量管理：跨用户列表/筛选 + 吊销恢复/调配额/改备注。

恢复上限语义：恢复后活跃数不得超过 API_KEY_MAX_PER_USER（与自助创建同口径）。
"""
import uuid as _uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.security import generate_api_key, hash_password
from app.models.api_key import ApiKey
from app.models.role import Role
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


async def _create_admin() -> User:
    role = await _get_or_create_role("超管", "super_admin")
    async with async_session_factory() as session:
        user = User(
            employee_id=f"adm_{_uuid.uuid4().hex[:8]}",
            username="测试管理员",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_plain_user() -> User:
    role = await _get_or_create_role("普通角色", f"user_{_uuid.uuid4().hex[:8]}")
    async with async_session_factory() as session:
        user = User(
            employee_id=f"emp_{_uuid.uuid4().hex[:8]}",
            username="普通用户",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_key(user_id: int, *, is_active: bool = True, name: str = "测试密钥", **kwargs) -> ApiKey:
    _, prefix, suffix, key_hash = generate_api_key()
    async with async_session_factory() as session:
        key = ApiKey(
            user_id=user_id,
            name=name,
            key_prefix=prefix,
            key_suffix=suffix,
            key_hash=key_hash,
            is_active=is_active,
            **kwargs,
        )
        session.add(key)
        await session.commit()
        await session.refresh(key)
        return key


async def _login(client, employee_id: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login", json={"employee_id": employee_id, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _admin_headers(client, admin: User) -> dict:
    token = await _login(client, admin.employee_id, PASSWORD)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin():
    return await _create_admin()


@pytest.fixture
async def plain_user():
    return await _create_plain_user()


async def test_list_shows_owner_and_crosses_users(client, admin, plain_user):
    mine = await _create_key(admin.id, name="管理员的密钥")
    others = await _create_key(plain_user.id, name="普通用户的密钥")

    resp = await client.get("/api/v1/admin/api-keys", headers=await _admin_headers(client, admin))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 2
    by_id = {item["id"]: item for item in data["items"]}
    assert by_id[others.id]["username"] == "普通用户"
    assert by_id[mine.id]["employee_id"] == admin.employee_id
    # 脱敏展示，不泄漏哈希
    assert all("key_hash" not in item and "..." in item["key_display"] for item in data["items"])


async def test_keyword_search_escapes_like_wildcards(client, admin, plain_user):
    key = await _create_key(plain_user.id, name=f"含%百分号{_uuid.uuid4().hex[:6]}")

    resp = await client.get(
        "/api/v1/admin/api-keys", params={"keyword": "%百分"}, headers=await _admin_headers(client, admin)
    )
    assert resp.status_code == 200
    assert [i["id"] for i in resp.json()["items"]] == [key.id]


async def test_status_filter(client, admin, plain_user):
    active = await _create_key(plain_user.id, name=f"活跃密钥{_uuid.uuid4().hex[:6]}", is_active=True)
    revoked = await _create_key(plain_user.id, name=f"吊销密钥{_uuid.uuid4().hex[:6]}", is_active=False)

    headers = await _admin_headers(client, admin)
    resp = await client.get("/api/v1/admin/api-keys", params={"key_status": "revoked"}, headers=headers)
    assert revoked.id in [i["id"] for i in resp.json()["items"]]
    assert active.id not in [i["id"] for i in resp.json()["items"]]

    resp = await client.get("/api/v1/admin/api-keys", params={"key_status": "active"}, headers=headers)
    assert active.id in [i["id"] for i in resp.json()["items"]]
    assert revoked.id not in [i["id"] for i in resp.json()["items"]]


async def test_requires_admin_permission(client, plain_user):
    await _create_key(plain_user.id)
    token = await _login(client, plain_user.employee_id, PASSWORD)

    resp = await client.get("/api/v1/admin/api-keys", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_revoke_and_restore(client, admin, plain_user):
    key = await _create_key(plain_user.id)
    headers = await _admin_headers(client, admin)

    resp = await client.patch(f"/api/v1/admin/api-keys/{key.id}", json={"is_active": False}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["changes"] == {"is_active": False}

    resp = await client.patch(f"/api/v1/admin/api-keys/{key.id}", json={"is_active": True}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


async def test_restore_rejected_when_user_at_active_limit(client, admin, plain_user):
    for i in range(settings.API_KEY_MAX_PER_USER):
        await _create_key(plain_user.id, name=f"占位{i}")
    revoked = await _create_key(plain_user.id, name="待恢复", is_active=False)

    resp = await client.patch(
        f"/api/v1/admin/api-keys/{revoked.id}", json={"is_active": True}, headers=await _admin_headers(client, admin)
    )
    assert resp.status_code == 400


async def test_update_quota_and_remark(client, admin, plain_user):
    key = await _create_key(plain_user.id, daily_quota=50, remark="旧备注")
    headers = await _admin_headers(client, admin)

    resp = await client.patch(
        f"/api/v1/admin/api-keys/{key.id}",
        json={"daily_quota": 200, "remark": None},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["changes"] == {"daily_quota": 200, "remark": None}

    async with async_session_factory() as session:
        fresh = await session.get(ApiKey, key.id)
        assert fresh.daily_quota == 200
        assert fresh.remark is None


async def test_patch_missing_key_returns_404(client, admin):
    resp = await client.patch(
        "/api/v1/admin/api-keys/99999", json={"remark": "x"}, headers=await _admin_headers(client, admin)
    )
    assert resp.status_code == 404


async def test_patch_without_changes_is_noop(client, admin, plain_user):
    key = await _create_key(plain_user.id, name="无变更密钥", remark="备注")

    resp = await client.patch(
        f"/api/v1/admin/api-keys/{key.id}", json={}, headers=await _admin_headers(client, admin)
    )
    assert resp.status_code == 200
    assert resp.json() == {"message": "无变更", "changes": {}}


async def test_unauthenticated_request_rejected(client):
    resp = await client.get("/api/v1/admin/api-keys")
    assert resp.status_code == 401
