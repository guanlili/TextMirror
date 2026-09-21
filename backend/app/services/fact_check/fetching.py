import asyncio
import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .constants import FETCH_TIMEOUT, MAX_BYTES, MAX_PAGE_TEXT, MAX_REDIRECTS, QUOTE_CONTEXT_CHARS
from .errors import _FetchError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(text: str) -> str:
    # Preserve Unicode characters; only collapse whitespace, never paraphrase.
    return " ".join(text.split())


def _hostname(value: str) -> str:
    return value.encode("idna").decode("ascii").lower().rstrip(".")


def _safe_path(path: str) -> str:
    for _ in range(4):
        decoded = unquote(path, errors="strict")
        if decoded == path:
            break
        path = decoded
    if ("%" in path or "\\" in path or "//" in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(part in {".", ".."} for part in path.split("/"))):
        raise ValueError("Ambiguous path")
    return path or "/"


def _validate_url(url: str, sources: list[dict] | None) -> httpx.URL:
    """Validate before DNS and again at every redirect; None means open-web mode."""
    try:
        if (not isinstance(url, str) or not url or len(url) > 4096 or "\\" in url
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)):
            raise ValueError
        parts = urlsplit(url)
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 80, 443} or "%" in parts.hostname):
            raise ValueError
        host = _hostname(parts.hostname)
        if not host:
            raise ValueError
        # Validate the raw path too: httpx normalizes literal dot segments.
        path = _safe_path(parts.path)
        parsed = httpx.URL(url).copy_with(host=host, fragment=None)
        if sources is not None:
            allowed = False
            for source in sources:
                domain = _hostname(source["domain"])
                prefix = _safe_path(source.get("path_prefix") or "/")
                if (host == domain or host.endswith("." + domain)) and (
                    path == prefix or path.startswith(prefix if prefix.endswith("/") else prefix + "/")
                ):
                    allowed = True
                    break
            if not allowed:
                raise _FetchError("SOURCE_NOT_ALLOWED", "页面域名或路径不在所选可信信源范围内。")
        return parsed
    except _FetchError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, httpx.InvalidURL):
        raise _FetchError("UNSAFE_URL", "页面 URL 不满足安全抓取要求。") from None


def _public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        if (not ip.is_global or ip.is_private or ip.is_reserved or ip.is_loopback
                or ip.is_link_local or ip.is_multicast or ip.is_unspecified):
            return False
        if isinstance(ip, ipaddress.IPv6Address):
            # Transition addresses can route to private IPv4 destinations despite appearing global.
            if (ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip.scope_id or ip.is_site_local
                    or ip in ipaddress.IPv6Network("64:ff9b::/96")
                    or ip in ipaddress.IPv6Network("64:ff9b:1::/48")):
                return False
        elif ip in ipaddress.IPv4Network("192.0.0.0/24") or ip in ipaddress.IPv4Network("192.88.99.0/24"):
            return False
        return True
    except ValueError:
        return False


async def _resolve_public(host: str, port: int) -> str:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=5
            )
            addresses = [record[4][0] for record in records]
        except (OSError, TimeoutError):
            raise _FetchError("DNS_FAILED", "页面 DNS 解析失败或超时。") from None
    # Mixed public/private DNS answers must fail closed, not just pick the public one.
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise _FetchError("UNSAFE_ADDRESS", "页面解析到非公网或保留地址。")
    return addresses[0]


def _date(value: str | None) -> str | None:
    if not value or len(value) > 64:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


