"""All LLM, search, DNS and HTTP traffic is mocked; no paid API calls."""

import asyncio
import copy
import hashlib
import json
import socket
from types import SimpleNamespace

import httpx
import pytest

from app.schemas.fact_check import FactCheckReport
from app.services import fact_check as fc

_REAL_CLIENT = httpx.AsyncClient
TEXT = "2024年该市人口为100万人。"
QUOTE = "2024年该市常住人口为100万人。"
SOURCES = [{"id": "s1", "name": "统计局", "domain": "example.com", "path_prefix": "/news", "is_enabled": True}]


class Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        assert self.responses, "Unexpected model call"
        content = self.responses.pop(0)
        if isinstance(content, Exception):
            raise content
        return SimpleNamespace(content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                               usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    async def close(self):
        self.closed = True


class Stream(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def response(status=200, body=b"", headers=None):
    return httpx.Response(status, headers=headers or {"content-type": "text/html"}, stream=Stream(body))


def extract(text=TEXT):
    return {"claims": [{"original": text, "start": 0, "end": len(text), "statement": text}]}


def checks(**statuses):
    return {key: {"status": statuses.get(key, "match"), "reason": reason} for key, reason in (
        ("subject", "正文说明同一城市的人口。"), ("event_time", "正文统计事件时间为2024年，不使用发布日期。"),
        ("scope_unit", "同为该市常住人口，单位万人；不比较数字值是否相等。"),
    )}


def decision(verdict="supported", evidence=None, **kwargs):
    if evidence is None:
        evidence = [{"id": "c1-e1", "quote": QUOTE, "stance": "supports", "checks": checks()}]
    return {"verdict": verdict, "reason": "原始正文给出2024年常住人口统计口径。", "suggestion": None,
            "evidence": evidence, **kwargs}


def page(url="https://example.com/news/report", text=QUOTE, id="c1-e1"):
    return fc._Page(id, "统计公报", url, text, "2025-01-01T00:00:00+00:00", fc._now(), "统计局")


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Real HTTP clients are prohibited in fact-check tests")
    monkeypatch.setattr(fc.httpx, "AsyncClient", denied)


@pytest.fixture
def mock_http(monkeypatch):
    def install(handler):
        options, requests = [], []

        async def dispatch(request):
            requests.append(request)
            return handler(request)

        def factory(**kwargs):
            options.append(dict(kwargs))
            return _REAL_CLIENT(transport=httpx.MockTransport(dispatch), **kwargs)

        monkeypatch.setattr(fc.httpx, "AsyncClient", factory)
        return options, requests
    return install


@pytest.fixture
async def public_dns(monkeypatch):
    calls = []

    async def resolve(host, port, **kwargs):
        calls.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    return calls


def mock_evidence(monkeypatch, result_page=None):
    searches = []

    async def search(query, key, sources):
        searches.append((query, key, sources))
        return ["https://example.com/news/report"]

    async def fetch(url, sources):
        return copy.deepcopy(result_page or page())

    monkeypatch.setattr(fc, "_search", search)
    monkeypatch.setattr(fc, "_fetch_page", fetch)
    return searches


async def run(provider, **kwargs):
    return await fc.run_fact_check(kwargs.pop("text", TEXT), mode=kwargs.pop("mode", "web"),
                                   sources=kwargs.pop("sources", SOURCES), api_key=kwargs.pop("api_key", "test-key"),
                                   search_provider=kwargs.pop("search_provider", "tavily"), provider=provider, **kwargs)


@pytest.mark.parametrize("empty", [{"claims": []}, []])
async def test_empty_report_is_success_and_reads_full_input(empty):
    text = "仅为愿景。" * 3998 + "希望世界更好。"
    provider = Provider(empty)
    events = []

    async def progress(percent, message, report=None):
        events.append((percent, report))

    report = await run(provider, text=text, api_key="", on_progress=progress)
    assert report["claims"] == []
    assert report["coverage"]["status"] == "complete"
    assert report["coverage"]["extracted"] == report["coverage"]["checked"] == 0
    assert report["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                               "search_queries": 0, "pages_fetched": 0}
    assert json.loads(provider.calls[0]["messages"][1]["content"])["text"] == text
    assert events[0][0] == 0 and events[-1][0] == 100
    assert not provider.closed


@pytest.mark.parametrize("content", ["not JSON", "[{}]", "{}", '{"claims":null}', '{"claims":[{}]}',
                                     '{"claims":[{"original":"x","statement":3}]}',
                                     '{"claims":[],"error":"failed"}', '{"claims":[],"success":false}'])
async def test_malformed_extraction_is_not_no_facts(content):
    with pytest.raises(fc.FactCheckError) as exc:
        await run(Provider(content))
    assert exc.value.code == "MODEL_FORMAT_ERROR"


@pytest.mark.parametrize("address", ["64:ff9b::7f00:1", "64:ff9b:1::a00:1", "fec0::1", "192.0.0.8", "192.88.99.1"])
def test_transition_and_special_purpose_addresses_are_rejected(address):
    assert not fc._public_ip(address)


async def test_model_provider_failure_is_sanitized():
    with pytest.raises(fc.FactCheckError) as exc:
        await run(Provider(RuntimeError("secret-key and request headers")))
    assert exc.value.code == "MODEL_PROVIDER_ERROR"
    assert "secret-key" not in str(exc.value)


async def test_truncated_json_response_fails_even_if_valid_json():
    class Truncated(Provider):
        async def chat(self, **kwargs):
            result = await super().chat(**kwargs)
            result.finish_reason = "length"
            return result

    with pytest.raises(fc.FactCheckError, match="截断"):
        await run(Truncated({"claims": []}))


def test_unicode_offsets_repetition_and_context_are_verified():
    text = "\U0001f600前文。同一句。中段。同一句。结尾。"
    second = text.rindex("同一句。")
    claims, issues = fc._claims_from_model({"claims": [
        {"original": "同一句。", "statement": "second", "start": second, "end": second + 4},
        {"original": "同一句。", "statement": "ambiguous"},
        {"original": "前文。", "statement": "wrong UTF16", "start": 2, "end": 5},
        {"original": "结尾。", "statement": "unique"},
    ]}, text)
    assert [claim["original"] for claim in claims] == ["同一句。", "结尾。"]
    assert claims[0]["start"] == second
    assert issues
    for claim in claims:
        assert text[claim["start"]:claim["end"]] == claim["original"]
    assert fc._locate(text, {"original": "同一句。", "context_before": "中段。", "context_after": "结尾。"}) == (second, second + 4)
    assert fc._locate("aaaa", {"original": "aa"}) is None  # overlapping duplicates
    assert fc._locate("唯一", {"original": "唯一", "start": 3, "end": 5}) is None


def test_distinct_atomic_claims_in_same_sentence_are_not_dropped():
    text = "该机构于2020年成立，并于2023年发布报告。"
    first = {"original": text, "statement": "该机构于2020年成立"}
    second = {"original": text, "statement": "该机构于2023年发布报告"}
    claims, issues = fc._claims_from_model({"claims": [first, second, first]}, text)
    assert len(claims) == 2 and not issues
    assert claims[0]["start"] == claims[1]["start"] == 0
    assert claims[0]["id"] != claims[1]["id"]


async def test_meta_charset_decodes_chinese_page(mock_http, public_dns):
    body = '<html><head><meta charset="gb2312"><title>统计公报</title></head><body><p>人口为100万人。</p></body></html>'.encode("gb2312")
    mock_http(lambda request: response(body=body))
    result = await fc._fetch_page("https://example.com/news/report", SOURCES)
    assert result.title == "统计公报" and "人口为100万人。" in result.text


async def test_invalid_locations_are_skipped_but_coverage_is_partial():
    report = await run(Provider({"claims": [{"original": "不存在", "statement": "不能映射"}]}))
    assert report["claims"] == []
    assert report["coverage"]["status"] == "partial"
    assert "坐标" in report["coverage"]["reason"]


async def test_extraction_cap_and_zero_check_budget():
    text = " ".join(f"事实{i}。" for i in range(31))
    raw = {"claims": [{"original": f"事实{i}。", "statement": f"事实{i}。"} for i in range(31)]}
    report = await run(Provider(raw), text=text, max_claims=0)
    assert len(report["claims"]) == 30
    assert report["coverage"]["unverified"] == 30
    assert report["coverage"]["status"] == "partial"
    assert all(not claim["checked"] and claim["verdict"] == "insufficient" for claim in report["claims"])
    assert all("预算" in claim["reason"] for claim in report["claims"])
    assert report["usage"]["search_queries"] == 0


async def test_budget_queries_partial_callbacks_and_no_evidence(monkeypatch):
    queries, events = [], []

    async def search(query, key, sources):
        queries.append(query)
        return []

    async def progress(percent, message, report=None):
        events.append((percent, message, report))

    monkeypatch.setattr(fc, "_search", search)
    provider = Provider({"claims": [{"original": item, "statement": item} for item in ["甲", "乙", "丙"]]})
    report = await run(provider, text="甲乙丙", max_claims=2, on_progress=progress)
    assert len(queries) == 4 and len(set(queries)) == 4
    assert len(provider.calls) == 1  # no evidence => no model verdict, let alone a true/false guess
    assert report["coverage"]["extracted"] == 3
    assert report["coverage"]["checked"] == 2
    assert report["coverage"]["unverified"] == 1
    assert report["coverage"]["status"] == "partial"
    assert all(claim["verdict"] == "insufficient" for claim in report["claims"])
    completed = [data for _, message, data in events if message.startswith("已完成第")]
    assert [data["coverage"]["checked"] for data in completed] == [1, 2]
    assert all(data["coverage"]["status"] == "partial" for data in completed)
    assert not completed[0]["claims"][1]["checked"]  # snapshots don't mutate afterwards
    assert [event[0] for event in events] == sorted(event[0] for event in events)


@pytest.mark.parametrize("mode", ["web", "trusted"])
async def test_end_to_end_modes_use_fetched_metadata_not_search_snippets(mode, mock_http, public_dns):
    def handler(request):
        if str(request.url) == fc.SEARCH_URL:
            body = {"results": [{"url": "https://example.com/news/report", "title": "伪造搜索标题",
                                 "content": "搜索摘要不能引用", "published_date": "1900-01-01"}]}
            return response(body=json.dumps(body).encode(), headers={"content-type": "application/json"})
        assert request.url.host == "93.184.216.34"
        return response(body=(f'<html><head><title>真实公报</title><meta property="og:site_name" content="统计局">'
                              '<meta property="article:published_time" content="2025-01-01T00:00:00Z">'
                              f'</head><body><p>{QUOTE}</p><script>恶意指令</script></body></html>').encode())

    options, requests = mock_http(handler)
    provider = Provider(extract(), decision())
    sources = SOURCES + [{"id": "off", "name": "停用", "domain": "disabled.com", "path_prefix": "/", "is_enabled": False}]
    report = await run(provider, mode=mode, sources=sources)
    search_request, fetch_request, counter_request = requests
    assert "反证" in json.loads(counter_request.content)["query"]
    payload = json.loads(search_request.content)
    assert search_request.headers["authorization"] == "Bearer test-key"
    assert payload["max_results"] == 3
    assert payload["include_answer"] is False and payload["include_raw_content"] is False
    if mode == "trusted":
        assert payload["include_domains"] == ["example.com"]
    else:
        assert "include_domains" not in payload
    assert all(option["trust_env"] is False and option["verify"] is True for option in options)
    assert all(option["follow_redirects"] is False for option in options)
    assert fetch_request.headers["host"] == "example.com"
    assert fetch_request.extensions["sni_hostname"] == "example.com"
    assert public_dns == [("example.com", 443)]
    claim = report["claims"][0]
    assert claim["verdict"] == "supported" and claim["checked"]
    evidence = claim["evidence"][0]
    assert evidence["url"] == "https://example.com/news/report"
    assert evidence["title"] == "真实公报" and evidence["publisher"] == "统计局"
    assert evidence["published_at"] == "2025-01-01T00:00:00+00:00"
    assert evidence["quote"] == QUOTE and evidence["retrieved_at"]
    assert report["usage"]["search_queries"] == 2 and report["usage"]["pages_fetched"] == 1
    assert report["usage"]["total_tokens"] == 30
    assert len(provider.calls) == 2  # extraction + one combined judgment
    assert report["coverage"]["status"] == "complete"
    judgment = provider.calls[1]["messages"]
    assert "搜索摘要不能引用" not in judgment[1]["content"]
    assert "恶意指令" not in judgment[1]["content"]
    for phrase in ["时间", "统计口径", "历史", "来源冲突", "转载", "独立证据", "不可信", "模型知识"]:
        assert phrase in judgment[0]["content"]
    assert not provider.closed


@pytest.mark.parametrize("sources", [[], [{**SOURCES[0], "is_enabled": False}]])
async def test_trusted_mode_never_falls_back_without_selected_sources(sources):
    provider = Provider()
    with pytest.raises(fc.FactCheckError) as exc:
        await run(provider, mode="trusted", sources=sources)
    assert exc.value.code == "NO_TRUSTED_SOURCES"
    assert not provider.calls


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/a", "javascript:alert(1)", "//example.com",
    "http://user:pass@example.com", "https://user@example.com", "https://example.com:8080",
    "https://example.com:22", "http://example.com\\@evil.com/", "https://example.com/\npath",
    "http://[fe80::1%25en0]/", "http://", "https://example.com/news/%2e%2e/private",
    "https://example.com/news/%252e%252e/private", "https://example.com/news/../private",
])
def test_unsafe_url_syntax(url):
    with pytest.raises(fc._FetchError):
        fc._validate_url(url, None)


@pytest.mark.parametrize("url", ["https://example.com.evil.com/news/a", "https://notexample.com/news/a",
                                  "https://example.com/private", "https://example.com/newsletter",
                                  "https://example.com/news%2f..%2fprivate"])
def test_trusted_domain_and_path_boundaries(url):
    with pytest.raises(fc._FetchError):
        fc._validate_url(url, SOURCES)


@pytest.mark.parametrize("url", ["https://example.com/news", "https://sub.example.com/news/a",
                                  "https://EXAMPLE.com/news/a", "https://example.com./news/a"])
def test_valid_trusted_url_boundaries(url):
    assert fc._validate_url(url, SOURCES).host.rstrip(".").endswith("example.com")


def test_trailing_slash_prefix_does_not_authorize_parent_path():
    sources = [{**SOURCES[0], "path_prefix": "/news/"}]
    with pytest.raises(fc._FetchError):
        fc._validate_url("https://example.com/news", sources)
    assert fc._validate_url("https://example.com/news/", sources)
    assert fc._validate_url("https://example.com/news/a", sources)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                                 "100.64.0.1", "0.0.0.0", "192.0.2.1", "198.18.0.1", "224.0.0.1",
                                 "240.0.0.1", "255.255.255.255", "::1", "::", "fc00::1", "fe80::1",
                                 "2001:db8::1", "ff02::1", "::ffff:93.184.216.34", "2002:5db8:d822::1"])
