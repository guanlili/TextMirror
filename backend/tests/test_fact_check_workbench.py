import hashlib
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update
from test_fact_check_api import BASE, TEXT, empty_report
from test_fact_check_api import actors as actors
from test_fact_check_engine import TEXT as ENGINE_TEXT
from test_fact_check_engine import Provider, decision, extract, page
from test_fact_check_engine import run as engine_run

from app.core.database import async_session_factory
from app.models.fact_check import FactCheckConfig, FactCheckReview, FactCheckRun
from app.models.llm_config import LLMConfig
from app.models.proofread import ProofreadRecord
from app.models.uploaded_document import UploadedDocument
from app.schemas.fact_check import FactCheckReport, FactEvidence
from app.services import fact_check as fc
from app.services import proofread
from app.services.fact_check_report import render_report
from app.tasks.fact_check_task import async_fact_check


def independent(**changes):
    return {"text": TEXT, "allow_external_search": True, "request_id": str(uuid.uuid4()), **changes}


async def create(client, **changes):
    response = await client.post(f"{BASE}/runs", json=independent(**changes))
    assert response.status_code == 202, response.text
    return response.json()


def prepared(text=TEXT):
    return {**empty_report(), "claims": [{"id": "c1", "original": text, "start": 0, "end": len(text),
            "statement": text, "verdict": "insufficient", "reason": "尚未核查", "checked": False, "evidence": [], "suggestion": None}],
            "coverage": {"extracted": 1, "checked": 0, "unverified": 1, "status": "partial", "reason": ""}}


async def save_state(run_id, **values):
    async with async_session_factory() as db:
        await db.execute(update(FactCheckRun).where(FactCheckRun.id == run_id).values(**values))
        await db.commit()


async def test_standalone_run_does_not_create_proofread_and_has_owner_source(client, actors):
    async with async_session_factory() as db:
        before = await db.scalar(select(func.count()).select_from(ProofreadRecord))
    result = await create(client, title="独立材料")
    assert result["record_id"] is None and result["source_kind"] == "text"
    source = await client.get(f"{BASE}/runs/{result['id']}/source")
    assert source.json() == {"text": TEXT, "source_hash": hashlib.sha256(TEXT.encode()).hexdigest()}
    history = (await client.get(f"{BASE}/history", params={"q": "独立材料"})).json()
    assert history["total"] == 1 and history["items"][0]["result"] is None
    async with async_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ProofreadRecord)) == before
    actors.current = actors.stranger
    for suffix in ("", "/source", "/reviews", "/export"):
        assert (await client.get(f"{BASE}/runs/{result['id']}{suffix}")).status_code == 404
    assert (await client.get(f"{BASE}/history")).status_code == 403


@pytest.mark.parametrize("changes", [{"record_id": 1}, {"file_id": "file"}, {"text": "  "}, {"text": "字" * 20001}])
async def test_independent_input_rejects_ambiguous_or_invalid_material(client, actors, changes):
    assert (await client.post(f"{BASE}/runs", json=independent(**changes))).status_code == 422
    actors.dispatch.assert_not_called()


async def test_document_import_checks_owner_and_uses_saved_text_without_proofread(client, actors):
    async with async_session_factory() as db:
        doc = UploadedDocument(file_id=str(uuid.uuid4()), filename="核查材料.txt", file_ext=".txt", file_path="unused",
                               file_size=10, text_length=len(TEXT), extracted_text=TEXT, user_id=actors.owner.id,
                               username="owner", owner_kind="user", status="uploaded")
        db.add(doc)
        await db.commit()
    request = {"file_id": doc.file_id, "allow_external_search": True, "request_id": str(uuid.uuid4())}
    actors.current = actors.stranger
    assert (await client.post(f"{BASE}/runs", json=request)).status_code == 404
    actors.current = actors.owner
    response = await client.post(f"{BASE}/runs", json=request)
    assert response.status_code == 202
    assert response.json()["source_kind"] == "document" and response.json()["record_id"] is None


