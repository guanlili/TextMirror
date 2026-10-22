"""客户端 IP 不能由 X-Forwarded-For 最左项伪造。

直连对端必须落在 TRUSTED_PROXIES（默认只有回环）里，才从右侧取第一个
不属于可信代理的地址。公网地址用 8.8.8.8 / 1.2.3.4：Python 把
203.0.113.0/24、198.51.100.0/24 标成 private，不能拿来代表公网。
"""
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from starlette.requests import Request

from app.core.config import Settings, settings
from app.core.database import async_session_factory
from app.core.exceptions import QuotaExceededError
from app.core.rate_limit import check_guest_rate_limit, check_upload_rate_limit
from app.core.security import hash_password
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.utils.ip import get_client_ip

PASSWORD = "Passw0rd!"


def _request(peer: str, xff: str | None = None, real_ip: str | None = None, extra_headers=None) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if real_ip is not None:
        headers.append((b"x-real-ip", real_ip.encode()))
    if extra_headers:
        headers.extend(extra_headers)
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": headers,
        "client": (peer, 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    })


def test_client_ip_helper_is_shared():
    from app.api.v1 import auth, feishu
    from app.core import rate_limit
    from app.services import audit_log

    assert auth.get_client_ip is get_client_ip
    assert feishu.get_client_ip is get_client_ip
    assert rate_limit.get_client_ip is get_client_ip
    assert audit_log.get_client_ip is get_client_ip


def test_untrusted_peer_ignores_forged_forwarding_headers():
    forged = "127.0.0.1, 10.1.1.1, 192.168.1.8, 172.16.0.4"
    for peer in ("8.8.8.8", "10.9.9.9", "172.18.0.5", "192.168.1.20", "testclient"):
        assert get_client_ip(_request(peer, forged, real_ip="127.0.0.1")) == peer


def test_trusted_proxy_uses_rightmost_untrusted_hop():
    peer = "127.0.0.1"
    assert get_client_ip(_request(peer, "8.8.8.8")) == "8.8.8.8"
    assert get_client_ip(_request(peer, "127.0.0.1, 8.8.8.8")) == "8.8.8.8"
    assert get_client_ip(_request(peer, "127.0.0.1, 10.2.1.5, 192.168.1.9, 8.8.8.8")) == "8.8.8.8"
    assert get_client_ip(_request(peer, "10.2.1.5")) == "10.2.1.5"
    assert get_client_ip(_request(peer, "10.2.1.5, 127.0.0.1")) == "10.2.1.5"
    assert get_client_ip(_request(peer, "8.8.8.8:443")) == "8.8.8.8"
    assert get_client_ip(_request(peer, "[2001:db8::5]:443")) == "2001:db8::5"
    assert get_client_ip(_request("::1", "8.8.8.8")) == "8.8.8.8"
    assert get_client_ip(_request("127.0.0.2", "1.2.3.4")) == "1.2.3.4"
    assert get_client_ip(_request("::ffff:127.0.0.1", "8.8.8.8")) == "8.8.8.8"


def test_unparseable_closest_hop_does_not_fall_through_to_spoof():
    assert get_client_ip(_request("127.0.0.1", "10.1.1.1, not-an-ip")) == "127.0.0.1"


def test_real_ip_is_fallback_and_does_not_override_forwarded_for():
    assert get_client_ip(_request("127.0.0.1", real_ip="8.8.8.8")) == "8.8.8.8"
    assert get_client_ip(_request("127.0.0.1", xff="8.8.8.8", real_ip="127.0.0.1")) == "8.8.8.8"
    assert get_client_ip(_request("8.8.8.8", real_ip="127.0.0.1")) == "8.8.8.8"


def test_multiple_forwarded_for_headers_keep_rightmost_client():
    request = _request("127.0.0.1", extra_headers=[
        (b"x-forwarded-for", b"127.0.0.1, 10.1.1.1"),
        (b"x-forwarded-for", b"8.8.8.8"),
    ])
    assert get_client_ip(request) == "8.8.8.8"


