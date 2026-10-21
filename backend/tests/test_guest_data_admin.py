"""后台游客语料：统计口径、筛选与只读详情。"""
import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import async_session_factory
from app.core.security import hash_password
from app.models.proofread import ProofreadRecord
from app.models.role import Role
from app.models.user import User

PASSWORD = "AdminPass123!"


async def _admin_headers(client):
    employee_id = f"admin_{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as session:
        role = Role(name="超管", code="super_admin")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=employee_id,
            username="游客语料管理员",
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


async def _seed_records():
    async with async_session_factory() as session:
        text_record = ProofreadRecord(
            owner_kind="guest", guest_id="guest-aaaa1111", type="text", domain="general",
            original_text="游客文本一，这里有一个错别字。", total_issues=2, quota_weight=1,
            result={"issues": [{"original": "错别字", "type": "typo", "suggestion": "正确字"}]},
        )
        document_record = ProofreadRecord(
            owner_kind="guest", guest_id="guest-bbbb2222", type="document", domain="official",
            original_text="游客文档正文", total_issues=1, quota_weight=2,
            result={"issues": []},
        )
        user_record = ProofreadRecord(
            owner_kind="user", user_id=None, type="text", domain="general",
            original_text="登录用户记录", total_issues=9, quota_weight=1,
        )
        old_record = ProofreadRecord(
            owner_kind="guest", guest_id="guest-old", type="text", domain="general",
            original_text="很久以前的游客记录", total_issues=0,
            created_at=datetime.now(timezone.utc) - timedelta(days=40),
        )
        session.add_all([text_record, document_record, user_record, old_record])
        await session.commit()
        await session.refresh(text_record)
        await session.refresh(user_record)
        return text_record.id, user_record.id


async def test_guest_data_stats_excludes_users_and_old_records(client):
    headers = await _admin_headers(client)
    await _seed_records()

    resp = await client.get(
        "/api/v1/admin/guest-data/stats", params={"days": 30}, headers=headers
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["records"] == 2
    assert data["proofreads"] == 3
    assert data["guests"] == 2
    assert data["issues"] == 3
    assert data["texts"] == 1
    assert data["documents"] == 1
    assert data["polishes"] == 0
    assert {item["domain"] for item in data["domains"]} == {"general", "official"}


async def test_guest_data_list_filters_and_detail(client):
    headers = await _admin_headers(client)
    text_id, user_id = await _seed_records()

    resp = await client.get(
        "/api/v1/admin/guest-data/records",
        params={"days": 30, "type": "document"}, headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["type"] == "document"
    assert data["items"][0]["guest_label"] == "guest-bb"

    resp = await client.get(
        "/api/v1/admin/guest-data/records",
        params={"days": 30, "keyword": "错别字"}, headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1

    detail = await client.get(f"/api/v1/admin/guest-data/records/{text_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["original_text"].startswith("游客文本一")
    assert body["issues"][0]["original"] == "错别字"

    forbidden = await client.get(f"/api/v1/admin/guest-data/records/{user_id}", headers=headers)
    assert forbidden.status_code == 404
