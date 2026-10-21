"""游客数据生命周期：摘要、导出、认领与删除。"""
import json
import os
import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.guest_identity import verify_guest_cookie
from app.core.security import create_access_token, hash_password
from app.models.issue_feedback import IssueFeedback
from app.models.proofread import ProofreadRecord
from app.models.proofread_task import ProofreadTask
from app.models.role import Role
from app.models.uploaded_document import UploadedDocument
from app.models.user import User

PASSWORD = "GuestPass123!"


async def _guest_id(client) -> str:
    await client.get("/api/v1/site/info")
    guest_id = verify_guest_cookie(client.cookies.get("tm_guest_id"))
    assert guest_id
    return guest_id


async def _user_headers():
    async with async_session_factory() as session:
        role = Role(name="游客数据测试角色", code=f"guest_data_{uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"guest_data_{uuid.uuid4().hex[:8]}",
            username="游客数据测试用户",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id, {"Authorization": f"Bearer {create_access_token(user.id)}"}


async def _seed(guest_id: str, file_id: str = "guest-doc-1"):
    file_dir = os.path.join(settings.UPLOAD_DIR, file_id)
    os.makedirs(file_dir, exist_ok=True)
    with open(os.path.join(file_dir, "a.txt"), "w", encoding="utf-8") as f:
        f.write("test")

    async with async_session_factory() as session:
        record = ProofreadRecord(
            owner_kind="guest", guest_id=guest_id, type="text", domain="general",
            original_text="游客原文", total_issues=1,
            result={"issues": [{"original": "游客", "type": "typo", "suggestion": "游客"}]},
        )
        session.add(record)
        await session.flush()
        session.add(IssueFeedback(
            record_id=record.id, user_id=None, guest_id=guest_id,
            original="游客", suggestion="游客", issue_type="typo", action="accept",
        ))
        session.add(UploadedDocument(
            file_id=file_id, filename="a.txt", file_ext=".txt", file_size=4,
            file_path=os.path.join(file_dir, "a.txt"), text_length=4,
            owner_kind="guest", guest_id=guest_id, status="uploaded",
            extracted_text="test",
        ))
        session.add(ProofreadTask(
            task_id=f"task-{uuid.uuid4().hex}",
            owner_kind="guest", owner_guest_id=guest_id, status="SUCCESS",
        ))
        await session.commit()


async def test_guest_summary_export_and_delete(client):
    guest_id = await _guest_id(client)
    await _seed(guest_id)

    summary = await client.get("/api/v1/guest-data/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json() == {
        "records": 1, "feedbacks": 1, "documents": 1, "total": 3, "can_claim": False,
    }

    exported = await client.get("/api/v1/guest-data/export")
    assert exported.status_code == 200, exported.text
    assert "attachment" in exported.headers["content-disposition"]
    data = json.loads(exported.text)
    assert data["records"][0]["original_text"] == "游客原文"
    assert data["feedbacks"][0]["original"] == "游客"
    assert data["documents"][0]["file_id"] == "guest-doc-1"

    wrong = await client.request("DELETE", "/api/v1/guest-data", json={"confirm": "NO"})
    assert wrong.status_code == 400

    deleted = await client.request("DELETE", "/api/v1/guest-data", json={"confirm": "DELETE"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"records": 1, "feedbacks": 1, "documents": 1, "tasks": 1}
    assert "Max-Age=0" in deleted.headers.get("set-cookie", "")
    assert not os.path.exists(os.path.join(settings.UPLOAD_DIR, "guest-doc-1"))

    async with async_session_factory() as session:
        assert await session.scalar(select(ProofreadRecord)) is None
        assert await session.scalar(select(IssueFeedback)) is None
        assert await session.scalar(select(UploadedDocument)) is None
        assert await session.scalar(select(ProofreadTask)) is None


async def test_claim_moves_guest_data_to_user(client):
    guest_id = await _guest_id(client)
    await _seed(guest_id, file_id="guest-doc-2")
    user_id, headers = await _user_headers()

    resp = await client.post("/api/v1/guest-data/claim", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"records": 1, "feedbacks": 1, "documents": 1, "tasks": 1}

    async with async_session_factory() as session:
        record = await session.scalar(select(ProofreadRecord))
        feedback = await session.scalar(select(IssueFeedback))
        doc = await session.scalar(select(UploadedDocument))
        task = await session.scalar(select(ProofreadTask))
    assert (record.owner_kind, record.user_id, record.guest_id) == ("user", user_id, None)
    assert (feedback.user_id, feedback.guest_id) == (user_id, None)
    assert (doc.owner_kind, doc.user_id, doc.guest_id) == ("user", user_id, None)
    assert (task.owner_kind, task.owner_user_id, task.owner_guest_id) == ("user", user_id, None)

    summary = await client.get("/api/v1/guest-data/summary", headers=headers)
    assert summary.json()["total"] == 0


async def test_claim_requires_login(client):
    await _guest_id(client)
    resp = await client.post("/api/v1/guest-data/claim")
    assert resp.status_code == 401


async def test_guest_upload_records_guest_id(client):
    guest_id = await _guest_id(client)
    resp = await client.post(
        "/api/v1/document/upload",
        files={"file": ("guest.txt", b"hello guest", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    file_id = resp.json()["file_id"]

    async with async_session_factory() as session:
        doc = await session.scalar(
            select(UploadedDocument).where(UploadedDocument.file_id == file_id)
        )
    assert doc is not None
    assert doc.guest_id == guest_id
    assert doc.owner_kind == "guest"
