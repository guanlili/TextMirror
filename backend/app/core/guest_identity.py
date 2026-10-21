"""匿名访客身份：签名 Cookie，用于游客校对记录的稳定归属。"""
import hmac
import secrets
import uuid
from hashlib import sha256
from typing import Optional

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

COOKIE_NAME = "tm_guest_id"
_COOKIE_MAX_AGE = 365 * 24 * 3600
_SIGNATURE_LEN = 32


def _signature(guest_id: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), guest_id.encode(), sha256).hexdigest()[:_SIGNATURE_LEN]


def sign_guest_id(guest_id: str) -> str:
    return f"{guest_id}.{_signature(guest_id)}"


def verify_guest_cookie(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    guest_id, sep, signature = value.rpartition(".")
    if not sep or not guest_id:
        return None
    if not secrets.compare_digest(signature, _signature(guest_id)):
        return None
    return guest_id


class GuestIdentityMiddleware:
    """为无有效访客 Cookie 的请求签发匿名 ID，并写入 request.state。

    纯 ASGI 实现，确保 SSE 等流式响应也能带回 Set-Cookie。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        cookie_value = None
        for raw in (headers.get(b"cookie") or b"").split(b";"):
            name, sep, value = raw.strip().partition(b"=")
            if sep and name == COOKIE_NAME.encode():
                cookie_value = value.decode(errors="ignore")
                break

        guest_id = verify_guest_cookie(cookie_value)
        scope.setdefault("state", {})["guest_id"] = guest_id
        if guest_id is not None:
            await self.app(scope, receive, send)
            return

        guest_id = uuid.uuid4().hex
        scope["state"]["guest_id"] = guest_id
        cookie = (
            f"{COOKIE_NAME}={sign_guest_id(guest_id)}; Path=/; Max-Age={_COOKIE_MAX_AGE}; "
            "HttpOnly; SameSite=Lax"
        )
        if settings.GUEST_COOKIE_SECURE:
            cookie += "; Secure"

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((b"set-cookie", cookie.encode()))
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def get_guest_id(request: Request) -> Optional[str]:
    """FastAPI 依赖：返回当前请求的匿名访客 ID（登录请求也会携带，由调用方决定是否使用）。"""
    return getattr(request.state, "guest_id", None)