async def test_blocks_private_reserved_and_transition_ips(ip):
    with pytest.raises(fc._FetchError) as exc:
        await fc._resolve_public(ip, 443)
    assert exc.value.code == "UNSAFE_ADDRESS"


async def test_mixed_dns_answers_are_rejected(monkeypatch):
    async def dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
                for address in ["93.184.216.34", "127.0.0.1"]]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    with pytest.raises(fc._FetchError, match="非公网"):
        await fc._fetch_page("https://example.com/", None)


async def test_dns_rebinding_cannot_change_actual_connection(monkeypatch, mock_http):
    calls = []

    async def dns(host, port, **kwargs):
        calls.append(host)
        address = "93.184.216.34" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    options, requests = mock_http(lambda request: response(body=b"<p>public text</p>"))
    result = await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert calls == ["example.com"]
    assert str(requests[0].url) == "https://93.184.216.34/news/a"
    assert requests[0].headers["host"] == "example.com"
    assert requests[0].extensions["sni_hostname"] == "example.com"
    assert result.url == "https://example.com/news/a"
    assert options[0]["verify"] and not options[0]["trust_env"]


@pytest.mark.parametrize("target", ["http://127.0.0.1/admin", "http://169.254.169.254/latest", "file:///etc/passwd",
                                     "https://example.com/private", "https://evil.com/news/a"])
