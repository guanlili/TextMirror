import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.proofread import orchestrator
from app.services.proofread.constants import PERSON_CONSISTENCY_PROMPT, PERSON_REFERENCE_RULES
from app.services.proofread.locate import _check_suggestion_effective, locate_issues, verify_llm_issues
from app.services.proofread.person_reference import (
    check_person_consistency,
    filter_gender_inferences,
    needs_person_consistency,
    parse_person_conflicts,
)
from app.services.proofread.prompts import build_system_prompt

FIRST = "梁士俊介绍了项目，她将负责实施。"
SECOND = "梁士俊介绍了进展，他将继续负责。"


def issue(original="她", suggestion="他", **extra):
    return {"original": original, "suggestion": suggestion, "type": "typo",
            "severity": "warning", "explanation": "用字错误", **extra}


def conflict(first=FIRST, second=SECOND, name="梁士俊", **extra):
    return {"name": name, "first": first, "second": second, **extra}


class Provider:
    config_id = 17
    timeout = 1
    default_temperature = 0.2

    def __init__(self, conflicts=(), findings=(), extras=(), failure=None):
        self.conflicts = conflicts
        self.findings = findings
        self.extras = extras
        self.failure = failure
        self.messages = []
        self.closed = False

    async def chat(self, messages, **kwargs):
        self.messages.append(messages)
        if messages[0]["content"] == PERSON_CONSISTENCY_PROMPT:
            if self.failure:
                raise self.failure
            items = self.conflicts
        elif "第一轮发现的问题" in messages[-1]["content"]:
            items = self.extras
        else:
            items = self.findings
        return SimpleNamespace(content=json.dumps(items, ensure_ascii=False), usage={"total_tokens": 7})

    async def close(self):
        self.closed = True


@pytest.fixture
def prepare(monkeypatch):
    def install(**kwargs):
        provider = Provider(**kwargs)
        words = {"sensitive": [], "banned": [], "correction": [], "whitelist": []}
        monkeypatch.setattr(orchestrator, "_gather_preparation", AsyncMock(return_value=(
            (words, {"correction": [], "whitelist": []}, "通用规则"), provider,
        )))
        return provider
    return install


@pytest.mark.parametrize("original,suggestion", [
    ("她", "他"), ("他", "她"), ("她们", "他们"), ("先生", "女士"),
    ("梁士俊表示，她以经完成任务。", "梁士俊表示，他已经完成任务。"),
    ("梁士俊", "他"), ("女性", "男性"), ("她介绍，他发言", "他介绍，她发言"),
])
def test_unsupported_gender_edits_are_removed(original, suggestion):
    assert filter_gender_inferences([issue(original, suggestion)]) == []


@pytest.mark.parametrize("original,suggestion", [
    ("她以经完成任务", "她已经完成任务"), ("他拍擦着", "轻轻地他拍擦着"),
    ("其它事项", "其他事项"), ("其他部门", "其它部门"), ("她的发言。", "她的发言！"),
    ("其她", "其他"), ("她她表示", "她表示"), ("她她", "她"),
])
def test_non_gender_edits_are_preserved(original, suggestion):
    finding = issue(original, suggestion)
    assert filter_gender_inferences([finding]) == [finding]


def test_unfounded_gender_warning_is_not_reported():
    assert filter_gender_inferences([issue(suggestion="", explanation="姓名通常为男性，性别可能错误")]) == []
    assert filter_gender_inferences([issue("梁士俊", "", explanation="姓名通常为男性")]) == []
    assert filter_gender_inferences([issue("梁士俊", "梁士俊", explanation="人物性别应为男性")]) == []
    timeline = issue("她于五月完成", "", type="logic", explanation="前文为六月，时间不一致")
    assert filter_gender_inferences([timeline]) == [timeline]


def test_explicit_dictionary_mapping_is_not_overridden():
    finding = issue(source="dict_scan")
    assert filter_gender_inferences([finding]) == [finding]


@pytest.mark.parametrize("text,expected", [
    (FIRST, False), ("梁士俊提出建议，她将完善其他细节。", False),
    (FIRST + SECOND, True), ("他乡的她遇到了其他人。", False),
])
def test_consistency_call_gate(text, expected):
    assert needs_person_consistency(text) is expected