async def test_confirmation_worker_releases_then_executes_exact_selected_statement(client, actors, monkeypatch):
    provider = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(proofread, "get_llm_provider", AsyncMock(return_value=provider))
    first_report = prepared()
    engine = AsyncMock(return_value=first_report)
    monkeypatch.setattr(fc, "run_fact_check", engine)
    result = await create(client, confirm_claims=True)
    async_fact_check.run(result["id"])
    waiting = (await client.get(f"{BASE}/runs/{result['id']}")).json()
    assert waiting["status"] == "WAITING_CONFIRMATION" and waiting["finished_at"] is None
    assert engine.call_args.kwargs["extraction_only"] is True
    request = {"claims": [{"id": "c1", "statement": "示例事件发生于2020年"}], "request_id": str(uuid.uuid4())}
    executed = await client.post(f"{BASE}/runs/{result['id']}/execute", json=request)
    assert executed.status_code == 202, executed.text
    assert executed.json()["result"]["claims"][0]["original_statement"] == TEXT
    assert executed.json()["result"]["claims"][0]["statement"] == "示例事件发生于2020年"
    assert (await client.post(f"{BASE}/runs/{result['id']}/execute", json=request)).status_code == 202
    assert actors.dispatch.call_count == 2
    assert (await client.post(f"{BASE}/runs/{result['id']}/execute", json={**request, "claims": [{"id": "c1", "statement": "改变请求"}]})).status_code == 409
    engine.return_value = empty_report()
    async_fact_check.run(result["id"])
    assert engine.call_args.kwargs["extraction_only"] is False
    assert engine.call_args.kwargs["selected_claim_ids"] == ["c1"]
    assert engine.call_args.kwargs["prepared_report"]["claims"][0]["original"] == TEXT


@pytest.mark.parametrize("selections", [[{"id": "absent", "statement": "a"}], [{"id": "c1", "statement": "a"}] * 2])
async def test_confirmation_rejects_unknown_and_duplicate_ids(client, actors, selections):
    result = await create(client, confirm_claims=True)
    await save_state(result["id"], status="WAITING_CONFIRMATION", result_json=prepared())
    response = await client.post(f"{BASE}/runs/{result['id']}/execute", json={"claims": selections, "request_id": str(uuid.uuid4())})
    assert response.status_code == 422


async def test_waiting_does_not_expire_or_block_another_run(client, actors):
    result = await create(client, confirm_claims=True)
    await save_state(result["id"], status="WAITING_CONFIRMATION", result_json=prepared(), created_at=datetime.now(timezone.utc) - timedelta(days=1))
    other = await create(client)
    assert other["status"] == "PENDING"
    waiting = (await client.get(f"{BASE}/runs/{result['id']}")).json()
    assert waiting["status"] == "WAITING_CONFIRMATION"
    assert (await client.post(f"{BASE}/runs/{result['id']}/execute", json={"claims": [{"id": "c1", "statement": TEXT}], "request_id": str(uuid.uuid4())})).status_code == 409


async def test_deepen_is_independent_version_and_reviews_do_not_mutate_report(client, actors):
    result = await create(client)
    report = prepared()
    report["claims"][0].update(checked=True, reason="证据不足")
    report["coverage"].update(checked=1, unverified=0, status="complete")
    await save_state(result["id"], status="SUCCESS", stage="complete", result_json=report)
    review = {"claim_id": "c1", "decision": "disagree", "note": "需要进一步核实原始公告", "request_id": str(uuid.uuid4())}
    assert (await client.post(f"{BASE}/runs/{result['id']}/reviews", json=review)).status_code == 201
    assert (await client.post(f"{BASE}/runs/{result['id']}/reviews", json=review)).status_code == 201
    assert len((await client.get(f"{BASE}/runs/{result['id']}/reviews")).json()) == 1
    assert (await client.post(f"{BASE}/runs/{result['id']}/reviews", json={**review, "note": "另一个意见"})).status_code == 409
    stored = (await client.get(f"{BASE}/runs/{result['id']}")).json()["result"]
    assert stored == FactCheckReport.model_validate(report).model_dump(mode="json")
    request = {"claim_id": "c1", "request_id": str(uuid.uuid4()), "allow_external_search": True, "supplemental_urls": []}
    child = await client.post(f"{BASE}/runs/{result['id']}/deepen", json=request)
    assert child.status_code == 202, child.text
    assert child.json()["parent_run_id"] == result["id"] and child.json()["depth"] == "deep"
    assert not child.json()["result"]["claims"][0]["checked"]
    assert (await client.post(f"{BASE}/runs/{result['id']}/deepen", json=request)).json()["id"] == child.json()["id"]