async def test_every_redirect_is_revalidated(target, mock_http, public_dns):
    _, requests = mock_http(lambda request: response(302, headers={"location": target}))
    with pytest.raises(fc._FetchError):
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert len(requests) == 1


async def test_web_redirect_to_private_ip_is_rejected(mock_http, public_dns):
    _, requests = mock_http(lambda request: response(302, headers={"location": "http://127.0.0.1/a"}))
    with pytest.raises(fc._FetchError) as exc:
        await fc._fetch_page("https://example.com/news/a", None)
    assert exc.value.code == "UNSAFE_ADDRESS" and len(requests) == 1


async def test_same_host_redirect_re_resolves_and_rejects_rebinding(monkeypatch, mock_http):
    calls = []

    async def dns(host, port, **kwargs):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34" if len(calls) == 1 else "10.0.0.1", port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    _, requests = mock_http(lambda request: response(302, headers={"location": "/news/next"}))
    with pytest.raises(fc._FetchError, match="非公网"):
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert len(calls) == 2 and len(requests) == 1


async def test_redirect_limit(mock_http, public_dns):
    _, requests = mock_http(lambda request: response(302, headers={"location": "/news/again"}))
    with pytest.raises(fc._FetchError) as exc:
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert exc.value.code == "REDIRECT_LIMIT"
    assert len(requests) == fc.MAX_REDIRECTS + 1


async def test_valid_redirect_uses_final_url_and_pins_new_hostname(mock_http, public_dns):
    def handler(request):
        if request.headers["host"] == "example.com":
            return response(302, headers={"location": "https://sub.example.com/news/final"})
        return response(body=b"Final text", headers={"content-type": "text/plain; charset=utf-8"})

    _, requests = mock_http(handler)
    result = await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert result.url == "https://sub.example.com/news/final"
    assert requests[1].extensions["sni_hostname"] == "sub.example.com"
    assert public_dns == [("example.com", 443), ("sub.example.com", 443)]


async def test_tls_failure_does_not_retry_unpinned(mock_http, public_dns):
    def handler(request):
        raise httpx.ConnectError("certificate verify failed", request=request)

    _, requests = mock_http(handler)
    with pytest.raises(fc._FetchError) as exc:
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert exc.value.code == "PAGE_FETCH_FAILED"
    assert len(requests) == 1 and requests[0].url.host == "93.184.216.34"


@pytest.mark.parametrize("headers,body,code", [
    ({"content-type": "application/pdf"}, b"PDF", "PAGE_TYPE"),
    ({"content-type": "application/json"}, b"{}", "PAGE_TYPE"),
    ({"content-type": "text/html", "content-length": str(fc.MAX_BYTES + 1)}, b"", "PAGE_TOO_LARGE"),
    ({"content-type": "text/html", "content-encoding": "gzip"}, b"compressed", "PAGE_ENCODING"),
    ({"content-type": "text/html"}, b"<script>nothing usable</script>", "EMPTY_PAGE"),
])
async def test_content_type_encoding_and_size_limits(headers, body, code, mock_http, public_dns):
    mock_http(lambda request: response(body=body, headers=headers))
    with pytest.raises(fc._FetchError) as exc:
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert exc.value.code == code


