"""
TextMirror 客户端 IP 提取工具
仅当直连方是可信代理（本机/内网 Nginx）时才信任 X-Forwarded-For，
避免直连场景下伪造请求头绕过限流或污染审计日志。
"""
import ipaddress

from fastapi import Request


def _is_trusted_proxy(host: str) -> bool:
    """直连客户端是否为本机/内网（即我们自己的 Nginx 反向代理）"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def get_client_ip(request: Request) -> str:
    """
    获取真实客户端 IP
    仅当直连方是可信代理（本机/内网 Nginx）时才信任 X-Forwarded-For，
    避免直连场景下伪造 XFF 头绕过限流
    """
    direct = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for and _is_trusted_proxy(direct):
        return forwarded_for.split(",")[0].strip()
    return direct