@pytest.fixture
def report_export_data():
    report = prepared()
    quote = "示例事件发生于2021年。"
    body = "公告原文：" + quote + " <script>正文快照</script>"
    start = body.index(quote)
    evidence = {"id": "e1", "title": "原始公告 <img src=x onerror=alert(1)>",
                "url": "https://example.com/news/original", "quote": quote, "stance": "refutes",
                "publisher": "发布机构", "retrieved_at": "2026-09-01T00:00:00Z", "published_at": None,
                "checks": {key: {"status": status, "reason": reason} for key, status, reason in (
                    ("subject", "match", "同一事件"), ("event_time", "match", "同一事件发生时间"),
                    ("scope_unit", "not_applicable", "不是统计陈述"))},
                "body_text": body, "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "body_hash_scope": "normalized_model_visible_text_utf8", "body_text_length": len(body),
                "quote_start": start, "quote_end": start + len(quote),
                "context_before": body[:start], "context_after": body[start + len(quote):]}

    def source(status, **changes):
        return {"url": f"https://example.com/news/{status}", "title": f"候选材料-{status}",
                "origin": "search", "status": status, "reason": f"处理说明-{status}",
                "error_code": None, "evidence_id": None, **changes}

    report["claims"][0].update(checked=True, verdict="refuted", reason="公告给出不同年份", evidence=[evidence], search_rounds=[
        {"kind": "initial", "query": "示例事件 原始公告", "status": "complete", "pages_fetched": 2, "error_codes": [],
         "sources": [source("fetched", url=evidence["url"], title=evidence["title"], evidence_id="e1"),
                     source("fetched", title="已抓取但未采纳", reason="未形成可用引文"),
                     source("duplicate", url=evidence["url"], evidence_id="e1")]},
        {"kind": "counter", "query": "事件 <script>更正</script>", "status": "failed", "pages_fetched": 0,
         "error_codes": ["FETCH_FAILED"], "sources": [
             source("failed", reason="连接超时 <script>错误</script>", error_code="FETCH_FAILED"),
             source("skipped", title="危险 <svg onload=alert(1)>", url="javascript:alert(1)\"><img src=x>",
                    origin="supplemental", reason="不安全地址 <script>说明</script>", error_code="UNSAFE_URL")]},
        {"kind": "followup", "query": "事件 后续公告", "status": "partial", "pages_fetched": 0,
         "error_codes": [], "sources": [source("pending", reason="等待抓取")]},
    ])
    report["coverage"].update(checked=1, unverified=0, status="complete")
    return {"id": 1, "title": "导出报告", "result": report, "source_text": TEXT,
            "source_hash": hashlib.sha256(TEXT.encode()).hexdigest(), "source_kind": "text", "mode": "web",
            "depth": "deep", "model": {"name": "测试模型", "model": "test"}, "limitations": "测试限制说明",
            "reviews": [{"claim_id": "c1", "created_at": "2026-09-01T00:00:00Z", "user_id": 1,
                         "decision": "disagree", "note": "人工意见 <script>复核</script>"}]}


@pytest.mark.parametrize("status,coverage,error_code,label", [
    ("SUCCESS", "partial", None, "事实提取失败"),
    ("FAILURE", "partial", "EXTRACTION_LOCATION_FAILED", "事实提取失败"),
    ("SUCCESS", "complete", None, "未识别到可核查事实"),
    ("FAILURE", "partial", "MODEL_FORMAT_ERROR", "执行失败"),
    ("CANCELLED", "partial", "EXTRACTION_LOCATION_FAILED", "已取消"),
    ("PENDING", "partial", None, "等待执行"),
    ("RUNNING", "partial", None, "核查中"),
    ("WAITING_CONFIRMATION", "partial", None, "待确认事实"),
])
def test_report_empty_state_is_honest_and_does_not_mutate(report_export_data, status, coverage, error_code, label):
    data = report_export_data
    data.update(status=status, error_code=error_code, message="原任务说明")
    data["result"]["claims"] = []
    data["result"]["coverage"].update(status=coverage, extracted=0, checked=0, unverified=0)
    before = deepcopy(data)
    html = render_report(data)
    assert f"任务状态：{label}" in html
    assert data == before
    if label == "事实提取失败":
        assert "未能获得可准确定位到原文的事实项" in html
        assert "不代表全文没有事实或事实正确" in html
    elif label == "未识别到可核查事实":
        assert "本次未进行搜索或证据核查；不代表全文事实正确" in html
    else:
        assert "原任务说明" in html
        assert "未识别到可核查事实" not in html and "事实提取失败" not in html
    if error_code:
        assert f"错误代码：{error_code}" in html


