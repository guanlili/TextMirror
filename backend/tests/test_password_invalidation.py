"""密码变更 Token 失效机制：改密后旧 Access/Refresh Token 立即拒绝。

背景：JWT 此前无 iat 与密码变更联动，密码泄露后改密也无法踢掉旧会话
（Refresh 30 天自续命）。users.password_changed_at + iat 校验闭环。
"""
import asyncio
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.security import (
    hash_password,
    is_token_revoked_by_password_change,
)
from app.models.role import Role
from app.models.user import User

OLD_PASSWORD = "OldPass123!"
NEW_PASSWORD = "NewPass456!"


async def _create_loginable_user() -> User:
    async with async_session_factory() as session:
        role_code = f"r_{_uuid.uuid4().hex[:8]}"
        role = Role(name="测试角色", code=role_code)
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"emp_{_uuid.uuid4().hex[:8]}",
            username="测试用户",
            password_hash=hash_password(OLD_PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _login(client, employee_id, password):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"employee_id": employee_id, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ----------------------------------------------------------------------
# 助手单元测试
# ----------------------------------------------------------------------

def test_helper_none_changed_at_allows_token():
    payload = {"iat": datetime.now(timezone.utc).timestamp()}
    assert is_token_revoked_by_password_change(payload, None) is False


def test_helper_rejects_token_issued_before_change():
    changed = datetime.now(timezone.utc)
    payload = {"iat": (changed - timedelta(hours=1)).timestamp()}
    assert is_token_revoked_by_password_change(payload, changed) is True


def test_helper_same_second_token_survives():
    """iat 整秒 vs DB 微秒：同秒签发的 Token（建号后立即登录）不能被误杀。"""
    changed = datetime.now(timezone.utc).replace(microsecond=999999)
    payload = {"iat": changed.timestamp()}  # 同秒内
    assert is_token_revoked_by_password_change(payload, changed) is False


def test_helper_allows_token_issued_after_change():
    changed = datetime.now(timezone.utc)
    payload = {"iat": (changed + timedelta(seconds=1)).timestamp()}
    assert is_token_revoked_by_password_change(payload, changed) is False


def test_helper_naive_datetime_treated_as_utc():
    """SQLite 单测往返丢失 tzinfo：naive 必须按 UTC 解释，否则同刻比较误判。"""
    changed_naive = datetime.utcnow()  # noqa: DTZ003 — 模拟 SQLite naive 往返
    payload = {"iat": (changed_naive - timedelta(seconds=1)).timestamp()}
    assert is_token_revoked_by_password_change(payload, changed_naive) is True


def test_helper_missing_iat_allows_token():
    changed = datetime.now(timezone.utc)
    assert is_token_revoked_by_password_change({}, changed) is False


# ----------------------------------------------------------------------
# API 集成：改密 → 旧 Token 全灭
# 注意：iat 秒粒度容差使「同秒内改密」不失效（设计权衡，见 security.py），
# 测试需间隔 >1s 模拟真实改密时序。
# ----------------------------------------------------------------------

@pytest.fixture
async def logged_in(client):
    user = await _create_loginable_user()
    tokens = await _login(client, user.employee_id, OLD_PASSWORD)
    return user, tokens


async def test_password_change_revokes_access_token(client, logged_in):
    user, tokens = logged_in
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 200

    await asyncio.sleep(1.1)  # 跨过秒边界，避开同秒容差
    resp = await client.put(
        "/api/v1/auth/password",
        headers=auth,
        json={"old_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200, resp.text

    me = await client.get("/api/v1/auth/me", headers=auth)
    assert me.status_code == 401
    assert "密码已变更" in me.json()["detail"]


async def test_password_change_revokes_refresh_token(client, logged_in):
    _, tokens = logged_in
    await asyncio.sleep(1.1)
    resp = await client.put(
        "/api/v1/auth/password",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={"old_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200

    refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refresh.status_code == 401
    assert "密码已变更" in refresh.json()["detail"]


async def test_new_login_after_change_works(client, logged_in):
    user, tokens = logged_in
    await asyncio.sleep(1.1)
    resp = await client.put(
        "/api/v1/auth/password",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={"old_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200

    tokens = await _login(client, user.employee_id, NEW_PASSWORD)
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200


async def test_user_creation_stamps_password_changed_at(client):
    """建号默认时间戳：新用户立刻签发的 Token 不受影响（iat >= 变更时间）。"""
    user = await _create_loginable_user()
    async with async_session_factory() as session:
        fresh = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
        assert fresh.password_changed_at is not None
    # iat 略晚于建号时间 → 有效
    assert is_token_revoked_by_password_change(
        {"iat": datetime.now(timezone.utc).timestamp()},
        fresh.password_changed_at,
    ) is False