def test_conflict_is_manual_only_and_exactly_located():
    text = "😀项目报道。" + FIRST + "中间的其他内容。" * 300 + SECOND
    findings = parse_person_conflicts(json.dumps([conflict(), conflict()]), text)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["suggestion"] == ""
    assert finding["severity"] == "warning"
    assert finding["type"] == "logic"
    assert FIRST in finding["explanation"] and SECOND in finding["explanation"]
    assert text[finding["start"]:finding["end"]] == SECOND
    assert finding["start"] > 800
    assert locate_issues(text, verify_llm_issues(text, findings)) == findings
    assert _check_suggestion_effective(findings, text) == findings


def test_reversed_evidence_is_sorted_by_source_position():
    findings = parse_person_conflicts(json.dumps([conflict(SECOND, FIRST)]), FIRST + SECOND)
    assert findings[0]["original"] == SECOND
    assert findings[0]["start"] == len(FIRST)


@pytest.mark.parametrize("text,pair", [
    (FIRST + SECOND, conflict(second="梁士俊介绍了进展，她将继续负责。")),
    (FIRST + FIRST, conflict(second=FIRST)),
    (FIRST + SECOND + SECOND, conflict()),
    (FIRST + SECOND, conflict(name="李明")),
    (FIRST + "王强介绍了进展，他将继续负责。", conflict(second="王强介绍了进展，他将继续负责。")),
    (FIRST + "她陪同梁士俊，他将继续负责。", conflict(second="她陪同梁士俊，他将继续负责。")),
    (FIRST + "王强见到梁士俊，他将继续负责。", conflict(second="梁士俊，他将继续负责。")),
    (FIRST + "王强陪同梁士俊，他将继续负责。", conflict(second="王强陪同梁士俊，他将继续负责。")),
    (FIRST + "梁士俊陪同王强，他将继续负责。", conflict(second="梁士俊陪同王强，他将继续负责。")),
    (FIRST + "梁士俊介绍了进展，他们将继续负责。", conflict(second="梁士俊介绍了进展，他们将继续负责。")),
    (FIRST + "梁士俊说：“他将继续负责。”", conflict(second="梁士俊说：“他将继续负责。”")),
    (FIRST + SECOND, conflict(second="梁士俊介绍了进展，他")),
])
def test_invalid_or_ambiguous_evidence_is_discarded(text, pair):
    assert parse_person_conflicts(json.dumps([pair]), text) == []


@pytest.mark.parametrize("content", ["{}", "null", "not json", '[{"name":123}]'])
def test_invalid_fulltext_response_is_not_a_valid_empty_result(content):
    with pytest.raises(ValueError):
        parse_person_conflicts(content, FIRST + SECOND)


def test_model_cannot_turn_a_warning_into_a_replacement():
    findings = parse_person_conflicts(json.dumps([conflict(suggestion="他", source="dict_scan", start=999)]), FIRST + SECOND)
    assert findings[0]["suggestion"] == ""
    assert findings[0]["source"] == "person_consistency"
    assert findings[0]["start"] == len(FIRST)


async def test_consistent_female_reference_does_not_call_fulltext_model(prepare):
    provider = prepare(findings=[issue(), issue("以经", "已经")])
    stream = []
    text = "梁士俊表示，她以经完成任务。她将继续努力。"
    result = await orchestrator.proofread_text(text, on_chunk_done=lambda *args: stream.append(args))
    assert [i["original"] for i in result["issues"]] == ["以经"]
    assert [i["original"] for i in stream[0][1]] == ["以经"]
    assert len(provider.messages) == 1
    assert PERSON_REFERENCE_RULES in provider.messages[0][0]["content"]
    assert provider.closed


async def test_cross_chunk_conflict_uses_entire_document_and_counts_tokens(prepare):
    text = FIRST + "项目正常推进。" * 300 + SECOND
    provider = prepare(conflicts=[conflict()], findings=[issue()])
    result = await orchestrator.proofread_text(text)
    assert provider.messages[0][-1]["content"] == text
    assert len(result["issues"]) == 1
    assert result["issues"][0]["source"] == "person_consistency"
    assert result["issues"][0]["suggestion"] == ""
    assert result["issues"][0]["start"] == text.index(SECOND)
    assert result["usage"]["total_tokens"] == 7 * len(provider.messages)
    assert provider.closed


