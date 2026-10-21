"""游客配额：按访客 ID 计数，另设 IP 上限防止清 Cookie 绕过。"""
import pytest
from starlette.requests import Request

from app.core.config import settings
from app.core.exceptions import QuotaExceededError
from app.core.rate_limit import check_guest_rate_limit


def _request(ip: str = "1.2.3.4") -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/", "headers": [],
        "client": (ip, 1234), "server": ("testserver", 80), "scheme": "http",
    })


async def test_guest_quota_is_counted_per_guest_id(client):
    await check_guest_rate_limit(_request(), daily_limit=1, guest_id="guest-a")

    with pytest.raises(QuotaExceededError) as exc:
        await check_guest_rate_limit(_request(), daily_limit=1, guest_id="guest-a")
    assert exc.value.code == "GUEST_LIMIT"

    # 同一 IP 的另一位访客不受影响
    await check_guest_rate_limit(_request(), daily_limit=1, guest_id="guest-b")


async def test_guest_quota_has_ip_ceiling_against_cookie_rotation(client, monkeypatch):
    monkeypatch.setattr(settings, "GUEST_IP_DAILY_LIMIT", 2)

    await check_guest_rate_limit(_request(), daily_limit=10, guest_id="guest-a")
    await check_guest_rate_limit(_request(), daily_limit=10, guest_id="guest-b")

    with pytest.raises(QuotaExceededError) as exc:
        await check_guest_rate_limit(_request(), daily_limit=10, guest_id="guest-c")
    assert exc.value.code == "GUEST_IP_LIMIT"

    # 另一 IP 不受影响
    await check_guest_rate_limit(_request("5.6.7.8"), daily_limit=10, guest_id="guest-d")