def test_report_status_text_is_escaped_even_without_result(report_export_data):
    report_export_data.update(status="FAILURE", result=None,
                              message="<script>message</script>", error_code='<img src=x onerror="alert(1)">')
    html = render_report(report_export_data)
    assert "任务状态：执行失败" in html
    assert escape(report_export_data["message"]) in html and escape(report_export_data["error_code"]) in html
    assert "<script>" not in html and "<img" not in html
    assert "未识别到可核查事实" not in html


@pytest.mark.parametrize("blocked", [True, False])
def test_report_partial_claims_distinguish_fetch_blocking(report_export_data, blocked):
    report_export_data["status"] = "SUCCESS"
    report = report_export_data["result"]
    report["coverage"]["status"] = "partial"
    report["usage"]["pages_fetched"] = 0 if blocked else 1
    html = render_report(report_export_data)
    assert f"任务状态：{'核查受阻' if blocked else '核查不完整'}" in html
    assert "事实提取失败" not in html and "未识别到可核查事实" not in html
    if blocked:
        assert "未取得可核对的正文" in html


@pytest.mark.parametrize("status,label,relation", [
    ("failed", "抓取失败", "未采纳为证据"),
    ("fetched", "已抓取", "未作为本次结论的引用依据（不代表材料无关或事实为假）"),
    ("pending", "待抓取", "尚未采纳，等待处理"),
    ("duplicate", "重复材料", "重复材料不计为独立佐证"),
    ("skipped", "已跳过", "未采纳为证据"),
])
def test_report_material_trace_without_evidence(report_export_data, status, label, relation):
    claim = report_export_data["result"]["claims"][0]
    source = next(source for round_ in claim["search_rounds"] for source in round_["sources"]
                  if source["status"] == status)
    source = {**source, "evidence_id": None}
    claim.update(evidence=[], verdict="insufficient", search_rounds=[{
        "kind": "initial", "query": "仅有候选资料", "status": "failed" if status == "failed" else "partial",
        "pages_fetched": int(status == "fetched"), "error_codes": [], "sources": [source]}])
    html = render_report(report_export_data)
    assert "仅有候选资料" in html and "初始检索" in html
    assert escape(source["title"]) in html and escape(source["url"]) in html
    assert escape(source["reason"]) in html and f"状态：{label}" in html and relation in html
    assert "暂无已采纳证据" in html and "<a " not in html
    assert "支持本事实项" not in html and "反驳本事实项" not in html
    if source["error_code"]:
        assert source["error_code"] in html


@pytest.mark.parametrize("legacy", ["missing", "none"])
def test_report_legacy_trace_is_explicitly_unrecorded(report_export_data, legacy):
    for round_ in report_export_data["result"]["claims"][0]["search_rounds"]:
        if legacy == "missing":
            round_.pop("sources")
        else:
            round_["sources"] = None
    html = render_report(report_export_data)
    assert html.count("历史报告未记录候选材料轨迹") == 3
    assert "无候选材料记录（检索已完成）" not in html
    assert "当次模型可见正文快照" in html


@pytest.mark.parametrize("status,message", [
    ("pending", "检索或抓取尚未完成"), ("searching", "检索或抓取尚未完成"), ("fetching", "检索或抓取尚未完成"),
    ("complete", "本轮无候选材料记录（检索已完成）"),
    ("partial", "检索未完整完成，不代表没有相关材料"), ("failed", "检索未完整完成，不代表没有相关材料"),
])
def test_report_empty_trace_distinguishes_pending_and_finished(report_export_data, status, message):
    claim = report_export_data["result"]["claims"][0]
    claim["search_rounds"] = [{**claim["search_rounds"][0], "sources": [], "status": status, "pages_fetched": 0}]
    html = render_report(report_export_data)
    assert message in html and "历史报告未记录候选材料轨迹" not in html


