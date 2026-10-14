import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.consistency import check_consistency
from app.services.format_rules import check_format_rules
from app.services.proofread import orchestrator
from app.services.proofread.constants import NUMERIC_REASONING_RULES
from app.services.proofread.locate import _check_suggestion_effective, locate_issues, verify_llm_issues
from app.services.proofread.numeric_reasoning import check_numeric_reasoning, protect_numeric_suggestions
from app.services.proofread.prompts import build_system_prompt

TEXT = "梁士俊志愿服务5年，服务时长6600余小时。"


def suggestion(original="6600", replacement="660", **extra):
    return {"original": original, "suggestion": replacement, "type": "typo",
            "severity": "error", "explanation": "数字有误", **extra}


def test_volunteer_average_is_evidence_not_an_impossibility_claim():
    result = check_numeric_reasoning(TEXT)
    assert len(result) == 1
    finding = result[0]
    assert finding["severity"] == "warning" and finding["suggestion"] == ""
    assert "6600 ÷ (5 × 365)" in finding["explanation"]
    assert "3.62小时/天" in finding["explanation"]
    assert "约数或上下限" in finding["explanation"]
    assert "不据此认定数据错误" in finding["explanation"]
    assert TEXT[finding["start"]:finding["end"]] == "6600余小时"


@pytest.mark.parametrize("text", [
    "她志愿服务5年，累计服务500小时。",
    "该团队志愿服务5年，服务时长66000小时。",
    "20名志愿者志愿服务5年，累计服务66000小时。",
    "她带领服务队志愿服务5年，累计服务66000小时。",
    "她志愿服务5年，去年服务6600小时。",
    "她志愿服务5年，王强累计服务66000小时。",
    "她志愿服务3至5年，累计服务6600小时。",
    "她志愿服务2025年，累计服务6600小时。",
    "本人志愿服务1年，累计服务少于9000小时。",
    "本人志愿服务1年，累计服务不足9000小时。",
    "本人志愿服务5年以上，累计服务6600小时。",
    "项目运行5年，服务器工作66000小时。",
    "甲组10人、乙组-5人，合计5人。",
    "共有100人，其中男生60人，女生占40%。",
    "今年共有100人，其中去年60人参加，占比30%。",
    "共有100人，其中60台设备，占比30%。",
    "共有3人，其中1人参加，占比33%。",
    "共有3人，其中1人参加，占比33.3%。",
    "共有约100人，其中60人参加，占比50%。",
    "产量同比增长200%。",
    "1小时（30分钟讲解、30分钟互动）。",
    "1吨，其中100千克用于试验。",
    "1000米等于1千米。",
    "1万元（10000元）。",
    "2小时等于120分钟。",
    "约1吨等于990千克。",
    "1年等于365天。",
    "甲组100人、乙组200人，合计300人。",
    "全校100人，男生60人，女生40人，合计100人。",
    "甲组100人、乙组200人，去重后合计250人。",
    "共300人，其中甲组100人、乙组200人，合计300人。",
])
def test_normal_or_ambiguous_data_is_not_misreported(text):
    assert check_numeric_reasoning(text) == []


@pytest.mark.parametrize("text", [
    "她志愿服务五年，累计服务六千六百小时。",
    "她5年来累计志愿服务6600余小时。",
    "她志愿服务约5年，累计服务66000小时。",
    "本人志愿服务1年，累计服务约9000余小时。",
    "志愿服务1年，累计服务9000小时。",
])
def test_approximate_or_unknown_scope_only_warns(text):
    findings = check_numeric_reasoning(text)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning" and findings[0]["suggestion"] == ""


def test_personal_duration_exceeding_conservative_time_bound_is_error():
    findings = check_numeric_reasoning("她志愿服务5年，服务时长66000小时。")
    assert findings[0]["severity"] == "error"
    assert "5 × 366" in findings[0]["explanation"]
    assert "> 24小时/天" in findings[0]["explanation"]
    assert findings[0]["suggestion"] == ""


