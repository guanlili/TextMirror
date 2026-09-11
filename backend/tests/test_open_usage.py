"""开放 API 用量统计：api_key_id 归属、/open/usage 聚合口径、自助列表 used_7d。

统计口径：只计成功调用（落库记录）；Web 端（JWT）调用不归属密钥，不进统计。
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.core.database import async_session_factory
from app.core.security import hash_api_key, hash_password
from app.models.api_key import ApiKey
from app.models.proofread import ProofreadRecord
from app.models.role import Role
from app.models.user import User


def _success_result(text: str = "测试文本") -> dict:
    return {
        "issues": [],
        "total_issues": 0,
        "chunks_count": 1,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "domain": "general",
        "check_types": [],
    }


async def _create_user_with_role(daily_quota=None) -> User:
    async with async_session_factory() as session:
        role = Role(name="测试角色", code=f"r_{_uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"emp_{_uuid.uuid4().hex[:8]}",
            username="测试用户",
            password_hash=hash_password("Passw0rd!123"),
            role_id=role.id,
            daily_quota=daily_quota,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_key(user_id: int) -> tuple[str, ApiKey]:
    plaintext = f"tm_{_uuid.uuid4().hex}"
    async with async_session_factory() as session:
        key = ApiKey(
            user_id=user_id,
            name="用量测试密钥",
            key_prefix=plaintext[:13],
            key_suffix=plaintext[-4:],
            key_hash=hash_api_key(plaintext),
            daily_quota=None,
            is_active=True,
        )
        session.add(key)
        await session.commit()
        await session.refresh(key)
        return plaintext, key


async def _insert_record(user_id: int, api_key_id=None, created_at=None) -> int:
    async with async_session_factory() as session:
        record = ProofreadRecord(
            user_id=user_id,
            api_key_id=api_key_id,
            type="text",
            original_text="测试",
            domain="general",
            result={},
            total_issues=0,
        )
        if created_at is not None:
            record.created_at = created_at
        session.add(record)
        await session.commit()
        return record.id


async def _login(client, employee_id: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login", json={"employee_id": employee_id, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_open_proofread_attributed_to_api_key(client):
    user = await _create_user_with_role(daily_quota=100)
    plaintext, key = await _create_key(user.id)

    with patch("app.api.v1.open.proofread_text", return_value=_success_result()):
        resp = await client.post(
            "/api/v1/open/proofread",
            json={"text": "测试文本"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 200, resp.text

    async with async_session_factory() as session:
        from sqlalchemy import select

        records = (await session.execute(
            select(ProofreadRecord).where(ProofreadRecord.user_id == user.id)
        )).scalars().all()
        assert len(records) == 1
        assert records[0].api_key_id == key.id


async def test_usage_scoped_to_calling_key(client):
    user = await _create_user_with_role()
    plaintext, key = await _create_key(user.id)
    _, other_key = await _create_key(user.id)
    await _insert_record(user.id, api_key_id=key.id)
    await _insert_record(user.id, api_key_id=other_key.id)
    await _insert_record(user.id, api_key_id=None)  # Web 调用不计入

    resp = await client.get(
        "/api/v1/open/usage?days=7",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scope"] == "api_key"
    assert data["total"] == 1
    assert len(data["daily"]) == 7
    assert sum(d["count"] for d in data["daily"]) == 1
    assert len(data["keys"]) == 1 and data["keys"][0]["key_id"] == key.id


async def test_usage_scoped_to_user_keys_with_jwt(client):
    user = await _create_user_with_role()
    _, key_a = await _create_key(user.id)
    _, key_b = await _create_key(user.id)
    other_user = await _create_user_with_role()
    _, other_user_key = await _create_key(other_user.id)

    await _insert_record(user.id, api_key_id=key_a.id)
    await _insert_record(user.id, api_key_id=key_b.id)
    await _insert_record(user.id, api_key_id=key_b.id)
    await _insert_record(other_user.id, api_key_id=other_user_key.id)  # 他人密钥不计入
    await _insert_record(user.id, api_key_id=None)  # Web 调用不计入

    token = await _login(client, user.employee_id, "Passw0rd!123")
    resp = await client.get(
        "/api/v1/open/usage", params={"days": 1}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scope"] == "user"
    assert data["total"] == 3
    key_counts = {k["key_id"]: k["count"] for k in data["keys"]}
    assert key_counts == {key_a.id: 1, key_b.id: 2}


async def test_usage_old_records_out_of_range(client):
    user = await _create_user_with_role()
    plaintext, key = await _create_key(user.id)
    eight_days_ago = datetime.now(timezone.utc) - timedelta(days=8)
    await _insert_record(user.id, api_key_id=key.id, created_at=eight_days_ago)

    resp = await client.get(
        "/api/v1/open/usage?days=7",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


async def test_usage_days_validation(client):
    user = await _create_user_with_role()
    plaintext, _ = await _create_key(user.id)

    for bad in (0, 91):
        resp = await client.get(
            f"/api/v1/open/usage?days={bad}",
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "VALIDATION_ERROR"


async def test_usage_requires_auth(client):
    resp = await client.get("/api/v1/open/usage")
    assert resp.status_code == 401


async def test_api_keys_list_includes_used_7d(client):
    user = await _create_user_with_role()
    plaintext, key = await _create_key(user.id)
    await _insert_record(user.id, api_key_id=key.id)

    token = await _login(client, user.employee_id, "Passw0rd!123")
    resp = await client.get("/api/v1/api-keys", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    mine = [i for i in items if i["id"] == key.id]
    assert mine and mine[0]["used_7d"] == 1