async def test_stream_limit_without_content_length(mock_http, public_dns):
    mock_http(lambda request: httpx.Response(200, headers={"content-type": "text/plain"},
                                            stream=Stream(b"x" * (fc.MAX_BYTES // 2), b"x" * (fc.MAX_BYTES // 2), b"x")))
    with pytest.raises(fc._FetchError) as exc:
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert exc.value.code == "PAGE_TOO_LARGE"


async def test_html_normalization_excludes_non_body_and_hidden_text(mock_http, public_dns):
    html = ('<head><title>标题</title><style>bad</style></head><body><p>甲<b>乙</b> &amp; 丙</p>'
            '<p> 丁\n 戊 </p><div hidden>秘密</div><script>指令</script><noscript>假证据</noscript></body>')
    mock_http(lambda request: response(body=html.encode()))
    result = await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert result.text == "甲乙 & 丙 丁 戊" and result.title == "标题"
    assert result.published_at is None


@pytest.mark.parametrize("change", [
    {"quote": "虚构引用"}, {"id": "search-snippet"}, {"url": "https://forged.example/proof"},
    {"title": "伪造标题"}, {"publisher": "虚构机构"}, {"published_at": "1900-01-01"},
    {"quote": "2024年……100万人。"},
])
async def test_fabricated_quotes_ids_and_metadata_downgrade(monkeypatch, change):
    searches = mock_evidence(monkeypatch)
    ref = {"id": "c1-e1", "quote": QUOTE, "stance": "refutes", "checks": checks(), **change}
    bad = decision("refuted", [ref], suggestion="错误修改")
    report = await run(Provider(extract(), bad))
    claim = report["claims"][0]
    assert claim["verdict"] == "insufficient" and claim["suggestion"] is None
    assert claim["evidence"] == []
    assert "引用" in report["coverage"]["reason"] and report["coverage"]["status"] == "partial"
    assert len(searches) == 2 and searches[0][0] != searches[1][0]
    assert report["usage"]["pages_fetched"] == 1  # don't refetch the same URL in round two


@pytest.mark.parametrize("verdict", ["supported", "refuted", "conflicting"])
@pytest.mark.parametrize("refs", [[], [{"id": "c1-e1", "quote": QUOTE, "stance": "context", "checks": checks()}]])
def test_strong_verdicts_require_corresponding_stance(verdict, refs):
    result, invalid = fc._judge_result(decision(verdict, refs, suggestion="撤回此修改"), [page()])
    assert invalid and result["verdict"] == "insufficient" and result["suggestion"] is None


def test_conflict_requires_different_material_and_support_and_refutation():
    left = page()
    right = page(url="https://other.example/a", text="2024年该市常住人口为90万人。", id="c1-e2")
    refs = [{"id": left.id, "quote": left.text, "stance": "supports", "checks": checks()},
            {"id": right.id, "quote": right.text, "stance": "refutes", "checks": checks()}]
    result, invalid = fc._judge_result(decision("conflicting", refs), [left, right])
    assert result["verdict"] == "conflicting" and not invalid
    right.text = left.text
    refs[1]["quote"] = left.text
    result, invalid = fc._judge_result(decision("conflicting", refs), [left, right])
    assert invalid and result["verdict"] == "insufficient"


def test_valid_refutation_can_keep_suggestion_but_top_level_forged_url_is_rejected():
    refs = [{"id": "c1-e1", "quote": QUOTE, "stance": "refutes", "checks": checks()}]
    result, invalid = fc._judge_result(decision("refuted", refs, suggestion="证据内的修正"), [page()])
    assert not invalid and result["suggestion"] == "证据内的修正"
    result, invalid = fc._judge_result(decision("refuted", refs, url="https://forged.example", suggestion="撤回"), [page()])
    assert invalid and result["suggestion"] is None


async def test_identical_reprints_are_not_multiple_evidence_sources(monkeypatch):
    async def search(*args):
        return ["https://example.com/news/a", "https://other.example/reprint"]

    async def fetch(url, sources):
        return page(url=url)

    monkeypatch.setattr(fc, "_search", search)
    monkeypatch.setattr(fc, "_fetch_page", fetch)
    provider = Provider(extract(), decision())
    report = await run(provider)
    data = json.loads(provider.calls[1]["messages"][1]["content"])
    assert len(data["pages"]) == 1
    assert report["usage"]["pages_fetched"] == 2


async def test_fetch_errors_mark_partial_and_insufficient(monkeypatch):
    async def search(*args):
        return ["https://example.com/news/a"]

    async def fetch(*args):
        raise fc._FetchError("PAGE_FETCH_FAILED", "failed")

    monkeypatch.setattr(fc, "_search", search)
    monkeypatch.setattr(fc, "_fetch_page", fetch)
    report = await run(Provider(extract()))
    assert report["claims"][0]["verdict"] == "insufficient"
    assert report["claims"][0]["checked"]
    assert report["coverage"]["status"] == "partial"
    assert "技术原因" in report["coverage"]["reason"]


async def test_search_outage_raises_global_error(monkeypatch):
    calls = []

    async def search(*args):
        calls.append(args)
        raise fc._SearchFailure("SEARCH_UNAVAILABLE", "failed")

    monkeypatch.setattr(fc, "_search", search)
    with pytest.raises(fc.FactCheckError) as exc:
        await run(Provider(extract()))
    assert exc.value.code == "SEARCH_PROVIDER_ERROR" and len(calls) == 2


@pytest.mark.parametrize("status,code", [(401, "SEARCH_AUTH_ERROR"), (403, "SEARCH_AUTH_ERROR"),
                                        (402, "SEARCH_PROVIDER_ERROR"), (429, "SEARCH_PROVIDER_ERROR")])
async def test_search_auth_and_quota_errors_fail_immediately(status, code, mock_http):
    _, requests = mock_http(lambda request: response(status))
    with pytest.raises(fc.FactCheckError) as exc:
        await run(Provider(extract()))
    assert exc.value.code == code and len(requests) == 1


async def test_search_api_redirect_is_not_followed(mock_http):
    _, requests = mock_http(lambda request: response(302, headers={"location": "https://other.example"}))
    with pytest.raises(fc._SearchFailure):
        await fc._search("query", "key", None)
    assert len(requests) == 1


@pytest.mark.parametrize("cancel_at", ["正在从", "已提取", "正在检索", "正在安全抓取", "正在依据", "正在校验", "已完成第", "事实核查完成"])
async def test_callback_errors_propagate_at_every_stage(monkeypatch, cancel_at):
    searches = mock_evidence(monkeypatch)
    error = RuntimeError("worker cancellation signal")

    async def callback(percent, message, report=None):
        if message.startswith(cancel_at):
            raise error

    provider = Provider(extract(), decision())
    with pytest.raises(RuntimeError) as exc:
        await run(provider, on_progress=callback)
    assert exc.value is error
    assert not provider.closed and len(searches) <= 2


async def test_async_cancellation_is_not_swallowed(monkeypatch):
    mock_evidence(monkeypatch)

    async def callback(percent, message, report=None):
        if percent >= 10:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run(Provider(extract()), on_progress=callback)


@pytest.mark.parametrize("ip", ["93.184.216.34", "2606:4700:4700::1111"])
async def test_real_httpcore_transport_pins_tcp_and_verifies_original_tls_name(ip, monkeypatch):
    import ssl

    from httpcore._backends.auto import AutoBackend
    from httpcore._backends.base import AsyncNetworkStream

    connections, writes, handshakes = [], [], []

    class Wire(AsyncNetworkStream):
        sent = False

        async def read(self, max_bytes, timeout=None):
            if self.sent:
                return b""
            self.sent = True
            return b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 4\r\nConnection: close\r\n\r\ntext"

        async def write(self, buffer, timeout=None):
            writes.append(buffer)

        async def aclose(self):
            pass

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            handshakes.append(server_hostname)
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert ssl_context.check_hostname is True
            return self

    async def connect(self, host, port, **kwargs):
        connections.append((host, port))
        return Wire()

    async def dns(host, port, **kwargs):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(AutoBackend, "connect_tcp", connect)
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setattr(fc.httpx, "AsyncClient", _REAL_CLIENT)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    result = await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert result.text == "text"
    assert connections == [(ip, 443)]
    assert handshakes == ["example.com"]
    assert b"Host: example.com\r\n" in b"".join(writes)


@pytest.mark.parametrize("value", [[], {}, 1, None])
def test_malformed_verdict_types_fail_explicitly(value):
    with pytest.raises(fc.FactCheckError) as exc:
        fc._judge_result(decision(value), [page()])
    assert exc.value.code == "MODEL_FORMAT_ERROR"


@pytest.mark.parametrize("value", [[], {}, 1, None])
def test_malformed_stance_types_downgrade(value):
    result, invalid = fc._judge_result(decision(evidence=[{"id": "c1-e1", "quote": QUOTE, "stance": value}]), [page()])
    assert invalid and result["verdict"] == "insufficient" and not result["evidence"]


async def test_unsupported_sni_transport_never_falls_back(mock_http, public_dns):
    def handler(request):
        raise TypeError("sni_hostname is not supported")

    _, requests = mock_http(handler)
    with pytest.raises(fc._FetchError) as exc:
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert exc.value.code == "PAGE_FETCH_FAILED" and len(requests) == 1


async def test_deeply_nested_html_is_bounded(mock_http, public_dns):
    mock_http(lambda request: response(body=b"<div>" * 257 + b"text"))
    with pytest.raises(fc._FetchError) as exc:
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert exc.value.code == "PAGE_STRUCTURE"


async def test_fetch_total_timeout_bounds_slow_stream(monkeypatch, mock_http, public_dns):
    class HangingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"never"

    monkeypatch.setattr(fc, "FETCH_TIMEOUT", 0.01)
    mock_http(lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, stream=HangingStream()))
    with pytest.raises(fc._FetchError) as exc:
        await fc._fetch_page("https://example.com/news/a", SOURCES)
    assert exc.value.code == "PAGE_FETCH_FAILED"


async def test_transient_search_failure_can_return_partial_report(monkeypatch):
    queries = []

    async def search(query, key, sources):
        queries.append(query)
        if len(queries) == 1:
            raise fc._SearchFailure("SEARCH_UNAVAILABLE", "transient")
        return []

    monkeypatch.setattr(fc, "_search", search)
    report = await run(Provider(extract()))
    assert len(queries) == 2
    assert report["coverage"]["status"] == "partial"
    assert "技术原因" in report["coverage"]["reason"]
    assert report["claims"][0]["verdict"] == "insufficient"


def test_shifted_reprint_headers_do_not_imply_independent_evidence():
    text = " ".join(f"城市{i}的统计人口为{i * 100}人。" for i in range(100))
    assert fc._same_material(page(text=text), page(text="转载声明：来源于统计公报。 " + text))


async def test_second_query_can_find_valid_evidence(monkeypatch):
    queries = []

    async def search(query, key, sources):
        queries.append(query)
        return [] if len(queries) == 1 else ["https://example.com/news/report"]

    async def fetch(url, sources):
        return page()

    monkeypatch.setattr(fc, "_search", search)
    monkeypatch.setattr(fc, "_fetch_page", fetch)
    report = await run(Provider(extract(), decision()))
    assert len(queries) == 2 and queries[0] != queries[1]
    assert report["claims"][0]["verdict"] == "supported"
    assert report["coverage"]["status"] == "complete"


async def test_quote_from_search_snippet_is_never_accepted(monkeypatch):
    mock_evidence(monkeypatch, page(text="页面的真正正文，没有人口数据。"))
    bad = decision(evidence=[{"id": "c1-e1", "quote": QUOTE, "stance": "supports", "checks": checks()}])
    report = await run(Provider(extract(), bad))
    assert report["claims"][0]["verdict"] == "insufficient"
    assert report["claims"][0]["evidence"] == []


async def test_truncated_model_page_is_disclosed_and_hidden_tail_cannot_be_cited(monkeypatch):
    long_page = page(text="甲" * (fc.MAX_PAGE_TEXT + 10) + QUOTE)
    mock_evidence(monkeypatch, long_page)
    report = await run(Provider(extract(), decision()))
    assert report["coverage"]["status"] == "partial"
    assert "阅读长度限制" in report["coverage"]["reason"]
    assert report["claims"][0]["verdict"] == "insufficient"
    assert not report["claims"][0]["evidence"]


async def test_first_round_support_and_counter_round_refutation_are_judged_together(monkeypatch):
    queries, fetched, events = [], [], []
    contrary = "2024年该市常住人口为90万人。"

    async def search(query, key, sources):
        queries.append(query)
        return [f"https://example.com/news/{len(queries)}"]

    async def fetch(url, sources):
        fetched.append(url)
        return page(url=url, text=QUOTE if len(fetched) == 1 else contrary)

    class CombinedProvider(Provider):
        async def chat(self, **kwargs):
            if self.calls:
                assert len(queries) == len(fetched) == 2
                visible = json.loads(kwargs["messages"][1]["content"])["pages"]
                assert [item["text"] for item in visible] == [QUOTE, contrary]
            return await super().chat(**kwargs)

    async def progress(percent, message, current=None):
        if current is not None:
            FactCheckReport.model_validate(current)
        events.append((percent, message, current))

    monkeypatch.setattr(fc, "_search", search)
    monkeypatch.setattr(fc, "_fetch_page", fetch)
    refs = [{"id": "c1-e1", "quote": QUOTE, "stance": "supports", "checks": checks()},
            {"id": "c1-e2", "quote": contrary, "stance": "refutes", "checks": checks()}]
    provider = CombinedProvider(extract(), decision("conflicting", refs))
    report = await run(provider, on_progress=progress)
    assert len(provider.calls) == 2 and not provider.responses
    assert "反证" in queries[1] and "更正" in queries[1]
    assert report["claims"][0]["verdict"] == "conflicting"
    assert report["usage"]["search_queries"] == report["usage"]["pages_fetched"] == 2
    rounds = report["claims"][0]["search_rounds"]
    assert [item["kind"] for item in rounds] == ["initial", "counter"]
    assert [item["status"] for item in rounds] == ["complete", "complete"]
    assert [event[0] for event in events] == sorted(event[0] for event in events)
    pending = next(current for _, _, current in events if current is not None)
    assert [item["status"] for item in pending["claims"][0]["search_rounds"]] == ["pending", "pending"]
    assert all(current["claims"][0]["verdict"] == "insufficient" for _, _, current in events
               if current is not None and not current["claims"][0]["checked"])


async def test_two_rounds_never_fetch_more_than_three_candidates_each(monkeypatch):
    queries, fetched = [], []

    async def search(query, key, sources):
        queries.append(query)
        return [f"https://example.com/news/{len(queries)}/{i}" for i in range(10)]

    async def fetch(url, sources):
        fetched.append(url)
        return page(url=url, text=QUOTE + str(len(fetched)))

    monkeypatch.setattr(fc, "_search", search)
    monkeypatch.setattr(fc, "_fetch_page", fetch)
    provider = Provider(extract(), decision())
    report = await run(provider)
    assert len(queries) == 2 and len(fetched) == 6 and len(provider.calls) == 2
    assert [item["pages_fetched"] for item in report["claims"][0]["search_rounds"]] == [3, 3]


@pytest.mark.parametrize("dimension,body,status", [
    ("subject", "2024年另一市常住人口为90万人。", "mismatch"),
    ("subject", "2024年常住人口为90万人，未说明城市。", "unknown"),
    ("event_time", "2023年该市常住人口为90万人。", "mismatch"),
    ("event_time", "该市常住人口为90万人，未说明统计年度。", "unknown"),
    ("scope_unit", "2024年该市户籍人口为90万人。", "mismatch"),
    ("scope_unit", "2024年该市常住人口为90人。", "mismatch"),
    ("scope_unit", "2024年该市人口为90万，未说明统计范围。", "unknown"),
])
@pytest.mark.parametrize("stance,verdict", [("supports", "supported"), ("refutes", "refuted")])
def test_incomparable_evidence_is_context_not_a_strong_verdict(dimension, body, status, stance, verdict):
    assessment = checks(**{dimension: status})
    assessment[dimension]["reason"] = "正文与陈述的对应维度不一致或无法确定。"
    ref = {"id": "c1-e1", "quote": body, "stance": stance, "checks": assessment}
    result, invalid = fc._judge_result(decision(verdict, [ref], suggestion="不能采用"), [page(text=body)])
    assert invalid and result["verdict"] == "insufficient" and result["suggestion"] is None
    assert result["evidence"][0]["stance"] == "context"
    assert result["evidence"][0]["checks"][dimension]["status"] == status


@pytest.mark.parametrize("patch", [
    {}, {"checks": None}, {"checks": {}}, {"checks": {"subject": checks()["subject"]}},
    {"checks": checks(subject="not_applicable")}, {"checks": checks(event_time="probably")},
    {"checks": checks(scope_unit=True)}, {"checks": checks() | {"extra": "ignored?"}},
    {"checks": checks() | {"event_time": {"status": "match", "reason": " "}}},
    {"checks": checks() | {"scope_unit": {"status": "match", "reason": 1}}},
    {"checks": checks() | {"subject": {"status": "match", "reason": "主体一致", "url": "https://forged.example"}}},
])
def test_missing_or_malformed_checks_are_strictly_rejected_and_downgraded(patch):
    ref = {"id": "c1-e1", "quote": QUOTE, "stance": "supports", **patch}
    with pytest.raises(ValueError):
        fc.FactJudgmentEvidence.model_validate(ref)
    result, invalid = fc._judge_result(decision(evidence=[ref]), [page()])
    assert invalid and result["verdict"] == "insufficient"
    evidence = result["evidence"][0]
    assert evidence["quote"] == QUOTE and evidence["stance"] == "context"
    assert {item["status"] for item in evidence["checks"].values()} == {"unknown"}


async def test_new_engine_never_accepts_legacy_judgment_without_checks(monkeypatch):
    mock_evidence(monkeypatch)
    old = decision()
    del old["evidence"][0]["checks"]
    provider = Provider(extract(), old)
    report = await run(provider)
    assert report["coverage"]["status"] == "partial"
    assert report["claims"][0]["verdict"] == "insufficient"
    assert report["claims"][0]["evidence"][0]["stance"] == "context"
    assert len(provider.calls) == 2


def test_numeric_value_difference_is_refutation_not_scope_mismatch():
    contrary = "2024年该市常住人口为90万人。"
    ref = {"id": "c1-e1", "quote": contrary, "stance": "refutes", "checks": checks()}
    result, invalid = fc._judge_result(decision("refuted", [ref], suggestion="改为90万人"), [page(text=contrary)])
    assert not invalid and result["verdict"] == "refuted" and result["suggestion"] == "改为90万人"
    assert result["evidence"][0]["checks"]["scope_unit"]["status"] == "match"


def test_not_applicable_and_context_unknown_are_explicit_not_implicit():
    body = "三角形是有三条边的多边形。"
    assessment = {"subject": {"status": "match", "reason": "同为三角形定义"},
                  "event_time": {"status": "not_applicable", "reason": "定义不涉及事件时间"},
                  "scope_unit": {"status": "not_applicable", "reason": "定义不涉及统计口径与计量单位"}}
    ref = {"id": "c1-e1", "quote": body, "stance": "supports", "checks": assessment}
    # This tests enum gating only; the program does not independently adjudicate semantics.
    result, invalid = fc._judge_result(decision(evidence=[ref]), [page(text=body)])
    assert not invalid and result["verdict"] == "supported"
    ref.update(quote=QUOTE, stance="context", checks=checks(event_time="unknown"))
    result, invalid = fc._judge_result(decision("insufficient", [ref]), [page()])
    assert not invalid and result["evidence"][0]["checks"]["event_time"]["status"] == "unknown"


@pytest.mark.parametrize("start", [0, 150, fc.MAX_PAGE_TEXT - len(QUOTE)])
def test_body_hash_and_bounded_context_share_model_visible_unicode_range(start):
    text = "𠮷" * start + QUOTE + "后" * 150 + QUOTE + "尾" * fc.MAX_PAGE_TEXT
    fetched = page(text=text)
    visible = text[:fc.MAX_PAGE_TEXT]
    result, invalid = fc._judge_result(decision(), [fetched])
    assert not invalid
    evidence = result["evidence"][0]
    assert evidence["body_sha256"] == hashlib.sha256(visible.encode("utf-8")).hexdigest()
    assert evidence["body_sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert evidence["body_hash_scope"] == "normalized_model_visible_text_utf8"
    assert evidence["body_text_length"] == len(visible)
    assert evidence["quote_start"] == start and evidence["quote_end"] == start + len(QUOTE)
    assert visible[evidence["quote_start"]:evidence["quote_end"]] == QUOTE
    assert evidence["context_before"] == visible[max(0, start - 120):start]
    assert evidence["context_after"] == visible[start + len(QUOTE):start + len(QUOTE) + 120]
    assert len(evidence["context_before"]) <= 120 and len(evidence["context_after"]) <= 120


@pytest.mark.parametrize("failure", ["search", "fetch"])
async def test_counter_round_failure_never_reports_supported_or_complete(monkeypatch, failure):
    queries, events = [], []

    async def search(query, key, sources):
        queries.append(query)
        if len(queries) == 2 and failure == "search":
            raise fc._SearchFailure("SEARCH_UNAVAILABLE", "failed")
        return [f"https://example.com/news/{len(queries)}"]

    async def fetch(url, sources):
        if len(queries) == 2:
            raise fc._FetchError("PAGE_FETCH_FAILED", "failed")
        return page(url=url)

    async def progress(percent, message, current=None):
        events.append((percent, message, current))

    monkeypatch.setattr(fc, "_search", search)
    monkeypatch.setattr(fc, "_fetch_page", fetch)
    provider = Provider(extract(), decision())
    report = await run(provider, on_progress=progress)
    assert len(queries) == len(provider.calls) == 2
    claim = report["claims"][0]
    assert claim["verdict"] == "insufficient" and claim["suggestion"] is None
    assert report["coverage"]["status"] == "partial"
    assert claim["search_rounds"][0]["status"] == "complete"
    assert claim["search_rounds"][1]["status"] == ("failed" if failure == "search" else "partial")
    assert claim["search_rounds"][1]["error_codes"]
    assert "部分完成" in events[-1][1]
    assert any("反证/更正检索" in message and ("失败" in message or "未完整完成" in message)
               for _, message, _ in events)
    assert [item[0] for item in events] == sorted(item[0] for item in events)


async def test_native_counter_failure_emits_failed_snapshot_and_never_judges(monkeypatch):
    calls, events = [], []

    async def search(query, provider, sources):
        calls.append(query)
        if len(calls) == 2:
            raise fc.NativeSearchError("SEARCH_INCOMPLETE", "反证搜索未完成")
        return SimpleNamespace(urls=["https://example.com/news/report"], usage={}, search_queries=1)

    async def fetch(url, sources):
        return page()

    async def progress(percent, message, current=None):
        events.append((percent, message, current))

    monkeypatch.setattr(fc, "search_model", search)
    monkeypatch.setattr(fc, "_fetch_page", fetch)
    provider = Provider(extract())
    with pytest.raises(fc.FactCheckError) as exc:
        await run(provider, search_provider="model", on_progress=progress)
    assert exc.value.code == "SEARCH_INCOMPLETE"
    assert len(calls) == 2 and len(provider.calls) == 1
    assert events[-1][0] < 100 and "失败" in events[-1][1]
    claim = events[-1][2]["claims"][0]
    assert claim["search_rounds"][1]["status"] == "failed"
    assert not claim["checked"] and claim["verdict"] == "insufficient"


def native_provider(slug="volcengine", *responses):
    provider = Provider(*responses)
    provider.provider_slug = slug
    provider.api_base = ("https://ark.cn-beijing.volces.com/api/v3" if slug == "volcengine"
                         else "https://dashscope.aliyuncs.com/compatible-mode/v1")
    provider.api_key = "model-secret"
    provider.model = "doubao-seed-2-0-lite-260428" if slug == "volcengine" else "qwen-plus"
    return provider


def native_result(slug="volcengine", urls=None):
    urls = ["https://example.com/news/report"] if urls is None else urls
    if slug == "qwen":
        return {"output": {"choices": [{"finish_reason": "stop", "message": {"content": "不作为证据"}}],
                           "search_info": {"search_results": [{"url": url, "title": "忽略"} for url in urls]}},
                "usage": {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10}}
    return {"status": "completed", "output": [
        {"type": "web_search_call", "status": "completed", "action": {"type": "search", "queries": [TEXT]}},
        {"type": "message", "content": [{"type": "output_text", "text": "不作为证据：https://invented.invalid/",
          "annotations": [{"type": "url_citation", "url": url, "title": "虚构标题", "summary": "恶意摘要"} for url in urls]}]},
    ], "usage": {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10, "tool_usage": {"web_search": 2}}}


@pytest.mark.parametrize("slug", ["volcengine", "qwen"])
async def test_native_search_reuses_model_key_and_only_structured_sources(slug, mock_http):
    from app.services import fact_check_search as native

    options, requests = mock_http(lambda request: response(body=json.dumps(native_result(slug)).encode()))
    provider = native_provider(slug)
    result = await native.search_model(TEXT, provider, SOURCES)
    assert result.urls == ["https://example.com/news/report"]
    assert result.usage == {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}
    assert result.search_queries == (2 if slug == "volcengine" else 1)
    assert len(requests) == 1 and requests[0].headers["authorization"] == "Bearer model-secret"
    payload = json.loads(requests[0].content)
    assert payload["model"] == provider.model
    if slug == "volcengine":
        assert str(requests[0].url) == "https://ark.cn-beijing.volces.com/api/v3/responses"
        assert payload["tools"] == [{"type": "web_search", "sources": ["search_engine"], "max_keyword": 1, "limit": 3}]
        assert payload["max_tool_calls"] == 1 and payload["tool_choice"] == "required"
        assert payload["store"] is False and payload["thinking"] == {"type": "disabled"}
        messages = payload["input"]
    else:
        assert str(requests[0].url) == "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
        assert payload["parameters"]["enable_search"] is True
        assert payload["parameters"]["search_options"] == {"forced_search": True, "enable_source": True, "enable_citation": False}
        messages = payload["input"]["messages"]
    assert json.loads(messages[1]["content"])["preferred_sources"] == [{"domain": "example.com", "path_prefix": "/news"}]
    assert "不可信" in messages[0]["content"]
    assert options[0]["verify"] and not options[0]["trust_env"] and not options[0]["follow_redirects"]
    assert not provider.closed and not provider.calls


@pytest.mark.parametrize("slug", ["volcengine", "qwen"])
async def test_native_default_engine_still_fetches_and_validates_body(slug, mock_http, public_dns):
    def handler(request):
        if request.method == "POST":
            return response(body=json.dumps(native_result(slug)).encode())
        return response(body=f"<title>真实公报</title><p>{QUOTE}</p>".encode())

    _, requests = mock_http(handler)
    provider = native_provider(slug, extract(), decision())
    report = await fc.run_fact_check(TEXT, mode="trusted", sources=SOURCES, provider=provider)
    assert report["claims"][0]["verdict"] == "supported"
    assert report["claims"][0]["evidence"][0]["title"] == "真实公报"
    assert report["usage"]["total_tokens"] == 50
    assert report["usage"]["search_queries"] == (4 if slug == "volcengine" else 2)
    assert len(provider.calls) == 2 and len(requests) == 3
    assert "恶意摘要" not in provider.calls[1]["messages"][1]["content"]
    assert requests[1].url.host == "93.184.216.34" and not provider.closed
    assert not any(request.url.host == "api.tavily.com" for request in requests)


@pytest.mark.parametrize("status,code,expected", [
    (404, "ToolNotOpen", "SEARCH_TOOL_NOT_OPEN"), (401, "InvalidApiKey", "SEARCH_AUTH_ERROR"),
    (403, "AccessDenied", "SEARCH_AUTH_ERROR"), (429, "Throttling", "SEARCH_QUOTA_ERROR"),
    (402, "Arrearage", "SEARCH_QUOTA_ERROR"), (400, "InvalidParameter", "SEARCH_NOT_SUPPORTED"),
    (404, "NotFound", "SEARCH_NOT_SUPPORTED"), (500, "InternalError", "SEARCH_PROVIDER_ERROR"),
    (200, "ToolNotOpen", "SEARCH_TOOL_NOT_OPEN"),
])
async def test_native_provider_errors_are_sanitized_without_tavily_fallback(status, code, expected, mock_http):
    _, requests = mock_http(lambda request: response(status, json.dumps({
        "error": {"code": code, "message": "model-secret request headers and original text"},
    }).encode()))
    with pytest.raises(fc.FactCheckError) as exc:
        await run(native_provider("volcengine", extract()), search_provider="model", api_key="tavily-secret")
    assert exc.value.code == expected and "model-secret" not in str(exc.value)
    assert len(requests) == 1 and requests[0].url.host == "ark.cn-beijing.volces.com"


@pytest.mark.parametrize("slug", ["volcengine", "qwen"])
async def test_native_model_answers_without_sources_are_never_used(slug, mock_http):
    data = native_result(slug, [])
    mock_http(lambda request: response(body=json.dumps(data).encode()))
    report = await run(native_provider(slug, extract()), search_provider="model", api_key="")
    assert report["claims"][0]["verdict"] == "insufficient" and not report["claims"][0]["evidence"]
    assert report["usage"]["pages_fetched"] == 0


@pytest.mark.parametrize("change,expected", [
    ({"status": "incomplete"}, "SEARCH_INCOMPLETE"), ({"status": "failed"}, "SEARCH_INCOMPLETE"),
    ({"output": []}, "SEARCH_NOT_EXECUTED"), ({"output": [None]}, "SEARCH_FORMAT_ERROR"),
    ({"output": [{"type": "web_search_call", "status": "failed"}]}, "SEARCH_NOT_EXECUTED"),
])
async def test_native_search_must_actually_complete(change, expected, mock_http):
    from app.services import fact_check_search as native

    data = native_result() | change
    mock_http(lambda request: response(body=json.dumps(data).encode()))
    with pytest.raises(native.NativeSearchError) as exc:
        await native.search_model(TEXT, native_provider(), None)
    assert exc.value.code == expected


@pytest.mark.parametrize("slug,base", [
    ("openai", "https://ark.cn-beijing.volces.com/api/v3"),
    ("volcengine", "http://ark.cn-beijing.volces.com/api/v3"),
    ("volcengine", "https://ark.cn-beijing.volces.com.evil.com/api/v3"),
    ("volcengine", "https://user@ark.cn-beijing.volces.com/api/v3"),
    ("volcengine", "https://ark.cn-beijing.volces.com/api/v3?redirect=1"),
    ("qwen", "https://custom-proxy.example.com/v1"),
])
async def test_native_rejects_unknown_endpoints_before_sending_key(slug, base):
    from app.services import fact_check_search as native

    provider = native_provider(slug)
    provider.api_base = base
    with pytest.raises(native.NativeSearchError) as exc:
        await native.search_model(TEXT, provider, None)
    assert exc.value.code == "SEARCH_NOT_SUPPORTED"


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "https://example.com/private", "https://evil.com/news/a"])
async def test_native_citations_still_obey_ssrf_and_trusted_paths(url, mock_http):
    _, requests = mock_http(lambda request: response(body=json.dumps(native_result(urls=[url])).encode()))
    report = await run(native_provider("volcengine", extract()), mode="trusted", search_provider="model")
    assert all(request.method == "POST" for request in requests)
    assert report["coverage"]["status"] == "partial" and report["claims"][0]["verdict"] == "insufficient"
    assert not report["claims"][0]["evidence"]


@pytest.mark.parametrize("status,body,headers,expected", [
    (200, b"not-json", {}, "SEARCH_FORMAT_ERROR"),
    (200, b"[]", {}, "SEARCH_FORMAT_ERROR"),
    (200, b"x", {"content-length": "1048577"}, "SEARCH_FORMAT_ERROR"),
    (200, b"x", {"content-encoding": "gzip"}, "SEARCH_FORMAT_ERROR"),
    (200, b"x" * 1048577, {}, "SEARCH_FORMAT_ERROR"),
    (302, b"", {"location": "https://evil.com"}, "SEARCH_PROVIDER_ERROR"),
    (401, b"not-json", {}, "SEARCH_AUTH_ERROR"),
])
async def test_native_response_limits_and_redirects(status, body, headers, expected, mock_http):
    from app.services import fact_check_search as native

    _, requests = mock_http(lambda request: response(status, body, headers or None))
    with pytest.raises(native.NativeSearchError) as exc:
        await native.search_model(TEXT, native_provider(), None)
    assert exc.value.code == expected and len(requests) == 1


async def test_native_timeout_and_cancellation_are_bounded(monkeypatch, mock_http):
    from app.services import fact_check_search as native

    class HangingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"never"

    mock_http(lambda request: httpx.Response(200, stream=HangingStream()))
    monkeypatch.setattr(native, "SEARCH_TIMEOUT", 0.01)
    with pytest.raises(native.NativeSearchError) as exc:
        await native.search_model(TEXT, native_provider(), None)
    assert exc.value.code == "SEARCH_UNAVAILABLE"

    def cancelled(request):
        raise asyncio.CancelledError

    mock_http(cancelled)
    with pytest.raises(asyncio.CancelledError):
        await native.search_model(TEXT, native_provider(), None)


async def test_qwen_requires_sources_and_completed_answer(mock_http):
    from app.services import fact_check_search as native

    data = native_result("qwen")
    del data["output"]["search_info"]
    mock_http(lambda request: response(body=json.dumps(data).encode()))
    with pytest.raises(native.NativeSearchError) as exc:
        await native.search_model(TEXT, native_provider("qwen"), None)
    assert exc.value.code == "SEARCH_NOT_EXECUTED"
    data = native_result("qwen")
    data["output"]["choices"][0]["finish_reason"] = "length"
    with pytest.raises(native.NativeSearchError) as exc:
        await native.search_model(TEXT, native_provider("qwen"), None)
    assert exc.value.code == "SEARCH_INCOMPLETE"


def qwen_sse(*chunks):
    body = "".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\r\n\r\n" for chunk in chunks).encode()
    return httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"},
                          stream=Stream(body[:17], body[17:39], body[39:]))


async def test_qwen38_stream_collects_sources_and_last_cumulative_usage(mock_http):
    from app.services import fact_check_search as native

    first = native_result("qwen")
    first["output"]["choices"][0]["finish_reason"] = "null"
    final = native_result("qwen", [])
    final["usage"] = {"input_tokens": 40, "output_tokens": 60, "plugins": {"search": {"count": 3}}}
    _, requests = mock_http(lambda request: qwen_sse(first, final))
    provider = native_provider("qwen")
    provider.model = "qwen3.8-flash"
    result = await native.search_model(TEXT, provider, None)
    assert result.urls == ["https://example.com/news/report"]
    assert result.usage == {"prompt_tokens": 40, "completion_tokens": 60, "total_tokens": 100}
    assert result.search_queries == 3
    request = requests[0]
    assert request.url.path == "/api/v1/services/aigc/multimodal-generation/generation"
    assert request.headers["x-dashscope-sse"] == "enable"
    assert request.headers["accept"] == "text/event-stream"
    payload = json.loads(request.content)
    assert payload["parameters"]["incremental_output"] is True
    assert payload["parameters"]["search_options"]["search_strategy"] == "turbo"
    assert "result_format" not in payload["parameters"]
    assert json.loads(payload["input"]["messages"][1]["content"][0]["text"])["statement"] == TEXT


@pytest.mark.parametrize("kind,code", [("error", "SEARCH_QUOTA_ERROR"), ("cutoff", "SEARCH_INCOMPLETE"),
                                      ("missing", "SEARCH_NOT_EXECUTED"), ("malformed", "SEARCH_FORMAT_ERROR")])
async def test_qwen38_stream_fails_closed(kind, code, mock_http):
    from app.services import fact_check_search as native

    data = native_result("qwen")
    if kind == "error":
        chunks = [data, {"code": "Arrearage", "message": "secret raw error"}]
    elif kind == "cutoff":
        data["output"]["choices"][0]["finish_reason"] = "null"
        chunks = [data]
    elif kind == "missing":
        del data["output"]["search_info"]
        chunks = [data]
    else:
        chunks = [data, []]
    _, requests = mock_http(lambda request: qwen_sse(*chunks))
    provider = native_provider("qwen")
    provider.model = "qwen3.8-flash"
    with pytest.raises(native.NativeSearchError) as exc:
        await native.search_model(TEXT, provider, None)
    assert exc.value.code == code and "secret raw" not in str(exc.value) and len(requests) == 1


async def test_qwen38_stream_search_result_cannot_replace_page_body(mock_http, public_dns):
    def handler(request):
        if request.method == "POST":
            return qwen_sse(native_result("qwen"))
        return response(body=b"No population statistics on this page.")

    mock_http(handler)
    provider = native_provider("qwen", extract(), decision())
    provider.model = "qwen3.8-flash"
    report = await run(provider, search_provider="model", api_key="")
    assert report["claims"][0]["verdict"] == "insufficient"
    assert report["claims"][0]["evidence"] == []