@pytest.mark.parametrize("text,formula", [
    ("重量为1吨，相当于100千克。", "1吨 = 1000千克"),
    ("1吨=100千克。", "1吨 = 1000千克"),
    ("1万元（1000元）。", "1万元 = 10000元"),
    ("2小时等于20分钟。", "2小时 = 120分钟"),
    ("共有100人，其中60人参加，占比30%。", "60 ÷ 100 × 100%"),
    ("共有100人，其中120人参加，占比120%。", "超过总量"),
    ("甲组100人、乙组200人，合计350人。", "100 + 200 = 300人"),
])
def test_explicit_arithmetic_contradiction_is_nonreplaceable(text, formula):
    findings = check_numeric_reasoning(text)
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert findings[0]["suggestion"] == ""
    assert formula in findings[0]["explanation"]


def test_large_approximate_unit_difference_only_warns():
    finding = check_numeric_reasoning("约1吨等于100千克。")[0]
    assert finding["severity"] == "warning"


def test_valid_large_amounts_are_not_errors():
    for text in ("投资10000万元。", "投资19999万元。", "投资500000万元。"):
        assert check_format_rules(text) == []
        assert check_numeric_reasoning(text) == []


def test_full_document_scanning_keeps_exact_repeated_offsets_across_chunks():
    text = "前言。" * 300 + TEXT + TEXT
    findings = check_numeric_reasoning(text)
    assert len(findings) == 2
    assert all(i["start"] > 800 for i in findings)
    assert len({i["start"] for i in findings}) == 2
    assert locate_issues(text, verify_llm_issues(text, findings)) == findings
    assert _check_suggestion_effective(findings, text) == findings
    assert len([i for i in check_consistency(text) if i["source"] == "numeric_rule"]) == 2


@pytest.mark.parametrize("original,replacement", [
    ("6600", "660"), ("6600小时", "6600分钟"), ("小时", "分钟"),
    ("万元", "元"), ("1人跑10次", "1人跑100次"),
    ("志愿服务5年，累计6600小时", "志愿服务5年，累计660小时"),
])
def test_unverified_numeric_replacements_are_manual_only(original, replacement):
    result = protect_numeric_suggestions([suggestion(original, replacement)])[0]
    assert result["suggestion"] == "" and result["severity"] == "warning"
    assert result["type"] == "logic"


@pytest.mark.parametrize("original,replacement", [
    ("她以经服务5年", "她已经服务5年"), ("1000米", "1千米"),
    ("10000万元", "1亿元"), ("1,000", "1000"), ("5 年", "5年"),
    ("1人跑10次，距离1000米", "1人跑10次，距离1千米"),
])
def test_non_numeric_and_equal_value_format_edits_survive(original, replacement):
    finding = suggestion(original, replacement)
    assert protect_numeric_suggestions([finding]) == [finding]


def test_speculation_does_not_return_as_a_fact_error():
    assert protect_numeric_suggestions([suggestion(explanation="服务时长过长，不可能")]) == []
    finding = suggestion(source="dict_scan")
    assert protect_numeric_suggestions([finding]) == [finding]


class Provider:
    config_id = 17
    timeout = 1
    default_temperature = 0.2

    def __init__(self, findings=()):
        self.findings = findings
        self.messages = []
        self.closed = False

    async def chat(self, messages, **kwargs):
        self.messages.append(messages)
        return SimpleNamespace(content=json.dumps(self.findings), usage={"total_tokens": 1})

    async def close(self):
        self.closed = True


@pytest.fixture
def prepare(monkeypatch):
    def install(findings=()):
        provider = Provider(findings)
        words = {"sensitive": [], "banned": [], "correction": [], "whitelist": []}
        monkeypatch.setattr(orchestrator, "_gather_preparation", AsyncMock(return_value=(
            (words, {"correction": [], "whitelist": []}, "通用规则"), provider,
        )))
        return provider
    return install


