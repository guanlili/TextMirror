"""开放 API 润色端点：同步三版本、SSE 流式、计费归属与错误契约。"""
import json
import uuid as _uuid
from unittest.mock import patch

from app.core.database import async_session_factory
from app.core.security import hash_api_key, hash_password
from app.models.api_key import ApiKey
from app.models.proofread import ProofreadRecord
from app.models.role import Role
from app.models.user import User


def _polish_result() -> dict:
    return {
        "versions": [
            {"label": "轻量润色", "level": "light", "content": "润色后的文本一。"},
            {"label": "标准润色", "level": "standard", "content": "润色后的文本二。"},
            {"label": "深度润色", "level": "deep", "content": "润色后的文本三。"},
        ],
        "style": "formal",
        "style_name": "正式规范",
        "usage": {"prompt_tokens": 30, "completion_tokens": 60},
    }


def _stream_events():
    return [
        {"event": "meta", "style": "formal", "style_name": "正式规范"},
        {"event": "start", "level": "light", "label": "轻量润色"},
        {"event": "delta", "level": "light", "content": "润"},
        {"event": "done", "level": "light", "content": "润色后的文本一。"},
        {"event": "end", "usage": {}},
    ]


async def _create_user_with_key(daily_quota=None):
    async with async_session_factory() as session:
        role = Role(name="测试角色", code=f"r_{_uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"emp_{_uuid.uuid4().hex[:8]}",
            username="润色测试用户",
            password_hash=hash_password("Passw0rd!123"),
            role_id=role.id,
            daily_quota=daily_quota,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        plaintext = f"tm_{_uuid.uuid4().hex}"
        key = ApiKey(
            user_id=user.id,
            name="润色测试密钥",
            key_prefix=plaintext[:13],
            key_suffix=plaintext[-4:],
            key_hash=hash_api_key(plaintext),
            daily_quota=None,
            is_active=True,
        )
        session.add(key)
        await session.commit()
        await session.refresh(user)
        await session.refresh(key)
        return user, plaintext, key


async def _records_for(user_id: int):
    from sqlalchemy import select

    async with async_session_factory() as session:
        return (await session.execute(
            select(ProofreadRecord).where(ProofreadRecord.user_id == user_id)
        )).scalars().all()


async def test_open_polish_sync_success(client):
    user, plaintext, key = await _create_user_with_key(daily_quota=100)

    with patch("app.api.v1.open_polish.polish_text", return_value=_polish_result()):
        resp = await client.post(
            "/api/v1/open/polish",
            json={"text": "这段文字需要更加正式的表达方式来呈现。"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["versions"]) == 3
    assert data["style_name"] == "正式规范"

    records = await _records_for(user.id)
    assert len(records) == 1
    assert records[0].type == "polish"
    assert records[0].api_key_id == key.id


async def test_open_polish_invalid_style_422(client):
    _, plaintext, _ = await _create_user_with_key()
    resp = await client.post(
        "/api/v1/open/polish",
        json={"text": "这段文字需要更加正式的表达方式来呈现。", "style": "nonexistent"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "VALIDATION_ERROR"


async def test_open_polish_service_error_refunds_and_503(client):
    _, plaintext, key = await _create_user_with_key()
    from app.core import redis as redis_module
    from app.core.rate_limit import _api_key_daily_redis_key

    with patch("app.api.v1.open_polish.polish_text", side_effect=RuntimeError("no active model")):
        resp = await client.post(
            "/api/v1/open/polish",
            json={"text": "这段文字需要更加正式的表达方式来呈现。"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "MODEL_UNAVAILABLE"

    # 扣了 1 次后服务失败应退还：日计数回到 0
    count = await redis_module.redis_client.get(_api_key_daily_redis_key(key))
    assert int(count) == 0

    records = await _records_for(key.user_id)
    assert records == []


async def test_open_polish_stream_events_and_record(client):
    user, plaintext, key = await _create_user_with_key(daily_quota=100)

    async def _fake_stream(text, style):
        for evt in _stream_events():
            yield evt

    with patch("app.api.v1.open_polish.polish_text_stream", _fake_stream):
        resp = await client.post(
            "/api/v1/open/polish/stream",
            json={"text": "这段文字需要更加正式的表达方式来呈现。"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = [
        json.loads(line.removeprefix("data: "))
        for line in resp.text.strip().split("\n\n")
        if line.startswith("data: ")
    ]
    kinds = [e["event"] for e in events]
    assert kinds == ["meta", "start", "delta", "done", "end"]

    records = await _records_for(user.id)
    assert len(records) == 1
    assert records[0].type == "polish"
    assert records[0].api_key_id == key.id


async def test_open_polish_stream_fatal_refunds_without_record(client):
    _, plaintext, key = await _create_user_with_key()
    from app.core import redis as redis_module
    from app.core.rate_limit import _api_key_daily_redis_key

    async def _broken_stream(text, style):
        yield {"event": "meta", "style": "formal", "style_name": "正式规范"}
        raise RuntimeError("boom")

    with patch("app.api.v1.open_polish.polish_text_stream", _broken_stream):
        resp = await client.post(
            "/api/v1/open/polish/stream",
            json={"text": "这段文字需要更加正式的表达方式来呈现。"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
    assert resp.status_code == 200
    assert '"event": "fatal"' in resp.text

    count = await redis_module.redis_client.get(_api_key_daily_redis_key(key))
    assert int(count) == 0
    records = await _records_for(key.user_id)
    assert records == []


async def test_open_polish_requires_auth(client):
    resp = await client.post(
        "/api/v1/open/polish",
        json={"text": "这段文字需要更加正式的表达方式来呈现。"},
    )
    assert resp.status_code == 401
