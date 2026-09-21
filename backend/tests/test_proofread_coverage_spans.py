"""Source spans, strict parsing and offsets; fake providers only."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.document import DocumentProofreadResponse
from app.schemas.proofread import TextProofreadResponse
from app.services import model_compare as compare
from app.services import proofread as service


def issue(original="错词", suggestion="对词", **extra):
    return {"original": original, "suggestion": suggestion, "type": "typo",
            "explanation": "测试", "severity": "warning", **extra}


class FakeProvider:
    config_id = 73
    timeout = 60
    default_temperature = 0.2

    def __init__(self, responses):
        self.responses = list(responses)
        self.contexts = []
        self.closed = False

    async def chat(self, messages, **kwargs):
        self.contexts.append(messages[-1]["content"])
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(content=response, usage={"total_tokens": 3})

    async def close(self):
        self.closed = True


@pytest.fixture
def prepare(monkeypatch):
    def install(responses, corrections=()):
        provider = FakeProvider(responses)
        words = {"sensitive": [], "banned": [], "correction": list(corrections), "whitelist": []}
        monkeypatch.setattr(service, "_gather_preparation", AsyncMock(return_value=(
            (words, {"correction": [], "whitelist": []}, service.DOMAIN_PROMPTS["general"]), provider,
        )))
        monkeypatch.setattr("app.services.proofread.orchestrator._gather_preparation", AsyncMock(return_value=(
            (words, {"correction": [], "whitelist": []}, service.DOMAIN_PROMPTS["general"]), provider,
        )))
        return provider
    return install


@pytest.mark.parametrize("text", [
    "重复段落。\n\n  重复段落。\n" * 200,
    "😀甲\r\n\n\t乙。 👩‍💻\n" * 250,
    "长" * 4001,
    " \n\r\n\t" * 500,
    "",
])
def test_spans_cover_exact_source_without_reconstruction(text):
    spans = service.split_text_into_chunk_spans(text)
    assert "".join(text[s.core_start:s.end] for s in spans) == text
    assert service.split_text_into_chunks(text) == [text[s.start:s.end] for s in spans]
    previous_end = 0
    for s in spans:
        assert s.core_start == previous_end
        assert 0 <= s.end - s.core_start <= 800
        assert s.start == max(0, s.core_start - 100)
        assert len(text[s.start:s.end]) <= 900
        previous_end = s.end
    assert previous_end == len(text)


def test_nearest_boundary_and_hard_cut():
    text = "甲" * 701 + "。" + "乙" * 30 + "\n" + "丙" * 1100
    spans = service.split_text_into_chunk_spans(text)
    assert spans[0].end == 733
    assert spans[1].end - spans[1].core_start == 800
    assert spans[1].start == 633
    assert len(service.split_text_into_chunks("甲" * 1601, overlap=0)) == 3


@pytest.mark.parametrize("content", [
    "", "not json", "[", "null", "{}", '{"issues": []}', "[null]", "[1]", "[{}]",
    "prefix [] suffix", '[{"o": 1}]',
    json.dumps([issue(), {"original": "缺字段"}]),
    json.dumps([issue(type="unknown")]),
    json.dumps([issue(severity="fatal")]),
    json.dumps([issue(original=" ")]),
    json.dumps([issue(extra=float("nan"))]),
])
def test_invalid_json_or_structure_is_not_empty_success(content):
    with pytest.raises(service.InvalidProofreadResponse):
        service.parse_proofread_result(content)


def test_empty_array_and_valid_short_fields():
    assert service.parse_proofread_result(" [] \n") == []
    assert service.parse_proofread_result("```json\n[]\n```") == []
    result = service.parse_proofread_result(json.dumps([{
        "o": "错词", "t": "typo", "s": "对词", "e": "测试", "sv": "warning",
        "source": "dict_scan", "chunk_index": 500, "start": 888, "end": 900,
    }]))
    assert result == [issue()]


@pytest.mark.parametrize("failure, code", [
    (RuntimeError("provider-secret-internal-url"), "MODEL_ERROR"),
    ('{"not": "an array"}', "INVALID_RESPONSE"),
])
async def test_partial_coverage_reports_provider_context_exactly(prepare, failure, code):
    text = "😀错词" + "甲" * 797 + "乙" * 800 + "终"
    provider = prepare([json.dumps([issue()]), failure, "[]"])
    result = await service.proofread_text(text, config_id=12)
    coverage = result["coverage"]
    assert coverage == {
        "status": "partial", "total_chunks": 3, "completed_chunks": 2,
        "failed_chunks": [{"chunk_index": 1, "start": 700, "end": 1600,
                           "text": text[700:1600], "error_code": code}],
    }
    assert provider.contexts[1] == service.PROOFREAD_USER_PROMPT.format(text=text[700:1600])
    assert "provider-secret" not in json.dumps(result)
    assert result["issues"][0]["start"] == 1
    assert result["issues"][0]["end"] == 3
    assert result["config_id"] == 73 and result["depth"] == "standard"
    assert provider.closed


@pytest.mark.parametrize("response", [RuntimeError("secret-provider-error"), "malformed"])
async def test_all_failed_raises_safe_error_even_with_deterministic_hits(prepare, response):
    provider = prepare([response, response], corrections=[{"word": "错词", "replacement": "对词"}])
    with pytest.raises(service.ModelProofreadError) as caught:
        await service.proofread_text("错词" + "甲" * 900)
    assert "secret-provider-error" not in str(caught.value)
    assert caught.value.coverage["completed_chunks"] == 0
    assert len(caught.value.coverage["failed_chunks"]) == 2
    assert caught.value.config_id == 73
    assert provider.closed


async def test_empty_array_is_complete_and_quick_never_calls_chat(prepare):
    provider = prepare(["[]"])
    complete = await service.proofread_text("干净文本")
    assert complete["issues"] == []
    assert complete["coverage"]["status"] == "complete"
    assert provider.closed
    provider = prepare([], corrections=[{"word": "错词", "replacement": "对词"}])
    text = "😀错词\n" + "甲" * 850 + "错词"
    quick = await service.proofread_text(text, depth="quick")
    assert quick["coverage"]["status"] == "complete"
    assert quick["coverage"]["completed_chunks"] == quick["chunks_count"]
    assert quick["depth"] == "quick" and quick["config_id"] == 73
    assert [(i["start"], i["end"]) for i in quick["issues"]] == [(1, 3), (854, 856)]
    assert quick["usage"]["total_tokens"] == 0
    assert not provider.contexts and provider.closed


async def test_repeated_text_llm_only_expands_inside_its_chunk(prepare):
    text = "错词" + "甲" * 798 + "乙" * 110 + "错词错词" + "丙" * 686
    prepare(["[]", json.dumps([issue(), issue()])])
    result = await service.proofread_text(text)
    assert [(i["start"], i["end"]) for i in result["issues"]] == [(910, 912), (912, 914)]
    assert all(i["chunk_index"] == 1 for i in result["issues"])


async def test_overlap_same_position_dedupes_but_distinct_positions_survive(prepare):
    text = "甲" * 740 + "错词" + "乙" * 58 + "错词" + "丙" * 700
    prepare([json.dumps([issue()]), json.dumps([issue()])])
    result = await service.proofread_text(text)
    assert [(i["start"], i["end"]) for i in result["issues"]] == [(740, 742), (800, 802)]


async def test_merge_suppresses_nested_scanner_hits_fixed_at_both_occurrences(prepare):
    text = "我迫不急待地参加活动。再次迫不急待地出发。"
    prepare([json.dumps([issue("迫不急待", "迫不及待"), issue("迫不急待", "迫不及待")])],
            corrections=[{"word": "急待", "replacement": "亟待"}])
    result = await service.proofread_text(text)
    assert result["total_issues"] == 2
    assert [(i["start"], i["end"], i["suggestion"]) for i in result["issues"]] == [
        (1, 5, "迫不及待"), (13, 17, "迫不及待"),
    ]
    assert all(i.get("source") != "dict_scan" for i in result["issues"])
    for i in reversed(result["issues"]):
        text = text[:i["start"]] + i["suggestion"] + text[i["end"]:]
    assert text == "我迫不及待地参加活动。再次迫不及待地出发。"


def test_merge_nested_scanner_suppression_does_not_affect_another_occurrence():
    text = "我迫不急待地参加活动。再次迫不急待地出发。"
    llm = service.locate_issues(text, [issue("迫不急待", "迫不及待", start=1, end=5)])
    scanned = service.locate_issues(text, service.scan_words_deterministic(
        text, {"correction": [{"word": "急待", "replacement": "亟待"}]},
    ))
    merged = service.merge_issues(llm, scanned)
    assert merged == [scanned[1], llm[0]]  # scanner error precedes LLM warning
    assert (scanned[1]["start"], scanned[1]["end"]) == (15, 17)


def test_merge_same_span_dedupes_exact_suggestion_but_keeps_alternatives():
    llm = issue("急待", "亟待", start=0, end=2)
    duplicate = issue("急待", "亟待", start=0, end=2, source="dict_scan")
    alternative = issue("急待", "等待", start=0, end=2, source="dict_scan")
    assert service.merge_issues([llm], [duplicate, alternative]) == [llm, alternative]


@pytest.mark.parametrize("suggestion, issue_type", [
    ("", "typo"),
    ("迫不急待的", "typo"),  # the scanner's original remains unfixed
    ("迫不及待的", "grammar"),  # different issue types must not suppress each other
])
def test_merge_keeps_nested_scanner_when_unfixed_or_different_type(suggestion, issue_type):
    llm = issue("迫不急待地", suggestion, type=issue_type, start=0, end=5)
    scanned = issue("急待", "亟待", start=2, end=4, source="dict_scan")
    assert service.merge_issues([llm], [scanned]) == [llm, scanned]


def test_unmatched_chunk_cannot_steal_another_chunks_exact_match():
    text = "错词" + "甲" * 798 + "乙" * 800
    spans = service.split_text_into_chunk_spans(text)
    checked = service.verify_llm_issues(text, [issue(chunk_index=1)], spans)
    result = service.locate_issues(text, checked, spans)
    assert len(result) == 1
    assert "start" not in result[0] and "end" not in result[0]
    assert result[0]["severity"] == "warning" and result[0]["suggestion"] == ""
    assert "需人工核对" in result[0]["explanation"]


def test_fuzzy_post_verify_offsets_and_exact_whitespace():
    text = "😀批准事项\n甲 乙\n重复 重复"
    fixed = service.verify_llm_issues(text, [issue("批准事顶"), issue("甲 乙")])
    result = service.locate_issues(text, fixed)
    assert [(i["start"], i["end"]) for i in result] == [(1, 5), (6, 9)]
    assert all(text[i["start"]:i["end"]] == i["original"] for i in result)
    no_exact = service.locate_issues(text, service.verify_llm_issues(text, [issue("甲乙")]))
    assert "start" not in no_exact[0] and no_exact[0]["severity"] == "warning"


async def test_punctuation_positions_not_merged_or_expanded_into_legal_times(prepare):
    text = "😀阶段:准备,执行,验收。时间:14:30；英文 a,b。再次:结束。"
    prepare([json.dumps([issue("阶段:", "阶段：", type="punctuation")])])
    result = await service.proofread_text(text)
    rules = [i for i in result["issues"] if i.get("source") == "format_rule"]
    # The LLM fixes only the colon inside "阶段:"; other positions remain independent.
    assert {i["start"] for i in rules} == {6, 9, 15, 31}
    assert len(rules) == 4
    assert any(i["original"] == "阶段:" and i["suggestion"] == "阶段：" for i in result["issues"])
    assert all(text[i["start"]:i["end"]] == i["original"] for i in rules)
    assert text[18] == ":" and not any(i["start"] == 18 for i in rules)


async def test_selfcheck_invalid_response_is_best_effort_and_new_issues_search_full_text():
    provider = FakeProvider(["invalid"])
    assert await service.self_check_pass("错词", [], provider, force=True) == []
    provider = FakeProvider([json.dumps([issue(review="new")])])
    extra = await service.self_check_pass("错词\n错词", [], provider, force=True)
    assert "review" not in extra[0] and extra[0]["source"] == "self_check"
    located = service.locate_issues("错词\n错词", extra, [service.ChunkSpan(0, 2, 0)])
    assert [i["start"] for i in located] == [0, 3]


async def test_selfcheck_does_not_locate_outside_the_text_sent_to_provider():
    text = "错词" + "甲" * 2998 + "错词尾项"
    provider = FakeProvider([json.dumps([issue(review="new"), issue("尾项", review="new")])])
    extra = await service.self_check_pass(text, [], provider, force=True)
    located = service.locate_issues(text, service.verify_llm_issues(text, extra))
    assert [(i["start"], i["end"]) for i in located if "start" in i] == [(0, 2)]
    unmatched = next(i for i in located if i["original"] == "尾项")
    assert "start" not in unmatched and unmatched["suggestion"] == ""
    assert unmatched["severity"] == "warning"


async def test_deep_selfcheck_does_not_upgrade_partial_coverage(prepare):
    provider = prepare(["[]", RuntimeError("failed chunk"), json.dumps([issue(review="new")])])
    text = "甲" * 800 + "错词"
    result = await service.proofread_text(text, depth="deep")
    assert result["depth"] == "deep"
    assert result["coverage"]["status"] == "partial"
    assert result["coverage"]["completed_chunks"] == 1
    assert result["issues"][0]["start"] == 800
    assert result["issues"][0]["source"] == "self_check"
    assert len(provider.contexts) == 3 and provider.closed


async def test_provider_remembers_actual_active_config(monkeypatch):
    config = SimpleNamespace(id=91, name="active", provider="test", model="fake", api_key="x",
                             api_base="https://invalid.test", timeout=60, max_retries=0, temperature=0.3)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: config)))

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(service, "async_session_factory", factory)
    monkeypatch.setattr("app.services.proofread.provider.async_session_factory", factory)
    monkeypatch.setattr(service, "decrypt_secret", lambda value: value)
    monkeypatch.setattr("app.services.proofread.provider.decrypt_secret", lambda value: value)
    monkeypatch.setattr(service, "OpenAICompatProvider", lambda **kw: FakeProvider([]))
    monkeypatch.setattr("app.services.proofread.provider.OpenAICompatProvider", lambda **kw: FakeProvider([]))
    provider = await service.get_llm_provider()
    assert provider.config_id == 91 and provider.default_temperature == 0.2


async def test_compare_keeps_partial_metadata_and_observed_consensus(monkeypatch):
    partial = {"status": "partial", "total_chunks": 2, "completed_chunks": 1,
               "failed_chunks": [{"chunk_index": 1, "start": 1, "end": 2, "text": "词",
                                  "error_code": "MODEL_ERROR"}]}

    async def fake(text, domain, config_id, user_id):
        return {"issues": [issue(start=0, end=2)], "total_issues": 1, "config_id": config_id,
                "coverage": partial if config_id == 1 else {**partial, "status": "complete", "completed_chunks": 2, "failed_chunks": []},
                "domain": "legal", "depth": "standard", "usage": {"total_tokens": 9}, "chunks_count": 2}

    monkeypatch.setattr(compare, "proofread_text", fake)
    configs = {i: SimpleNamespace(id=i, name=f"model-{i}", model="fake") for i in (1, 2)}
    items, consensus, _ = await compare.run_proofread_compare("错词", "auto", [2, 1], configs, None)
    assert [i["config_id"] for i in items] == [2, 1]
    assert [i["complete"] for i in items] == [True, False]
    assert all(i["success"] for i in items)
    assert items[1]["coverage"] == partial
    assert items[1]["domain"] == "legal" and items[1]["usage"] == {"total_tokens": 9}
    assert consensus == ["错词"]
    merged = compare.dedupe_compare_issues([
        {"config_name": "one", "issues": [issue(start=0, end=2), issue(start=4, end=6)]},
        {"config_name": "two", "issues": [issue(start=0, end=2), issue(start=4, end=6)]},
    ])
    assert [i["start"] for i in merged] == [0, 4]
    assert all(i["found_by"] == ["one", "two"] for i in merged)


def test_document_and_text_schemas_keep_metadata_without_faking_legacy_coverage():
    result = {"issues": [issue(start=1, end=3)], "config_id": 73, "depth": "deep",
              "coverage": {"status": "complete", "total_chunks": 1, "completed_chunks": 1, "failed_chunks": []}}
    for schema in (TextProofreadResponse, DocumentProofreadResponse):
        serialized = schema(**result, file_id="x", filename="x.txt").model_dump()
        assert serialized["coverage"] == result["coverage"]
        assert serialized["config_id"] == 73 and serialized["depth"] == "deep"
        assert serialized["issues"][0]["start"] == 1
        assert schema(file_id="x", filename="x.txt").coverage is None