def test_configured_proxy_hop_is_skipped(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.0/8,::1,10.0.0.8/32")
    assert get_client_ip(_request("127.0.0.1", "8.8.8.8, 10.0.0.8")) == "8.8.8.8"
    assert get_client_ip(_request("127.0.0.1", "127.0.0.1, 8.8.8.8, 10.0.0.8")) == "8.8.8.8"
    assert get_client_ip(_request("10.0.0.8", "1.2.3.4")) == "1.2.3.4"
    assert get_client_ip(_request("10.0.0.9", "127.0.0.1")) == "10.0.0.9"


def test_trusted_proxies_config_parses_lists_and_rejects_garbage():
    assert Settings.parse_trusted_proxies(["127.0.0.0/8", "::1"]) == "127.0.0.0/8,::1"
    with pytest.raises(ValueError):
        Settings.validate_trusted_proxies("127.0.0.1,not-a-cidr")


async def _ensure_user(employee_id: str, role_code: str = "user") -> None:
    async with async_session_factory() as session:
        from sqlalchemy import select

        existing = (await session.execute(
            select(User).where(User.employee_id == employee_id)
        )).scalar_one_or_none()
        if existing:
            return
        role = Role(name=f"角色{employee_id}", code=f"{role_code}_{uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.flush()
        session.add(User(
            employee_id=employee_id,
            username=employee_id,
            password_hash=hash_password(PASSWORD),
            role_id=role.id,
            is_active=True,
        ))
        await session.commit()


@asynccontextmanager
async def _http(peer: str):
    transport = httpx.ASGITransport(app=app, client=(peer, 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def test_quick_login_rejects_spoofed_internal_xff(client, monkeypatch):
    """公网客户端写在最左边的回环/内网地址不能通过一键登录。"""
    await _ensure_user("demo")
    monkeypatch.setattr(settings, "DEBUG", False)
    real = "8.8.8.8"
    forged = (
        f"127.0.0.1, {real}",
        f"10.1.1.1, {real}",
        f"192.168.1.8, {real}",
        f"172.16.4.4, {real}",
        f"127.0.0.1, 10.2.1.5, 192.168.1.9, {real}",
        real,
    )
    async with _http("127.0.0.1") as http:
        for header in forged:
            resp = await http.post(
                "/api/v1/auth/quick-login?account=demo",
                headers={"X-Forwarded-For": header, "X-Real-IP": "127.0.0.1"},
            )
            assert resp.status_code == 403, header
            assert resp.json()["detail"]["code"] == "QUICK_LOGIN_INTERNAL_ONLY"


async def test_quick_login_allows_legitimate_internal_client_behind_proxy(client, monkeypatch):
    await _ensure_user("demo")
    monkeypatch.setattr(settings, "DEBUG", False)
    async with _http("127.0.0.1") as http:
        for header in ("10.2.1.5", "10.2.1.5, 127.0.0.1", "192.168.1.20"):
            resp = await http.post(
                "/api/v1/auth/quick-login?account=demo",
                headers={"X-Forwarded-For": header, "X-Real-IP": "10.2.1.5"},
            )
            assert resp.status_code == 200, (header, resp.text)


async def test_quick_login_ignores_xff_when_peer_is_not_a_trusted_proxy(client, monkeypatch):
    await _ensure_user("demo")
    monkeypatch.setattr(settings, "DEBUG", False)
    async with _http("10.9.9.9") as http:
        resp = await http.post(
            "/api/v1/auth/quick-login?account=demo",
            headers={"X-Forwarded-For": "8.8.8.8", "X-Real-IP": "8.8.8.8"},
        )
    assert resp.status_code == 200, resp.text

    async with _http("8.8.8.8") as http:
        resp = await http.post(
            "/api/v1/auth/quick-login?account=demo",
            headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "10.1.1.1"},
        )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "QUICK_LOGIN_INTERNAL_ONLY"


async def test_login_lockout_is_not_bypassed_by_spoofed_xff(client, monkeypatch):
    from app.core import redis as redis_module

    monkeypatch.setattr(settings, "LOGIN_MAX_FAILURES", 3)
    employee_id = f"lock_{uuid.uuid4().hex[:8]}"
    body = {"employee_id": employee_id, "password": "wrong-password"}
    real = "8.8.8.8"

    async with _http("127.0.0.1") as http:
        for _ in range(3):
            resp = await http.post(
                "/api/v1/auth/login",
                json=body,
                headers={"X-Forwarded-For": f"127.0.0.1, 10.1.1.1, {real}"},
            )
            assert resp.status_code == 401, resp.text
        spoofed = await http.post(
            "/api/v1/auth/login",
            json=body,
            headers={"X-Forwarded-For": f"127.0.0.1, 192.168.1.1, {real}"},
        )
        other = await http.post(
            "/api/v1/auth/login",
            json=body,
            headers={"X-Forwarded-For": "1.2.3.4"},
        )

    assert spoofed.status_code == 429
    assert spoofed.json()["detail"]["code"] == "LOGIN_LOCKED"
    assert other.status_code == 401
    assert await redis_module.redis_client.get(f"textmirror:login_lock:{employee_id}:{real}")
    assert await redis_module.redis_client.get(f"textmirror:login_lock:{employee_id}:127.0.0.1") is None

    async with _http(real) as http:
        direct = await http.post(
            "/api/v1/auth/login",
            json=body,
            headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "10.0.0.8"},
        )
    assert direct.status_code == 429
    assert direct.json()["detail"]["code"] == "LOGIN_LOCKED"


async def test_guest_ip_limit_is_not_bypassed_by_spoofed_xff(client, monkeypatch):
    monkeypatch.setattr(settings, "GUEST_IP_DAILY_LIMIT", 2)
    real = "8.8.8.8"
    bodies = [
        _request("127.0.0.1", f"127.0.0.1, {real}"),
        _request("127.0.0.1", f"10.1.1.1, 192.168.1.8, {real}"),
        _request("127.0.0.1", f"172.16.0.9, {real}"),
    ]
    for index, request in enumerate(bodies):
        if index < 2:
            await check_guest_rate_limit(request, daily_limit=10, guest_id=f"guest-{index}")
        else:
            with pytest.raises(QuotaExceededError) as exc:
                await check_guest_rate_limit(request, daily_limit=10, guest_id="guest-spoof")
            assert exc.value.code == "GUEST_IP_LIMIT"

    await check_guest_rate_limit(
        _request("127.0.0.1", "127.0.0.1, 1.2.3.4"), daily_limit=10, guest_id="guest-other",
    )
    await check_guest_rate_limit(
        _request("1.2.3.4", "127.0.0.1"), daily_limit=10, guest_id="guest-direct",
    )


async def test_guest_http_limit_uses_real_client_ip(client, monkeypatch):
    from app.api.v1 import polish as polish_module

    monkeypatch.setattr(settings, "GUEST_IP_DAILY_LIMIT", 2)

    async def _fake_polish(text, style="formal"):
        return {
            "versions": [{"label": "轻量润色", "level": "light", "content": "ok"}],
            "style": style,
            "style_name": "正式规范",
            "usage": {},
        }

    monkeypatch.setattr(polish_module, "polish_text", _fake_polish)
    payload = {"text": "这是一段用于测试的原文。", "style": "formal"}
    real = "8.8.8.8"

    async with _http("127.0.0.1") as http:
        codes = []
        for header in (
            f"127.0.0.1, {real}",
            f"10.2.1.5, 192.168.1.1, {real}",
            f"172.16.8.8, {real}",
        ):
            resp = await http.post(
                "/api/v1/polish/text",
                json=payload,
                headers={"X-Forwarded-For": header, "X-Real-IP": "127.0.0.1"},
            )
            codes.append((resp.status_code, resp.json().get("detail", {}).get("code")))
        other = await http.post(
            "/api/v1/polish/text",
            json=payload,
            headers={"X-Forwarded-For": "1.2.3.4"},
        )

    assert codes == [(200, None), (200, None), (429, "GUEST_IP_LIMIT")], codes
    assert other.status_code == 200


async def test_upload_limit_uses_rightmost_client_ip(client, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_MAX_PER_MINUTE", 1)
    await check_upload_rate_limit(_request("127.0.0.1", "198.51.100.9, 8.8.8.8"))
    with pytest.raises(QuotaExceededError) as exc:
        await check_upload_rate_limit(_request("127.0.0.1", "127.0.0.1, 8.8.8.8"))
    assert exc.value.code == "UPLOAD_RATE_LIMITED"
    await check_upload_rate_limit(_request("127.0.0.1", "1.2.3.4"))
