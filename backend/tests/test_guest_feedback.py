"""游客审校建议反馈：落库归属、记录越权校验与后台建议聚合。"""
import uuid

from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.guest_identity import verify_guest_cookie
from app.core.security import create_access_token, hash_password
from app.models.issue_feedback import IssueFeedback
from app.models.proofread import ProofreadRecord
from app.models.role import Role
from app.models.user import User

PASSWORD = "GuestPass123!"
FEEDBACK = {
    "items": [
        {"original": "鹅暖石", "suggestion": "鹅卵石", "issue_type": "typo", "action": "accept"},
    ],
}


async def _guest_id(client) -> str:
    await client.get("/api/v1/site/info")
    guest_id = verify_guest_cookie(client.cookies.get("tm_guest_id"))
    assert guest_id
    return guest_id


async def _authenticated_user():
    async with async_session_factory() as session:
        role = Role(name="反馈测试角色", code=f"feedback_{uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"feedback_{uuid.uuid4().hex[:8]}",
            username="反馈测试用户",
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
            username="反馈统计管理员",
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


async def _feedbacks():
    async with async_session_factory() as session:
        return (await session.execute(select(IssueFeedback))).scalars().all()


async def test_guest_feedback_is_saved_without_record_id(client):
    guest_id = await _guest_id(client)

    resp = await client.post("/api/v1/proofread/feedback", json=FEEDBACK)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"saved": 1}
    rows = await _feedbacks()
    assert len(rows) == 1
    assert rows[0].user_id is None
    assert rows[0].guest_id == guest_id
    assert rows[0].record_id is None
    assert rows[0].action == "accept"


async def test_guest_feedback_accepts_own_record(client):
    guest_id = await _guest_id(client)
    async with async_session_factory() as session:
        record = ProofreadRecord(
            owner_kind="guest", guest_id=guest_id, original_text="原文",
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        record_id = record.id

    resp = await client.post(
        "/api/v1/proofread/feedback", json={**FEEDBACK, "record_id": record_id}
    )

    assert resp.status_code == 200, resp.text
    rows = await _feedbacks()
    assert len(rows) == 1
    assert rows[0].guest_id == guest_id
    assert rows[0].record_id == record_id


async def test_guest_feedback_rejects_another_guests_record(client):
    await _guest_id(client)
    async with async_session_factory() as session:
        record = ProofreadRecord(
            owner_kind="guest", guest_id="another-guest", original_text="别人的原文",
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        record_id = record.id

    resp = await client.post(
        "/api/v1/proofread/feedback", json={**FEEDBACK, "record_id": record_id}
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "FEEDBACK_NOT_ALLOWED"
    assert await _feedbacks() == []


async def test_logged_in_feedback_keeps_user_attribution(client):
    user_id, headers = await _authenticated_user()
    async with async_session_factory() as session:
        record = ProofreadRecord(
            user_id=user_id, owner_kind="user", original_text="用户原文",
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        record_id = record.id

    resp = await client.post(
        "/api/v1/proofread/feedback",
        json={**FEEDBACK, "record_id": record_id}, headers=headers,
    )

    assert resp.status_code == 200, resp.text
    rows = await _feedbacks()
    assert len(rows) == 1
    assert rows[0].user_id == user_id
    assert rows[0].guest_id is None


async def test_admin_suggestions_count_guests(client):
    headers = await _admin_headers(client)
    async with async_session_factory() as session:
        for _ in range(3):
            session.add(IssueFeedback(
                user_id=None, guest_id="guest-1", original="鹅暖石",
                suggestion="鹅卵石", issue_type="typo", action="ignore",
            ))
        await session.commit()

    resp = await client.get(
        "/api/v1/admin/global-dict/suggestions", params={"min_count": 3}, headers=headers,
    )

    assert resp.status_code == 200, resp.text
    items = {item["word"]: item for item in resp.json()["whitelist"]}
    assert items["鹅暖石"]["ignore_count"] == 3
    assert items["鹅暖石"]["user_count"] == 0
    assert items["鹅暖石"]["guest_count"] == 1
