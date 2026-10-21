"""游客记录归属回归：签名 Cookie、记录落库、后台分群统计。"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api.v1 import proofread as proofread_api
from app.core.database import async_session_factory
from app.core.guest_identity import sign_guest_id, verify_guest_cookie
from app.core.security import create_access_token, hash_password
from app.models.proofread import ProofreadRecord
from app.models.proofread_task import ProofreadTask
from app.models.role import Role
from app.models.user import User
from app.services.record_owner import resolve_task_owner

TEXT = "这家公司的业绩蒸蒸日上，未来可期。"
PASSWORD = "GuestPass123!"


def _result():
    return {
        "issues": [],
        "total_issues": 0,
        "chunks_count": 1,
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        "domain": "general",
        "check_types": [],
        "coverage": {"status": "complete", "total_chunks": 1, "completed_chunks": 1},
        "depth": "standard",
    }


@pytest.fixture
def mock_proofread(monkeypatch):
    async def fake(**kwargs):
        return _result()

    monkeypatch.setattr(proofread_api, "proofread_text", fake)
    return fake


async def _records():
    async with async_session_factory() as session:
        return (await session.execute(
            select(ProofreadRecord).order_by(ProofreadRecord.id)
        )).scalars().all()


async def _authenticated_user():
    async with async_session_factory() as session:
        role = Role(name="游客测试角色", code=f"guest_test_{uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"guest_{uuid.uuid4().hex[:8]}",
            username="游客记录测试用户",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id, {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def _admin_headers(client):
    employee_id = f"admin_{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as session:
        role = Role(name="超管", code="super_admin")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=employee_id,
            username="游客统计管理员",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
    resp = await client.post(
        "/api/v1/auth/login", json={"employee_id": employee_id, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_guest_cookie_rejects_tampering():
    signed = sign_guest_id("abc123")
    assert verify_guest_cookie(signed) == "abc123"
    assert verify_guest_cookie("abc123") is None
    assert verify_guest_cookie(None) is None
    tampered = signed[:-1] + ("0" if signed[-1] != "0" else "1")
    assert verify_guest_cookie(tampered) is None


def test_resolve_task_owner_covers_guest_and_api_key():
    guest = SimpleNamespace(owner_kind="guest", owner_guest_id="g1", owner_user_id=None)
    assert resolve_task_owner(guest) == {"user_id": None, "owner_kind": "guest", "guest_id": "g1"}
    assert resolve_task_owner(
        SimpleNamespace(owner_kind="guest", owner_guest_id=None, owner_user_id=None)
    ) is None
    user = SimpleNamespace(owner_kind="user", owner_guest_id=None, owner_user_id=7)
    assert resolve_task_owner(user) == {"user_id": 7, "owner_kind": "user", "guest_id": None}
    api = SimpleNamespace(owner_kind="api_key", owner_guest_id=None, owner_user_id=7)
    assert resolve_task_owner(api) == {"user_id": 7, "owner_kind": "api_key", "guest_id": None}


async def test_guest_proofread_persists_records_with_stable_signed_cookie(
    client, mock_proofread,
):
    first = await client.post("/api/v1/proofread/text", json={"text": TEXT})
    assert first.status_code == 200, first.text
    assert "tm_guest_id=" in first.headers.get("set-cookie", "")
    assert first.json()["record_id"] is None

    second = await client.post("/api/v1/proofread/text", json={"text": TEXT})
    assert second.status_code == 200, second.text

    records = await _records()
    assert len(records) == 2
    assert {r.owner_kind for r in records} == {"guest"}
    assert all(r.user_id is None for r in records)
    assert records[0].guest_id
    assert records[0].guest_id == records[1].guest_id
    assert verify_guest_cookie(client.cookies.get("tm_guest_id")) == records[0].guest_id


async def test_tampered_cookie_is_replaced(client, mock_proofread):
    resp = await client.post(
        "/api/v1/proofread/text", json={"text": TEXT},
        headers={"Cookie": "tm_guest_id=attacker.0123456789abcdef"},
    )
    assert resp.status_code == 200, resp.text

    records = await _records()
    assert len(records) == 1
    assert records[0].guest_id != "attacker"
    set_cookie = resp.headers["set-cookie"]
    new_value = set_cookie.split("tm_guest_id=", 1)[1].split(";", 1)[0]
    assert verify_guest_cookie(new_value) == records[0].guest_id


async def test_logged_in_user_record_is_not_attributed_to_guest(client, mock_proofread):
    user_id, headers = await _authenticated_user()
    resp = await client.post("/api/v1/proofread/text", json={"text": TEXT}, headers=headers)
    assert resp.status_code == 200, resp.text

    records = await _records()
    assert len(records) == 1
    assert records[0].owner_kind == "user"
    assert records[0].user_id == user_id
    assert records[0].guest_id is None


async def test_guest_document_task_stores_guest_id(client, monkeypatch):
    from app.api.v1 import document as document_api

    async def fake_load(file_id, db):
        return {
            "file_id": file_id, "filename": "guest.txt", "file_path": "/tmp/guest.txt",
            "file_ext": ".txt", "text": "游客文档正文", "owner_kind": "guest", "user_id": None,
        }

    async def fake_check(*args, **kwargs):
        return None

    monkeypatch.setattr(document_api, "_load_document_info", fake_load)
    monkeypatch.setattr(document_api, "_check_document_ownership", fake_check)
    monkeypatch.setattr(document_api.async_proofread_document, "apply_async", lambda **kwargs: None)

    # 先发一次游客请求拿到签名 Cookie，再提交异步文档任务
    await client.get("/api/v1/site/info")
    resp = await client.post(
        "/api/v1/document/proofread/async", json={"file_id": "guest-file-1"}
    )
    assert resp.status_code == 200, resp.text

    async with async_session_factory() as session:
        task = (await session.execute(select(ProofreadTask))).scalar_one()
    assert task.owner_kind == "guest"
    assert task.owner_guest_id
    assert task.access_token_hash


async def test_dashboard_stats_split_guest_and_user_records(client):
    headers = await _admin_headers(client)
    user_id, _ = await _authenticated_user()
    async with async_session_factory() as session:
        session.add_all([
            ProofreadRecord(
                user_id=user_id, owner_kind="user", original_text="用户记录", quota_weight=2,
            ),
            ProofreadRecord(
                owner_kind="guest", guest_id="guest-1", original_text="游客记录一", quota_weight=1,
            ),
            ProofreadRecord(
                owner_kind="guest", guest_id="guest-2", original_text="游客记录二", quota_weight=3,
            ),
        ])
        await session.commit()

    resp = await client.get("/api/v1/admin/dashboard/stats", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_proofread_count"] == 6
    assert data["today_proofread_count"] == 6
    assert data["total_guest_proofread_count"] == 4
    assert data["today_guest_proofread_count"] == 4
    assert data["total_user_proofread_count"] == 2
    assert data["today_user_proofread_count"] == 2
    assert data["active_guests_today"] == 2
    assert data["active_users_today"] == 1
