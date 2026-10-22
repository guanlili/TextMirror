"""
客户端 IP。

只信任配置里的反向代理。直连对端不在 TRUSTED_PROXIES 中时，忽略
X-Forwarded-For 和 X-Real-IP，直接使用套接字对端。对端是可信代理时，
从转发链的右侧丢掉仍属于可信代理的跳，剩下最右边的地址才是客户端。
客户端写在最左边的 127.0.0.1 或内网地址因此不会生效。
"""
import ipaddress

from fastapi import Request

from app.core.config import settings

_network_cache: dict[str, tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = {}


def _parse_networks(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """空列表不匹配任何地址。调用方不得把「没配」理解成信任所有来源。"""
    cached = _network_cache.get(raw)
    if cached is not None:
        return cached
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        networks.append(ipaddress.ip_network(part, strict=False))
    parsed = tuple(networks)
    _network_cache[raw] = parsed
    return parsed


def _ip_in_networks(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, raw: str) -> bool:
    ip = _unwrap(ip)
    return any(ip.version == network.version and ip in network for network in _parse_networks(raw))


def _unwrap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped if mapped is not None else ip


def _format(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    return str(_unwrap(ip))


def _parse_ip(token: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """解析转发头里的一跳。接受裸 IP、IPv4:port 和 [IPv6]:port。"""
    value = token.strip().strip('"').strip("'").strip()
    if not value or value.lower() == "unknown":
        return None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        value = value[1:end]
    elif value.count(":") == 1:
        host, maybe_port = value.rsplit(":", 1)
        if maybe_port.isdigit() and host.count(".") == 3:
            value = host
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_trusted_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return _ip_in_networks(ip, settings.TRUSTED_PROXIES or "")


def is_internal_network(value: str) -> bool:
    """一键登录来源是否落在 INTERNAL_NETWORKS。回环默认不在名单里。"""
    ip = _parse_ip(value)
    if ip is None:
        return False
    return _ip_in_networks(ip, settings.INTERNAL_NETWORKS or "")


def _header_values(request: Request, name: str) -> list[str]:
    return request.headers.getlist(name)


def _forwarded_tokens(request: Request) -> list[str]:
    tokens: list[str] = []
    for value in _header_values(request, "x-forwarded-for"):
        tokens.extend(part.strip() for part in value.split(","))
    return [token for token in tokens if token]


def _client_from_chain(chain: list[str], direct: str) -> str:
    """从右侧取第一个不是可信代理的地址。

    最右侧如果无法解析，说明最近一跳不是合法 IP。此时不再向左继续，
    避免把客户端伪造的内网地址当成真实来源。
    """
    parsed = [(token, _parse_ip(token)) for token in chain]
    for _token, ip in reversed(parsed):
        if ip is None:
            return direct
        if _is_trusted_ip(ip):
            continue
        return _format(ip)
    for _token, ip in parsed:
        if ip is not None:
            return _format(ip)
    return direct


def get_client_ip(request: Request) -> str:
    """返回用于限流、锁定、内网判断和审计的客户端 IP。"""
    direct_raw = request.client.host if request.client and request.client.host else "unknown"
    direct_ip = _parse_ip(direct_raw)
    if direct_ip is None or not _is_trusted_ip(direct_ip):
        return _format(direct_ip) if direct_ip is not None else direct_raw

    direct = _format(direct_ip)
    chain = _forwarded_tokens(request)
    if not chain:
        real_values = _header_values(request, "x-real-ip")
        if real_values and real_values[-1].strip():
            chain = [real_values[-1].strip()]
    if not chain:
        return direct
    return _client_from_chain(chain, direct)
