"""Collaboration engine tests: mocked preparation/provider, no network or paid calls."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.schemas.collaboration import CollaborationReport, CollaborationResult
from app.services import collaboration as engine
from app.services import proofread

TEXT = "这里有错词，后面还有病句。"
TOKENS = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def proposal(original="错词", suggestion="正词", kind="typo", severity="error", **extra):
    return {"o": original, "s": suggestion, "t": kind, "sv": severity, "e": "原文用词有误", **extra}


def decision(index=0, status="confirmed", reason="原文支持该判断"):
    return {"index": index, "status": status, "reason": reason}


def role_map(report):
    return {role["id"]: role for role in report["roles"]}


class Provider:
    model = "test-model"
    config_id = 42
    default_temperature = 0.1
    timeout = 600

    def __init__(self, language=None, consistency=None, reviewer=None):
        self.responses = {role_id: [] if value is None else value for role_id, value in (
            ("language", language), ("consistency", consistency), ("reviewer", reviewer),
        )}
        self.calls = []
        self.closed = False
        self.close_count = 0
        self.active = set()
        self.completed = set()

    async def chat(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        role_id = ("reviewer" if "争议复核角色 reviewer" in prompt else
                   "language" if "最终角色范围：language" in prompt else "consistency")
        assert role_id not in [call["role_id"] for call in self.calls], "Role called more than once"
        if role_id == "reviewer":
            assert {"language", "consistency"} <= self.completed
        self.calls.append({"role_id": role_id, **deepcopy(kwargs)})
        self.active.add(role_id)
        try:
            content = self.responses[role_id]
            if callable(content):
                content = await content(kwargs)
            if isinstance(content, BaseException):
                raise content
            if isinstance(content, SimpleNamespace):
                return content
            return SimpleNamespace(content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                                   usage=dict(TOKENS), finish_reason="stop")
        finally:
            self.active.remove(role_id)
            self.completed.add(role_id)

    async def close(self):
        assert not self.active, "Provider closed before its role tasks were drained"
        self.closed = True
        self.close_count += 1


@pytest.fixture(autouse=True)
def prohibit_unrelated_calls(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("No network, normal proofreading, or self-check calls allowed")

    monkeypatch.setattr(httpx, "AsyncClient", denied)
    monkeypatch.setattr(proofread, "self_check_pass", denied)
    monkeypatch.setattr(proofread, "proofread_text", denied)


@pytest.fixture
def install(monkeypatch):
    def setup(provider, *, global_words=None, user_words=None, domain_rules="管理员要求逻辑核对"):
        prep = AsyncMock(return_value=((global_words or {}, user_words or {}, domain_rules), provider))
        monkeypatch.setattr(proofread, "_gather_preparation", prep)
        monkeypatch.setattr("app.services.proofread.orchestrator._gather_preparation", prep)
        return prep
    return setup


async def run(**kwargs):
    return await engine.run_collaboration(kwargs.pop("text", TEXT), domain=kwargs.pop("domain", "general"),
                                          config_id=kwargs.pop("config_id", 7), user_id=kwargs.pop("user_id", 9), **kwargs)


def test_standalone_worker_registers_record_foreign_keys():
    import subprocess
    import sys

    result = subprocess.run([
        sys.executable, "-c",
        "import app.tasks; from app.models.base import BaseModel; "
        "tables = {table.name for table in BaseModel.metadata.sorted_tables}; "
        "assert {'proofread_records', 'users', 'api_keys', 'uploaded_documents'} <= tables",
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_initial_report_matches_schema_and_has_no_shared_state():
    report = engine.initial_report(7)
    parsed = CollaborationReport.model_validate(report)
    assert parsed.status == "running" and parsed.config_id == 7 and parsed.model_name == ""
    assert list(role_map(report)) == ["rules", "language", "consistency", "reviewer"]
    assert all(role["status"] == "pending" for role in report["roles"])
    report["roles"][0]["usage"]["total_tokens"] = 100
    assert engine.initial_report(None, "model")["roles"][0]["usage"] == {}


async def test_parallel_roles_full_input_scopes_and_real_awaited_snapshots(install):
    entered = set()
    both_entered = asyncio.Event()

    async def detect(kwargs):
        entered.add("language" if "最终角色范围：language" in kwargs["messages"][0]["content"] else "consistency")
        if len(entered) == 2:
            both_entered.set()
        await both_entered.wait()
        return []

    provider = Provider(language=detect, consistency=detect)
    prep = install(provider)
    events = []
    in_callback = False

    async def progress(event):
        nonlocal in_callback
        assert not in_callback
        in_callback = True
        await asyncio.sleep(0)  # Deliberately yield; callback writes must remain serialized.
        CollaborationReport.model_validate(event["collaboration"])
        events.append(event)
        in_callback = False

    text = "甲" * 7998 + "尾。"
    async with asyncio.timeout(2):
        result = await run(text=text, domain="auto", on_progress=progress)
    CollaborationResult.model_validate(result)
    prep.assert_awaited_once_with(9, "general", 7)
    assert result["collaboration"]["model_name"] == provider.model
    assert result["collaboration"]["config_id"] == result["config_id"] == 42
    assert result["collaboration"]["status"] == result["coverage"]["status"] == "complete"
    assert result["coverage"]["completed_chunks"] == 1
    assert result["usage"] == {key: value * 2 for key, value in TOKENS.items()}
    assert result["issues"] == [] and len(provider.calls) == 2
    assert provider.close_count == 1
    assert [event["progress"] for event in events] == [0, 5, 15, 20, 45, 70, 100]
    assert all(role["status"] == "pending" for role in events[0]["collaboration"]["roles"])
    parallel_roles = role_map(events[3]["collaboration"])
    assert parallel_roles["rules"]["status"] == "success"
    assert parallel_roles["language"]["status"] == parallel_roles["consistency"]["status"] == "running"
    assert role_map(result["collaboration"])["reviewer"]["status"] == "skipped"
    for call in provider.calls:
        assert call["messages"][1]["content"].endswith(text)
        assert call["thinking"] is False and 0 < call["max_tokens"] <= 4096
        assert 0 < call["timeout"] <= 90
        assert "tools" not in call
        prompt = call["messages"][0]["content"]
        assert prompt.index("最终角色范围") > prompt.index("管理员要求逻辑核对")
        assert "禁止联网" in prompt and "不输出思考过程" in prompt
        scope = prompt[prompt.index("最终角色范围"):]
        if call["role_id"] == "language":
            assert "typo/grammar/punctuation/style" in scope and "不得检查逻辑" in scope
        else:
            assert "完整原文" in scope and "不使用外部知识" in scope
            assert "上下文" in scope and "不得猜测修正" in scope


async def test_review_is_bounded_decisions_only_and_disputed_findings_are_manual(install):
    provider = Provider(language=[proposal(), proposal(suggestion="另一词")],
                        reviewer=[decision(0), decision(1, "disputed", "两种建议冲突，需人工确认")])
    install(provider)
    events = []

    async def progress(event):
        events.append(event)

    result = await run(on_progress=progress)
    CollaborationResult.model_validate(result)
    report = result["collaboration"]
    assert len(provider.calls) == 3 and result["usage"] == {key: value * 3 for key, value in TOKENS.items()}
    assert report["reviewed_count"] == 2 and report["status"] == "complete"
    findings = report["findings"]
    before = next(event["collaboration"]["findings"] for event in events if event["progress"] == 75)
    for old, new in zip(before, findings, strict=True):
        assert {key: value for key, value in new.items() if not key.startswith("review_")} == {
            key: value for key, value in old.items() if not key.startswith("review_")
        }
        assert old["review_status"] == "not_reviewed"
    disputed = result["issues"][1]
    assert disputed["suggestion"] == "另一词" and disputed["review_status"] == "disputed"
    assert disputed["manual_required"] and not disputed["auto_apply"]
    assert findings[0]["found_by"] == [engine.ROLE_NAMES["language"]]
    review_call = provider.calls[-1]
    submitted = json.loads(review_call["messages"][1]["content"])
    assert [item["index"] for item in submitted["proposals"]] == [0, 1]
    assert "不得新增问题" in review_call["messages"][0]["content"]
    assert role_map(report)["reviewer"]["usage"] == TOKENS


@pytest.mark.parametrize("review", [
    "not json", {}, [decision(99)], [decision(0), decision(0)], [],
    [{**decision(), "suggestion": "篡改建议"}], [{**decision(), "original": "新增问题"}],
    [decision(status="new")], [decision(index=True)], [decision(reason="x" * 121)],
    [decision(reason="")], [decision(status=[])],
])
async def test_malformed_review_fails_safely_without_losing_findings_or_tokens(install, review):
    provider = Provider(language=[proposal(severity="warning")], reviewer=review)
    install(provider)
    result = await run()
    report = result["collaboration"]
    assert report["status"] == "partial" and result["coverage"]["status"] == "complete"
    assert role_map(report)["reviewer"]["status"] == "failed"
    assert report["reviewed_count"] == 0
    assert report["findings"][0]["review_status"] == "not_reviewed"
    assert report["findings"][0]["suggestion"] == "正词"
    assert "复核失败" in report["findings"][0]["review_note"]
    assert result["issues"][0]["manual_required"]
    assert result["usage"]["total_tokens"] == 45 and provider.closed


@pytest.mark.parametrize("bad", [RuntimeError("secret-key raw user text"), "not JSON", "{}", "[{}]"])
@pytest.mark.parametrize("failed_role", ["language", "consistency"])
async def test_one_detector_failure_preserves_other_findings_and_marks_coverage(install, bad, failed_role):
    good_role = "consistency" if failed_role == "language" else "language"
    good = proposal(kind="logic", severity="warning") if good_role == "consistency" else proposal()
    responses = {failed_role: bad, good_role: [good], "reviewer": [decision()]}
    provider = Provider(**responses)
    install(provider)
    result = await run()
    report = result["collaboration"]
    assert report["status"] == "partial" and result["total_issues"] == 1
    assert role_map(report)[failed_role]["status"] == "failed"
    assert role_map(report)[good_role]["status"] == "success"
    assert result["coverage"] == {
        "status": "partial", "total_chunks": 1, "completed_chunks": 0,
        "failed_chunks": [{"chunk_index": 0, "start": 0, "end": len(TEXT), "text": TEXT, "error_code": "MODEL_ERROR"}],
    }
    response_count = len(provider.calls) - int(isinstance(bad, Exception))
    assert result["usage"]["total_tokens"] == 15 * response_count
    assert "secret-key" not in json.dumps(report) and "raw user text" not in json.dumps(report)
    assert provider.closed


async def test_both_detector_failures_raise_with_partial_report_and_rule_findings(install):
    provider = Provider(language="bad json", consistency=RuntimeError("secret-key"))
    install(provider, global_words={"correction": [{"word": "错词", "replacement": "正词"}]})
    with pytest.raises(engine.CollaborationFailed) as exc:
        await run()
    error = exc.value
    CollaborationReport.model_validate(error.collaboration)
    assert error.collaboration["status"] == "partial"
    roles = role_map(error.collaboration)
    assert roles["language"]["status"] == roles["consistency"]["status"] == "failed"
    assert roles["reviewer"]["status"] == "skipped" and roles["rules"]["status"] == "success"
    assert error.collaboration["findings"][0]["source"] == "dict_scan"
    assert error.usage["total_tokens"] == 15 and error.coverage["completed_chunks"] == 0
    assert "secret-key" not in str(error) and len(provider.calls) == 2 and provider.closed


async def test_reviewer_provider_failure_and_truncation_are_partial(install):
    for failed in [RuntimeError("private payload"), SimpleNamespace(content="[]", usage=TOKENS, finish_reason="length")]:
        provider = Provider(language=[proposal(severity="warning")], reviewer=failed)
        install(provider)
        result = await run()
        assert result["collaboration"]["status"] == "partial"
        assert role_map(result["collaboration"])["reviewer"]["status"] == "failed"
        assert result["total_issues"] == 1 and len(provider.calls) == 3 and provider.closed
        assert result["usage"]["total_tokens"] == (30 if isinstance(failed, Exception) else 45)


async def test_truncated_detection_is_failure_even_for_valid_json_and_counts_usage(install):
    provider = Provider(language=SimpleNamespace(content="[]", usage=TOKENS, finish_reason="length"))
    install(provider)
    result = await run()
    assert role_map(result["collaboration"])["language"]["status"] == "failed"
    assert result["coverage"]["completed_chunks"] == 0 and result["usage"]["total_tokens"] == 30


async def test_unicode_offsets_duplicate_context_and_forged_metadata(install):
    text = "\U0001f600开头。错词。中段。错词。结尾。"
    second = text.rindex("错词")
    provider = Provider(language=[
        proposal(start=second, end=second + 2, source="dict_scan", found_by=["伪造来源"]),
        proposal(suggestion="上下文修正", context_before="中段。", context_after="。结尾。"),
        proposal(suggestion="不得展开到所有位置"),
        proposal(start=True, end=3),
        proposal(original="开头", start=2, end=4),  # Invalid UTF-16-style offsets are not repaired.
        proposal(original="结尾", suggestion="末尾"),
        proposal(original="并不存在", suggestion="不要编造坐标"),
    ], reviewer=[decision(0), decision(1, "disputed")])
    install(provider)
    result = await run(text=text)
    assert result["collaboration"]["status"] == "partial"  # rejected location hints are disclosed
    findings = result["collaboration"]["findings"]
    repeated = [item for item in findings if item["original"] == "错词"]
    assert len(repeated) == 2 and all(item["start"] == second for item in repeated)
    assert len(findings) == 3
    for item in findings:
        assert text[item["start"]:item["end"]] == item["original"]
        assert item["source"] == "llm" and item["found_by"] == [engine.ROLE_NAMES["language"]]


@pytest.mark.parametrize("text,hints", [
    ("aaaa", {}), ("唯一", {"start": -1, "end": 2}), ("唯一", {"start": 0}),
    ("唯一", {"context_before": []}), ("唯一", {"start": 0, "end": 999}),
])
def test_ambiguous_or_invalid_positions_never_expand(text, hints):
    original = "aa" if text == "aaaa" else text
    assert engine._position(text, {"original": original, **hints}) is None


async def test_whitelist_precedence_rule_provenance_and_exact_duplicate_merging(install, monkeypatch):
    provider = Provider(language=[proposal(), proposal(original="白名单", suggestion="改名")])
    install(provider, global_words={"whitelist": [{"word": "白名单"}, {"word": "错词"}],
                                    "correction": [{"word": "错词", "replacement": "全局映射"}]},
            user_words={"correction": [{"word": "错词", "replacement": "正词"}]})
    logs = []
    monkeypatch.setattr(proofread.logger, "info", lambda message, *args, **kwargs: logs.append(message))
    result = await run(text="错词。白名单。错词。")
    # The ambiguous model snippet is rejected; dictionary mappings are
    # unconditional and intentionally expand to every occurrence.
    assert [item["start"] for item in result["issues"]] == [0, 7]
    assert all(item["source"] == "dict_scan" for item in result["issues"])
    assert all(item["suggestion"] == "正词" for item in result["issues"])
    assert all("我的词库" in item["explanation"] for item in result["issues"])
    assert not any("白名单" in log or "错词" in log for log in logs)

    provider = Provider(language=[proposal()])
    install(provider, global_words={"correction": [{"word": "错词", "replacement": "正词"}]})
    result = await run()
    assert len(result["issues"]) == 1
    merged = result["collaboration"]["findings"][0]
    assert merged["source"] == "dict_scan" and "全局词库" in merged["explanation"]
    assert merged["found_by"] == [engine.ROLE_NAMES["rules"], engine.ROLE_NAMES["language"]]
    assert role_map(result["collaboration"])["reviewer"]["status"] == "skipped"


async def test_whitelist_applies_to_language_and_consistency_and_user_whitelist(install):
    provider = Provider(language=[proposal(original="API 接口", suggestion="接口")],
                        consistency=[proposal(original="内部约定", kind="logic")])
    install(provider, global_words={"whitelist": [{"word": "API"}]},
            user_words={"whitelist": [{"word": "内部约定"}]})
    result = await run(text="API 接口。内部约定。")
    assert not result["issues"] and len(provider.calls) == 2


async def test_existing_rule_scans_and_effective_suggestion_checks_are_used(install):
    provider = Provider(language=[proposal(original="拍擦着", suggestion="轻轻地拍擦着", kind="grammar")],
                        reviewer=[decision(0), decision(1), decision(2)])
    install(provider)
    result = await run(text="项目一100万元，项目二200万元，合计500万元。2025年2月30日拍擦着。")
    findings = result["collaboration"]["findings"]
    assert {item["source"] for item in findings} == {"consistency", "format_rule", "llm"}
    ineffective = next(item for item in result["issues"] if item["source"] == "llm")
    assert ineffective["severity"] == "warning" and "建议待改进" in ineffective["explanation"]
    assert ineffective["manual_required"]
    assert len(provider.calls) == 3


async def test_rule_punctuation_location_preserves_correct_time_colons(install):
    provider = Provider()
    install(provider)
    text = "会议时间:14:30，比分3:2。"
    result = await run(text=text)
    assert len(result["issues"]) == 1
    assert result["issues"][0]["start"] == text.index(":")
    assert result["issues"][0]["source"] == "format_rule"


async def test_repeated_invalid_dates_survive_collaboration(install):
    install(Provider(reviewer=[decision(0), decision(1)]))
    text = "初稿日期为2025年2月30日，终稿日期为2025年2月30日。"
    result = await run(text=text)
    assert result["collaboration"]["status"] == "complete"
    assert role_map(result["collaboration"])["rules"]["issue_count"] == 2
    issues = result["issues"]
    assert [issue["start"] for issue in issues] == [text.index("2025"), text.rindex("2025")]
    for issue in issues:
        assert text[issue["start"]:issue["end"]] == issue["original"] == "2025年2月30日"
        assert issue["source"] == "format_rule" and issue["manual_required"]


async def test_naming_collaboration_only_keeps_standalone_occurrences(install):
    install(Provider(reviewer=[decision(0), decision(1)]))
    full = "北京华宇信息技术有限公司"
    prefix = f"{full}负责开发。上海华宇信息技术有限公司负责验收。{full}提供支持。"
    standalone = "华宇信息技术负责运维。"
    text = prefix + standalone * 2
    result = await run(text=text)
    assert result["collaboration"]["status"] == "complete"
    issues = result["issues"]
    assert [issue["start"] for issue in issues] == [len(prefix), len(prefix) + len(standalone)]
    for issue in issues:
        assert text[issue["start"]:issue["end"]] == issue["original"] == "华宇信息技术"
        assert issue["source"] == "consistency" and issue["suggestion"] == full
        assert issue["found_by"] == [engine.ROLE_NAMES["rules"]]


@pytest.mark.parametrize("unrelated,relevant,original", [
    ("档案号23456789。", "电话：23456789。", "23456789"),
    ("设备200万元，材料300万元，合计500万元。", "设备100万元，材料200万元，合计500万元。", "合计500万元"),
    ("会议14:30，比分3:2。", "会议时间:14:30。", ":"),
])
async def test_rules_only_keep_repeated_relevant_context(install, unrelated, relevant, original):
    install(Provider(reviewer=[decision(0), decision(1)]))
    text = unrelated + relevant * 2
    scanned, rejected = engine._rule_findings(text, {}, {})
    assert rejected == 0 and len(scanned) == 2
    result = await run(text=text)
    assert result["collaboration"]["status"] == "complete"
    first = len(unrelated) + relevant.index(original)
    assert [issue["start"] for issue in result["issues"]] == [first, first + len(relevant)]
    for issue in result["issues"]:
        assert text[issue["start"]:issue["end"]] == issue["original"] == original


def test_model_snippet_with_repeated_context_still_rejected():
    text = "前文。错词。后文。前文。错词。后文。"
    content = json.dumps([proposal(context_before="前文。", context_after="。后文。")], ensure_ascii=False)
    findings, rejected = engine._detection_findings(content, text, "language", {}, {})
    assert findings == [] and rejected == 1


async def test_consistency_is_advisory_and_out_of_scope_findings_are_rejected(install):
    provider = Provider(language=[proposal(kind="logic")], consistency=[
        proposal(original="后面", suggestion="猜测事实", kind="logic", e="前文与后文口径冲突"),
        proposal(original="病句", suggestion="修语法", kind="grammar"),
    ], reviewer=[decision()])
    install(provider)
    result = await run()
    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["type"] == "logic" and issue["suggestion"] == ""
    assert issue["manual_required"] and not issue["auto_apply"] and issue["severity"] == "warning"
    assert "前文与后文" in issue["explanation"]
    assert issue["found_by"] == [engine.ROLE_NAMES["consistency"]]
    assert result["collaboration"]["status"] == "partial"


async def test_review_limit_prefers_conflicts_then_warnings_and_discloses_uncovered(install):
    originals = [f"词项{index:02d}" for index in range(23)]
    text = "。".join(originals)
    language = [proposal(original=item, suggestion="修正" + item, severity="warning") for item in originals]
    # This late overlap must precede earlier nonconflicting warnings in review.
    language.append(proposal(original=originals[-1], suggestion="另一修正", severity="warning"))

    async def review(kwargs):
        submitted = json.loads(kwargs["messages"][1]["content"])["proposals"]
        assert len(submitted) == 20
        assert [item["index"] for item in submitted[:2]] == [22, 23]
        return [decision(item["index"]) for item in submitted]

    provider = Provider(language=language, reviewer=review)
    install(provider)
    result = await run(text=text)
    report = result["collaboration"]
    assert report["reviewed_count"] == report["review_limit"] == 20
    assert report["status"] == "partial" and result["coverage"]["status"] == "complete"
    assert role_map(report)["reviewer"]["status"] == "success"
    unreviewed = [item for item in result["issues"] if item["review_status"] == "not_reviewed"]
    assert len(unreviewed) == 4 and all("预算" in item["review_note"] for item in unreviewed)
    assert all(item["manual_required"] for item in unreviewed)
    assert "未复核4项" in role_map(report)["reviewer"]["message"] and len(provider.calls) == 3


@pytest.mark.parametrize("text", ["", "  \n", "甲" * 8001, None])
async def test_input_validation_happens_before_preparation_or_progress(install, text):
    provider = Provider()
    prep = install(provider)
    progress = AsyncMock()
    with pytest.raises(ValueError):
        await run(text=text, on_progress=progress)
    prep.assert_not_awaited()
    progress.assert_not_awaited()
    assert not provider.calls


@pytest.mark.parametrize("stage", [0, 5, 15, 20, 45, 70, 75, 100])
@pytest.mark.parametrize("error_class", [RuntimeError, asyncio.CancelledError])
async def test_callback_errors_and_cancellation_propagate_at_all_phases(install, stage, error_class):
    provider = Provider(language=[proposal(severity="warning")], reviewer=[decision()])
    install(provider)
    error = error_class("worker cancelled")

    async def progress(event):
        if event["progress"] == stage:
            raise error

    with pytest.raises(error_class) as exc:
        await run(on_progress=progress)
    assert exc.value is error
    assert provider.closed == (stage != 0)
    assert not provider.active


async def test_callback_failure_cancels_and_drains_other_detector(install):
    consistency_started = asyncio.Event()
    consistency_cancelled = asyncio.Event()

    async def language(kwargs):
        await consistency_started.wait()
        return []

    async def consistency(kwargs):
        consistency_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            consistency_cancelled.set()

    provider = Provider(language=language, consistency=consistency)
    install(provider)
    error = RuntimeError("stop persistence")

    async def progress(event):
        if event["progress"] == 45:
            raise error

    async with asyncio.timeout(2):
        with pytest.raises(RuntimeError) as exc:
            await run(on_progress=progress)
    assert exc.value is error and consistency_cancelled.is_set() and provider.closed


@pytest.mark.parametrize("role_id", ["language", "consistency", "reviewer"])
async def test_provider_cancellation_propagates_and_closes(install, role_id):
    responses = {"language": [proposal(severity="warning")], "reviewer": [decision()]}
    responses[role_id] = asyncio.CancelledError()
    provider = Provider(**responses)
    install(provider)
    with pytest.raises(asyncio.CancelledError):
        await run()
    assert provider.closed and not provider.active


async def test_role_timeout_is_local_and_does_not_hang_other_detector(install, monkeypatch):
    cancelled = asyncio.Event()

    async def hanging(kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(engine, "ROLE_TIMEOUT_SECONDS", 0.01)
    provider = Provider(language=hanging, consistency=[])
    install(provider)
    async with asyncio.timeout(2):
        result = await run()
    assert cancelled.is_set() and provider.closed
    roles = role_map(result["collaboration"])
    assert roles["language"]["status"] == "failed" and roles["consistency"]["status"] == "success"
    assert result["coverage"]["completed_chunks"] == 0 and result["collaboration"]["status"] == "partial"


async def test_outer_task_cancellation_drains_parallel_roles_and_closes_provider(install):
    entered = asyncio.Event()
    starts = 0

    async def hanging(kwargs):
        nonlocal starts
        starts += 1
        if starts == 2:
            entered.set()
        await asyncio.Event().wait()

    provider = Provider(language=hanging, consistency=hanging)
    install(provider)
    task = asyncio.create_task(run())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.closed and not provider.active


async def test_callback_snapshot_mutation_does_not_mutate_engine_state(install):
    provider = Provider(language=[proposal()])
    install(provider)

    async def progress(event):
        event["collaboration"]["roles"].clear()
        event["collaboration"]["findings"].clear()

    result = await run(on_progress=progress)
    assert len(result["collaboration"]["roles"]) == 4 and len(result["issues"]) == 1
    result["issues"][0]["found_by"].clear()
    assert result["collaboration"]["findings"][0]["found_by"]


async def test_cleanup_failure_does_not_mask_callback_failure(install):
    provider = Provider()
    error = RuntimeError("worker signal")

    async def close():
        provider.closed = True
        raise OSError("close failed")

    provider.close = close
    install(provider)

    async def progress(event):
        if event["progress"] == 5:
            raise error

    with pytest.raises(RuntimeError) as exc:
        await run(on_progress=progress)
    assert exc.value is error and provider.closed