@pytest.mark.parametrize("stance,label", [("refutes", "反驳本事实项"), ("supports", "支持本事实项"), ("context", "仅作背景")])
def test_report_adopted_sources_link_to_escaped_claim_scoped_evidence(report_export_data, stance, label):
    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))

    first = report_export_data["result"]["claims"][0]
    first["id"] = "c'\"><img src=x>"
    evidence_id = "e'\"><script>alert(1)</script>"
    first["evidence"][0].update(id=evidence_id, stance=stance)
    for source in first["search_rounds"][0]["sources"]:
        if source["evidence_id"]:
            source["evidence_id"] = evidence_id
    second = deepcopy(first)
    second["id"] = "c2"
    third = deepcopy(first)
    third.update(id="c3", evidence=[], verdict="insufficient")
    report_export_data["result"]["claims"] = [first, second, third]
    html = render_report(report_export_data)
    parsed = Tags()
    parsed.feed(html)
    anchors = [attrs["href"] for tag, attrs in parsed.tags if tag == "a"]
    assert anchors == ["#claim-1-evidence-1"] * 2 + ["#claim-2-evidence-1"] * 2
    assert [attrs["id"] for tag, attrs in parsed.tags if "id" in attrs] == ["claim-1-evidence-1", "claim-2-evidence-1"]
    assert not any(tag in {"script", "img", "svg"} for tag, attrs in parsed.tags)
    assert all(not attr.startswith("on") for tag, attrs in parsed.tags for attr in attrs)
    assert escape(first["id"]) in html and escape(evidence_id) in html
    assert f"立场：{label}" in html and f"</a> · {label}" in html
    assert "关联已采纳证据" in html and "重复材料不计为独立佐证" in html
    third_section = html.split("<section>")[3]
    assert "未找到本事实项对应证据" in third_section
    assert "<a " not in third_section and label not in third_section
    for round_ in first["search_rounds"]:
        assert escape(round_["query"]) in html
        for source in round_["sources"]:
            for key in ("title", "url", "reason"):
                assert escape(source[key]) in html
    assert "来源：补充材料" in html and "来源：检索发现" in html
    assert "主体：一致" in html and "统计范围与单位：不适用" in html
    assert escape(first["evidence"][0]["body_text"]) in html
    assert "@media print" in html and "核查原文快照" in html


@pytest.mark.parametrize("trace", ["recorded", "empty", "none", "missing"])
async def test_report_exports_preserve_source_trace_and_evidence(client, actors, report_export_data, trace):
    report = report_export_data["result"]
    if trace != "recorded":
        for round_ in report["claims"][0]["search_rounds"]:
            if trace == "missing":
                round_.pop("sources")
            else:
                round_["sources"] = [] if trace == "empty" else None
    result = await create(client)
    await save_state(result["id"], status="SUCCESS", result_json=report)
    review = {"claim_id": "c1", "decision": "disagree", "note": report_export_data["reviews"][0]["note"],
              "request_id": str(uuid.uuid4())}
    assert (await client.post(f"{BASE}/runs/{result['id']}/reviews", json=review)).status_code == 201
    response = await client.get(f"{BASE}/runs/{result['id']}/export", params={"format": "json"})
    assert response.status_code == 200, response.text
    exported = response.json()
    expected = FactCheckReport.model_validate(report).model_dump(mode="json")
    assert exported["result"] == expected
    exported_claim = exported["result"]["claims"][0]
    for saved_round, original_round in zip(exported_claim["search_rounds"], report["claims"][0]["search_rounds"], strict=True):
        assert "sources" in saved_round and saved_round["sources"] == original_round.get("sources")
    assert exported["source_text"] == TEXT and exported["reviews"][0]["note"] == review["note"]
    assert exported_claim["evidence"][0]["body_text"] == report["claims"][0]["evidence"][0]["body_text"]
    response = await client.get(f"{BASE}/runs/{result['id']}/export", params={"format": "html"})
    assert response.status_code == 200, response.text
    assert "检索轮次与候选材料" in response.text and "立场：反驳本事实项" in response.text
    assert escape(review["note"]) in response.text and "<script>" not in response.text
    if trace == "recorded":
        assert "href='#claim-1-evidence-1'" in response.text
        for round_ in report["claims"][0]["search_rounds"]:
            for source in round_["sources"]:
                assert escape(source["url"]) in response.text and escape(source["reason"]) in response.text
    elif trace in ("none", "missing"):
        assert response.text.count("历史报告未记录候选材料轨迹") == 3


async def test_report_export_escapes_untrusted_text_and_clear_keeps_quota_ledger(client, actors):
    result = await create(client, text="<script>alert('x')</script>", title="<img src=x onerror=alert(1)>")
    await save_state(result["id"], status="SUCCESS", result_json=empty_report())
    response = await client.get(f"{BASE}/runs/{result['id']}/export", params={"format": "html"})
    assert response.status_code == 200 and "<script>" not in response.text and "&lt;script&gt;" in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert (await client.delete(f"{BASE}/runs/{result['id']}")).status_code == 204
    async with async_session_factory() as db:
        stored = await db.get(FactCheckRun, result["id"])
        assert stored is not None and stored.source_text == "" and stored.result_json is None
        assert await db.scalar(select(func.count()).select_from(FactCheckReview).where(FactCheckReview.run_id == result["id"])) == 0