async def test_standard_adds_calculation_without_an_extra_model_call(prepare):
    provider = prepare()
    result = await orchestrator.proofread_text(TEXT)
    assert len(provider.messages) == 1 and provider.closed
    assert NUMERIC_REASONING_RULES in provider.messages[0][0]["content"]
    assert len(result["issues"]) == 1 and result["issues"][0]["severity"] == "warning"
    assert result["coverage"]["status"] == "complete"


async def test_quick_gets_the_same_arithmetic_without_a_model(prepare):
    provider = prepare()
    result = await orchestrator.proofread_text(TEXT, depth="quick")
    assert provider.messages == [] and provider.closed
    assert result["issues"][0]["source"] == "numeric_rule"


@pytest.mark.parametrize("cached", [False, True])
async def test_cache_and_stream_cannot_expose_an_inferred_number_change(prepare, cached):
    prepare([suggestion()])
    cache = Mock()
    cache.get_chunk.return_value = {"issues": [suggestion()], "usage": {}} if cached else None
    streamed = []
    result = await orchestrator.proofread_text(TEXT, chunk_cache=cache, on_chunk_done=lambda *args: streamed.append(args))
    assert all(not i["suggestion"] for i in result["issues"])
    assert all(not i["suggestion"] for i in streamed[0][1])
    if not cached:
        assert all(not i["suggestion"] for i in cache.set_chunk.call_args.args[2])


def test_numeric_protection_after_fuzzy_realignment():
    text = "累计服务6600小时"
    finding = suggestion("累计服务660小时", "累计服务660个小时")
    verified = verify_llm_issues(text, [finding])
    assert all(not i["suggestion"] for i in verified)


def test_all_domains_get_numeric_rules():
    for domain in ("general", "official", "legal"):
        assert NUMERIC_REASONING_RULES in build_system_prompt(domain)


def test_unverified_model_average_is_not_used_for_team_data():
    finding = suggestion("累计服务66000小时", "", type="logic", explanation="5年累计66000小时约日均36小时，远超单人合理时长")
    assert protect_numeric_suggestions([finding]) == []


def test_verified_arithmetic_wins_over_duplicate_model_warning():
    from app.services.proofread.scan import merge_issues

    text = "1吨=100千克。"
    rule = check_numeric_reasoning(text)[0]
    for original, start, end in [("100千克", 3, 8), ("1吨=100千克", 0, 8)]:
        model = suggestion(original, "", type="logic", severity="warning", source="numeric_guard", start=start, end=end)
        assert merge_issues([model], [rule]) == [rule]


def test_existing_amount_contradictions_are_manual_only():
    for text in ("贰佰万元（￥200000元）", "设备费100万元，材料费200万元，合计400万元。"):
        findings = check_consistency(text)
        assert findings and all(not i["suggestion"] for i in findings)


def test_long_non_quantity_input_and_digit_years_do_not_misparse():
    assert check_numeric_reasoning("一" * 10000 + " " * 10000) == []
    assert check_numeric_reasoning("她志愿服务二〇二五年，累计服务66000小时。") == []


def test_adjacent_sentence_service_evidence_keeps_original_offsets():
    text = "她志愿服务5年。\n累计服务6600余小时。"
    findings = check_numeric_reasoning(text)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert text[findings[0]["start"]:findings[0]["end"]] == "6600余小时"
    assert "3.62小时/天" in findings[0]["explanation"]
    assert check_numeric_reasoning("她志愿服务5年。王强累计服务6600余小时。") == []


def test_potentially_overlapping_counts_do_not_prove_an_error():
    findings = check_numeric_reasoning("青年100人、工程师200人，合计250人。")
    assert len(findings) == 1 and findings[0]["severity"] == "warning"
    assert "互不重叠" in findings[0]["explanation"]