async def test_cached_inference_is_filtered_before_streaming(prepare):
    provider = prepare()
    cache = Mock()
    cache.get_chunk.return_value = {"issues": [issue()], "usage": {"total_tokens": 1}}
    stream = []
    result = await orchestrator.proofread_text(FIRST, chunk_cache=cache, on_chunk_done=lambda *args: stream.append(args))
    assert result["issues"] == []
    assert stream == [(0, [], 0)]
    assert provider.messages == []


async def test_deep_self_check_cannot_reintroduce_gender_guess(prepare):
    provider = prepare(extras=[issue(review="new")])
    result = await orchestrator.proofread_text(FIRST, depth="deep")
    assert result["issues"] == []
    assert len(provider.messages) == 2
    assert provider.messages[1][0]["role"] == "system"
    assert PERSON_REFERENCE_RULES in provider.messages[1][0]["content"]


async def test_multiple_people_without_conflict_remain_unchanged(prepare):
    provider = prepare()
    result = await orchestrator.proofread_text("梁士俊介绍项目，她负责设计。王强介绍方案，他负责施工。")
    assert result["issues"] == []
    assert len(provider.messages) == 2


async def test_fulltext_failure_keeps_edit_protection(prepare):
    provider = prepare(failure=TimeoutError(), findings=[issue()])
    result = await orchestrator.proofread_text(FIRST + SECOND)
    assert result["issues"] == []
    assert result["coverage"]["status"] == "partial"
    assert result["coverage"]["person_consistency_status"] == "failed"
    assert result["coverage"]["failed_chunks"] == []
    assert provider.closed


async def test_quick_mode_remains_model_free(prepare):
    provider = prepare()
    await orchestrator.proofread_text(FIRST + SECOND, depth="quick")
    assert provider.messages == []
    assert provider.closed


async def test_fulltext_usage_retained_on_invalid_response():
    provider = Provider()
    provider.chat = AsyncMock(return_value=SimpleNamespace(content="{}", usage={"total_tokens": 13}))
    assert await check_person_consistency(FIRST + SECOND, provider) == ([], {"total_tokens": 13}, False)


@pytest.mark.parametrize("cached", [False, True])
async def test_fuzzy_gender_edit_never_reaches_stream_or_cache(prepare, cached):
    bad = issue("他取得优异成绩", "他取得优异的成绩")
    prepare(findings=[bad])
    cache = Mock()
    cache.get_chunk.return_value = {"issues": [bad]} if cached else None
    streamed = []
    result = await orchestrator.proofread_text(
        "她取得优异成绩", chunk_cache=cache, on_chunk_done=lambda *args: streamed.append(args),
    )
    assert result["issues"] == []
    assert streamed == [(0, [], 0)]
    if not cached:
        assert cache.set_chunk.call_args.args[2] == []


def test_shared_postprocessing_protects_fuzzy_alignment():
    text = "她取得优异成绩"
    findings = [issue("他取得优异成绩", "他取得优异的成绩")]
    verified = verify_llm_issues(text, findings)
    assert verified[0]["original"] == text
    assert _check_suggestion_effective(locate_issues(text, verified), text) == []


def test_all_domains_get_person_protection():
    for domain in ("general", "legal", "official"):
        assert PERSON_REFERENCE_RULES in build_system_prompt(domain, domain_rules="管理员自定义规则")


def test_person_check_failure_roundtrips_api_and_saved_review():
    from app.schemas.proofread import ProofreadCoverage
    from app.schemas.review import ReviewCoverage

    coverage = {"status": "partial", "total_chunks": 2, "completed_chunks": 2,
                "failed_chunks": [], "person_consistency_status": "failed"}
    assert ProofreadCoverage.model_validate(coverage).model_dump() == coverage
    assert ReviewCoverage.model_validate(coverage).model_dump() == coverage
    with pytest.raises(ValueError):
        ReviewCoverage.model_validate({**coverage, "status": "complete"})