class _HTMLText(HTMLParser):
    _ignored = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object"}
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _blocks = {"p", "div", "section", "article", "main", "li", "ul", "ol", "table", "tr", "td", "th",
               "h1", "h2", "h3", "h4", "header", "footer", "nav", "aside", "blockquote", "br", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.title_parts = []
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = (tag in self._ignored or "hidden" in attrs or attrs.get("aria-hidden") == "true"
                  or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style") or "", re.I)))
        ignored = hidden or any(item[1] for item in self.stack)
        if not ignored and tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            value = attrs.get("content")
            if value and key not in self.metadata:
                self.metadata[key] = value
        if not ignored and tag == "time" and attrs.get("itemprop", "").lower() == "datepublished":
            self.metadata.setdefault("datepublished", attrs.get("datetime"))
        if tag in self._blocks and not ignored:
            self.parts.append("\n")
        if tag not in self._void:
            if len(self.stack) >= 256:
                raise _FetchError("PAGE_STRUCTURE", "HTML 嵌套层数超过安全解析限制。")
            self.stack.append((tag, ignored))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in self._blocks:
            self.parts.append("\n")

    def handle_data(self, data):
        if any(item[1] for item in self.stack):
            return
        tags = {item[0] for item in self.stack}
        if "title" in tags:
            self.title_parts.append(data)
        elif "head" not in tags:
            self.parts.append(data)


@dataclass
class _Page:
    id: str
    title: str
    url: str
    text: str
    published_at: str | None
    retrieved_at: str
    publisher: str

    def evidence(self, quote: str, stance: str, checks) -> dict:
        # Hash exactly the normalized body prefix supplied to the model, encoded as UTF-8.
        # Positions/context use Unicode code points in that same text; repeated quotes use the first match.
        visible = self.text[:MAX_PAGE_TEXT]
        start = visible.index(quote)
        end = start + len(quote)
        return {"id": self.id, "title": self.title, "url": self.url, "quote": quote,
                "published_at": self.published_at, "retrieved_at": self.retrieved_at, "body_text": visible,
                "publisher": self.publisher, "stance": stance, "checks": checks.model_dump(),
                "body_sha256": hashlib.sha256(visible.encode("utf-8")).hexdigest(),
                "body_hash_scope": "normalized_model_visible_text_utf8", "body_text_length": len(visible),
                "quote_start": start, "quote_end": end,
                "context_before": visible[max(0, start - QUOTE_CONTEXT_CHARS):start],
                "context_after": visible[end:end + QUOTE_CONTEXT_CHARS]}


async def _read_limited(response: httpx.Response) -> bytes:
    # Request identity and reject compressed responses: decompression bombs never reach a decoder.
    if response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
        raise _FetchError("PAGE_ENCODING", "页面使用不支持的压缩编码。")
    length = response.headers.get("content-length")
    if length:
        try:
            if int(length) < 0 or int(length) > MAX_BYTES:
                raise _FetchError("PAGE_TOO_LARGE", "响应超过 1MB 限制。")
        except ValueError:
            raise _FetchError("PAGE_FORMAT", "响应长度格式无效。") from None
    body = bytearray()
    async for chunk in response.aiter_raw():
        if len(body) + len(chunk) > MAX_BYTES:
            raise _FetchError("PAGE_TOO_LARGE", "响应超过 1MB 限制。")
        body.extend(chunk)
    return bytes(body)


async def _fetch_page(url: str, sources: list[dict] | None) -> "_Page":
    try:
        async with asyncio.timeout(FETCH_TIMEOUT):
            for hop in range(MAX_REDIRECTS + 1):
                parsed = _validate_url(url, sources)
                address = await _resolve_public(parsed.host, parsed.port or (443 if parsed.scheme == "https" else 80))
                # Pin the IP per hop while preserving TLS hostname verification and origin isolation.
                pinned = parsed.copy_with(host=address)
                async with httpx.AsyncClient(
                    verify=True, trust_env=False, follow_redirects=False, timeout=httpx.Timeout(10),
                ) as client:
                    async with client.stream(
                        "GET", pinned,
                        headers={"Host": parsed.netloc.decode("ascii"), "Accept-Encoding": "identity",
                                 "Accept": "text/html, application/xhtml+xml, text/plain",
                                 "User-Agent": "TextMirror-FactCheck/1.0"},
                        extensions={"sni_hostname": parsed.host},
                    ) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if hop == MAX_REDIRECTS or not location:
                                raise _FetchError("REDIRECT_LIMIT", "页面重定向次数超限或缺失目标。")
                            url = urljoin(str(parsed), location)
                            continue
                        if response.status_code != 200:
                            raise _FetchError("PAGE_HTTP_ERROR", "页面返回非成功状态。")
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                            raise _FetchError("PAGE_TYPE", "只允许 HTML 或纯文本正文。")
                        raw = await _read_limited(response)
                        encoding = response.charset_encoding
                        if not encoding and content_type != "text/plain":
                            declared = re.search(rb"<meta\b[^>]*\bcharset\s*=\s*['\"]?\s*([a-zA-Z0-9._-]+)", raw[:8192], re.I)
                            encoding = declared.group(1).decode("ascii") if declared else None
                        try:
                            decoded = raw.decode(encoding or "utf-8", errors="replace")
                        except LookupError:
                            decoded = raw.decode("utf-8", errors="replace")
                metadata = {}
                if content_type == "text/plain":
                    text, title = _normalize(decoded), parsed.host
                else:
                    parser = _HTMLText()
                    parser.feed(decoded)
                    parser.close()
                    text = _normalize("".join(parser.parts))
                    metadata = parser.metadata
                    title = _normalize("".join(parser.title_parts)) or _normalize(metadata.get("og:title", ""))
                if not text:
                    raise _FetchError("EMPTY_PAGE", "未取得可用正文，搜索摘要不作为证据。")
                published = next((date for key in ("article:published_time", "datepublished", "dc.date", "date")
                                  if (date := _date(metadata.get(key)))), None)
                return _Page("", (title or parsed.host)[:500], str(parsed), text, published, _now(),
                             _normalize(metadata.get("og:site_name", ""))[:200] or parsed.host)
    except _FetchError:
        raise
    except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError, RuntimeError):
        raise _FetchError("PAGE_FETCH_FAILED", "安全抓取失败或超时；未尝试不安全回退。") from None
    raise _FetchError("REDIRECT_LIMIT", "页面重定向次数超限。")