async def test_independent_model_setting_does_not_change_global_active(client, actors):
    async with async_session_factory() as db:
        model = LLMConfig(name=uuid.uuid4().hex, provider="openai", model="separate", api_base="https://example.com/v1", api_key="test", is_enabled=True, is_active=False)
        db.add(model)
        await db.commit()
        config = await db.get(FactCheckConfig, 1)
        config.model_config_id = model.id
        await db.commit()
    result = await create(client)
    async with async_session_factory() as db:
        stored = await db.get(FactCheckRun, result["id"])
        assert stored.config_id == model.id
        assert (await db.get(LLMConfig, actors.model.id)).is_active


async def test_model_drift_fails_before_external_call(client, actors, monkeypatch):
    result = await create(client)
    async with async_session_factory() as db:
        model = await db.get(LLMConfig, actors.model.id)
        model.model = "different-after-queue"
        await db.commit()
    get_provider = AsyncMock()
    monkeypatch.setattr(proofread, "get_llm_provider", get_provider)
    async_fact_check.run(result["id"])
    stored = (await client.get(f"{BASE}/runs/{result['id']}")).json()
    assert stored["error_code"] == "MODEL_CONFIG_CHANGED"
    get_provider.assert_not_called()


async def test_engine_extract_only_never_searches_and_selection_preserves_source(monkeypatch):
    search = AsyncMock(return_value=fc.SearchResult([], {}, 1))
    monkeypatch.setattr(fc.orchestrator, "_search", search)
    report = await engine_run(Provider(extract()), extraction_only=True)
    search.assert_not_awaited()
    assert report["claims"] and not report["claims"][0]["checked"]
    report["claims"][0]["original_statement"] = ENGINE_TEXT
    report["claims"][0]["statement"] = "2024年该市常住人口为100万人。"
    checked = await engine_run(Provider(), prepared_report=report, selected_claim_ids=["c1"])
    assert checked["claims"][0]["original"] == ENGINE_TEXT and checked["claims"][0]["checked"]
    assert search.await_count == 2


async def test_deep_engine_has_three_bounded_rounds_and_snapshot(monkeypatch):
    search = AsyncMock(return_value=fc.SearchResult(["https://example.com/news/report"], {}, 1))
    fetch = AsyncMock(side_effect=lambda url, sources: page(url=url))
    monkeypatch.setattr(fc.orchestrator, "_search", search)
    monkeypatch.setattr(fc.orchestrator, "_fetch_page", fetch)
    result = await engine_run(Provider(extract(), decision()), depth="deep", supplemental_urls=["https://example.com/news/original"])
    assert search.await_count == 3 and fetch.await_count == 2
    assert [item["kind"] for item in result["claims"][0]["search_rounds"]] == ["initial", "counter", "followup"]
    evidence = result["claims"][0]["evidence"][0]
    assert evidence["body_text"] == page().text
    FactEvidence.model_validate(evidence)
    with pytest.raises(ValidationError):
        FactEvidence.model_validate({**evidence, "body_text": "篡改正文"})


async def test_prepared_claims_reject_changed_original_and_duplicate_ids(monkeypatch):
    report = prepared(ENGINE_TEXT)
    report["claims"][0]["original"] = "不存在的原文"
    with pytest.raises(fc.FactCheckError, match="原文快照"):
        await engine_run(Provider(), prepared_report=report)
    with pytest.raises(fc.FactCheckError, match="重复"):
        await engine_run(Provider(), prepared_report=prepared(ENGINE_TEXT), selected_claim_ids=["c1", "c1"])


async def test_retention_cleans_only_expired_inactive_material_and_is_idempotent(client, actors):
    from app.tasks.fact_check_task import clean_expired_fact_checks

    expired = await create(client)
    await save_state(expired["id"], status="SUCCESS", result_json=empty_report(), created_at=datetime.now(timezone.utc) - timedelta(days=91))
    waiting = await create(client)
    await save_state(waiting["id"], status="WAITING_CONFIRMATION", result_json=prepared(), created_at=datetime.now(timezone.utc) - timedelta(days=91))
    current = await create(client)
    clean_expired_fact_checks.run()
    for run_id in (expired["id"], waiting["id"]):
        async with async_session_factory() as db:
            stored = await db.get(FactCheckRun, run_id)
            assert stored.source_text == "" and stored.result_json is None and stored.error_code == "MATERIAL_EXPIRED"
    async with async_session_factory() as db:
        assert (await db.get(FactCheckRun, current["id"])).source_text == TEXT
    assert clean_expired_fact_checks.run() == 0
