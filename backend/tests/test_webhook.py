"""Webhook 回调：签名/SSRF 校验、设置与轮换、投递任务。

投递任务在 Celery eager 模式下同步执行——httpx.post 以 mock 拦截，验证签名头与重试语义。
"""
import json
import uuid as _uuid
from unittest.mock import patch

import httpx
import pytest

from app.core.database import async_session_factory
from app.core.secret_crypto import decrypt_secret, encrypt_secret
from app.core.security import hash_api_key, hash_password
from app.models.api_key import ApiKey
from app.models.role import Role
from app.models.user import User
from app.services.webhook import (
    build_event,
    sign_payload,
    validate_webhook_url,
    verify_signature,
)
from app.tasks.webhook_task import webhook_deliver

PASSWORD = "Passw0rd!123"
HOOK_URL = "https://example.com/hook"


async def _create_user_with_key():
    async with async_session_factory() as session:
        role = Role(name="测试角色", code=f"r_{_uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        user = User(
            employee_id=f"emp_{_uuid.uuid4().hex[:8]}",
            username="回调测试用户",
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        plaintext = f"tm_{_uuid.uuid4().hex}"
        key = ApiKey(
            user_id=user.id,
            name="回调测试密钥",
            key_prefix=plaintext[:13],
            key_suffix=plaintext[-4:],
            key_hash=hash_api_key(plaintext),
            is_active=True,
        )
        session.add(key)
        await session.commit()
        await session.refresh(user)
        await session.refresh(key)
        return user, plaintext, key


async def _configure_webhook(key_id: int, secret: str):
    async with async_session_factory() as session:
        fresh = await session.get(ApiKey, key_id)
        fresh.webhook_url = HOOK_URL
        fresh.webhook_secret = encrypt_secret(secret)
        await session.commit()


async def _login(client, employee_id: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login", json={"employee_id": employee_id, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _mock_response(status_code: int):
    """构造 httpx.post mock 返回值（带 request 以支持 HTTPStatusError）"""
    return httpx.Response(status_code, request=httpx.Request("POST", HOOK_URL))


# ----------------------------------------------------------------------
# 单元：签名与地址校验
# ----------------------------------------------------------------------

def test_signature_roundtrip():
    body = b'{"event": "webhook.test"}'
    sig = sign_payload("whsec_x", body)
    assert sig.startswith("sha256=")
    assert verify_signature("whsec_x", body, sig)
    assert not verify_signature("whsec_other", body, sig)


def test_validate_webhook_url_rejects_private_and_bad_scheme():
    with pytest.raises(ValueError, match="http"):
        validate_webhook_url("ftp://example.com/hook")
    with pytest.raises(ValueError, match="主机名"):
        validate_webhook_url("http:///hook")
    # mock 内网解析（避免依赖真实 DNS）
    with patch("socket.getaddrinfo", return_value=[(None, None, None, "", ("127.0.0.1", 80))]):
        with pytest.raises(ValueError, match="内网"):
            validate_webhook_url("http://internal.example/hook")
        # allow_private 显式放行（DEBUG 联调）
        validate_webhook_url("http://internal.example/hook", allow_private=True)


def test_build_event_shape():
    event = build_event("document.completed", "job-1", {"total_issues": 3})
    assert event["event"] == "document.completed"
    assert event["api_version"] == "v1"
    assert event["job_id"] == "job-1"
    assert event["data"]["total_issues"] == 3
    assert "timestamp" in event


# ----------------------------------------------------------------------
# API：设置 / 轮换 / 清除 / 测试推送
# ----------------------------------------------------------------------

async def test_set_webhook_returns_secret_once_and_stores_encrypted(client):
    user, _, key = await _create_user_with_key()
    token = await _login(client, user.employee_id)

    resp = await client.put(
        f"/api/v1/api-keys/{key.id}/webhook",
        json={"url": HOOK_URL},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    secret = resp.json()["secret"]
    assert secret.startswith("whsec_")

    async with async_session_factory() as session:
        fresh = await session.get(ApiKey, key.id)
        assert fresh.webhook_url == HOOK_URL
        assert fresh.webhook_secret.startswith("enc:")  # 密文存储
        assert decrypt_secret(fresh.webhook_secret) == secret

    # 列表展示回调地址但不展示密钥
    resp = await client.get("/api/v1/api-keys", headers={"Authorization": f"Bearer {token}"})
    item = [i for i in resp.json()["items"] if i["id"] == key.id][0]
    assert item["webhook_url"] == HOOK_URL


async def test_set_webhook_rejects_private_url(client):
    user, _, key = await _create_user_with_key()
    token = await _login(client, user.employee_id)

    # 测试环境 DEBUG=true 默认放行内网，显式关闭验证生产语义
    with patch("app.core.config.settings.DEBUG", False), \
         patch("socket.getaddrinfo", return_value=[(None, None, None, "", ("10.0.0.5", 80))]):
        resp = await client.put(
            f"/api/v1/api-keys/{key.id}/webhook",
            json={"url": "https://intranet.example/hook"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400
    assert "内网" in resp.json()["detail"]


async def test_webhook_ownership_and_clear(client):
    user, _, key = await _create_user_with_key()
    other, _, other_key = await _create_user_with_key()
    token = await _login(client, user.employee_id)

    # 他人密钥 404
    resp = await client.put(
        f"/api/v1/api-keys/{other_key.id}/webhook",
        json={"url": HOOK_URL},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404

    # 设置后清除
    await client.put(
        f"/api/v1/api-keys/{key.id}/webhook",
        json={"url": HOOK_URL},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.delete(
        f"/api/v1/api-keys/{key.id}/webhook",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    async with async_session_factory() as session:
        fresh = await session.get(ApiKey, key.id)
        assert fresh.webhook_url is None and fresh.webhook_secret is None


async def test_webhook_test_push_with_signature(client):
    user, _, key = await _create_user_with_key()
    token = await _login(client, user.employee_id)

    set_resp = await client.put(
        f"/api/v1/api-keys/{key.id}/webhook",
        json={"url": HOOK_URL},
        headers={"Authorization": f"Bearer {token}"},
    )
    secret = set_resp.json()["secret"]

    with patch("httpx.post", return_value=_mock_response(200)) as mock_post:
        resp = await client.post(
            f"/api/v1/api-keys/{key.id}/webhook/test",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert "送达" in resp.json()["message"]

    # 请求侧验签：对等构造 HMAC 应与服务端签名一致
    sent_body = mock_post.call_args.kwargs["content"]
    sent_headers = mock_post.call_args.kwargs["headers"]
    assert sent_headers["X-TextMirror-Event"] == "webhook.test"
    assert verify_signature(secret, sent_body, sent_headers["X-TextMirror-Signature"])
    assert json.loads(sent_body)["event"] == "webhook.test"


async def test_webhook_test_without_config_400(client):
    user, _, key = await _create_user_with_key()
    token = await _login(client, user.employee_id)
    resp = await client.post(
        f"/api/v1/api-keys/{key.id}/webhook/test",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


# ----------------------------------------------------------------------
# 投递任务：签名投递 / 未配置跳过 / 非 2xx 重试
# ----------------------------------------------------------------------

async def test_webhook_deliver_posts_signed_event(client):
    user, _, key = await _create_user_with_key()
    await _configure_webhook(key.id, "whsec_abc")

    event = build_event("document.completed", "job-1", {"total_issues": 2})
    with patch("app.tasks.webhook_task.httpx.post", return_value=_mock_response(204)) as mock_post:
        result = webhook_deliver.apply(args=(key.id, event)).get()

    assert result["delivered"] is True
    sent_body = mock_post.call_args.kwargs["content"]
    sent_headers = mock_post.call_args.kwargs["headers"]
    assert sent_headers["X-TextMirror-Event"] == "document.completed"
    assert verify_signature("whsec_abc", sent_body, sent_headers["X-TextMirror-Signature"])
    assert json.loads(sent_body)["job_id"] == "job-1"


async def test_webhook_deliver_skips_when_not_configured(client):
    user, _, key = await _create_user_with_key()
    event = build_event("document.completed", "job-1")
    result = webhook_deliver.apply(args=(key.id, event)).get()
    assert result == {"skipped": True, "reason": "webhook not configured"}


async def test_webhook_deliver_retries_on_non_2xx(client):
    from celery.exceptions import Retry

    user, _, key = await _create_user_with_key()
    await _configure_webhook(key.id, "whsec_abc")

    event = build_event("document.failed", "job-1", {"error_code": "PROOFREAD_FAILED"})
    with patch("app.tasks.webhook_task.httpx.post", return_value=_mock_response(500)):
        # eager + eager_propagates：autoretry 把 HTTPStatusError 转成 Celery Retry（或原样抛出）
        with pytest.raises((httpx.HTTPStatusError, Retry)):
            webhook_deliver.apply(args=(key.id, event)).get()
