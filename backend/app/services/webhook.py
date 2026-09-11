"""
TextMirror Webhook 回调服务
异步文档审校任务（开放 API 提交）完成/失败时向密钥配置的 webhook_url 推送通知。

签名约定：X-TextMirror-Signature: sha256=<HMAC-SHA256(secret, raw_body).hexdigest()>
集成方验签示例（Python）：
    hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
"""
import hashlib
import hmac
import ipaddress
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

from loguru import logger

ALLOWED_SCHEMES = {"http", "https"}


def validate_webhook_url(url: str, *, allow_private: bool = False) -> str:
    """
    校验回调地址：http(s) scheme + 主机可解析 + 非内网/保留地址（防 SSRF）。

    :param allow_private: DEBUG 本地联调时允许内网地址（生产必须 False）
    :raises ValueError: 地址不合法时给出原因
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError("回调地址必须是 http:// 或 https://")
    host = parsed.hostname
    if not host:
        raise ValueError("回调地址缺少主机名")

    try:
        addr_infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"回调主机 {host} 无法解析")

    for info in addr_infos:
        ip = ipaddress.ip_address(info[4][0])
        if not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast):
            raise ValueError(f"回调地址不允许指向内网/保留地址（解析到 {ip}）。本地联调请用公网可达地址")
    return url


def sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 签名，返回 'sha256=<hexdigest>' 头格式"""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """校验签名（恒定时间比较）；供文档示例与测试使用"""
    expected = sign_payload(secret, body)
    return hmac.compare_digest(expected, signature_header or "")


def build_event(event: str, job_id: str, data: dict | None = None) -> dict:
    """构造回调事件体（字段顺序固定，body 序列化后即签名对象）"""
    return {
        "event": event,
        "api_version": "v1",
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data or {},
    }


def dispatch_webhook(api_key_id: int, event: dict) -> None:
    """
    投递回调（Celery 异步，带重试退避）。密钥未配置 webhook_url 时静默跳过。
    Celery broker 不可用只记日志——回调是尽力而为的增值通知，不能阻塞主任务收尾。
    """
    from app.tasks.webhook_task import webhook_deliver
    try:
        webhook_deliver.delay(api_key_id, event)
    except Exception as e:
        logger.warning(f"[Webhook] 投递任务入队失败 api_key_id={api_key_id} event={event.get('event')}: {e}")


__all__ = [
    "build_event",
    "dispatch_webhook",
    "sign_payload",
    "validate_webhook_url",
    "verify_signature",
]
