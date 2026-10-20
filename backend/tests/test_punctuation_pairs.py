"""引号/括号全文配对：长引语、跨段引语与 LLM 配对误报过滤。"""
from app.services.format_rules import check_format_rules, check_paired_punctuation
from app.services.proofread import merge_issues
from app.services.proofread.chunking import ChunkSpan, split_text_into_chunk_spans
from app.services.proofread.constants import PROOFREAD_SYSTEM_PROMPT, SELF_CHECK_PROMPT
from app.services.proofread.locate import verify_llm_issues
from app.services.punctuation_pairs import (
    filter_false_pairing_issues,
    find_unmatched_pairing,
    is_pairing_claim,
)


def _llm_issue(original: str, explanation: str = "引号不成对", suggestion: str = "x") -> dict:
    return {
        "original": original,
        "type": "punctuation",
        "suggestion": suggestion,
        "severity": "warning",
        "explanation": explanation,
        "chunk_index": 0,
    }


def test_balanced_quotes_and_brackets_are_ignored():
    assert find_unmatched_pairing("他说：“你好。”") == {}
    assert find_unmatched_pairing("他说：“外层‘内层’结束。”") == {}
    assert find_unmatched_pairing("（一）【备注】〔2024〕5号《通知》") == {}
    assert find_unmatched_pairing("（第一段\n第二段）") == {}


def test_multiline_quote_with_repeated_opening_is_one_pair():
    text = "他说：“第一段内容。\n“第二段内容。\n“第三段内容。”"
    assert find_unmatched_pairing(text) == {}
    assert check_paired_punctuation(text) == []


def test_multiline_quote_with_short_lead_in_continues_previous_quote():
    text = "他说：“第一段内容。\n主持人补充说：“第二段内容。”"
    assert find_unmatched_pairing(text) == {}


def test_real_unclosed_multiline_quote_reports_first_opening_only():
    text = "他说：“第一段内容。\n“第二段内容。\n“第三段内容。"
    assert find_unmatched_pairing(text) == {text.index("“"): "“"}


def test_unmatched_opening_quote_reports_position_and_manual_hint():
    text = "他说：“你好。"
    issues = check_paired_punctuation(text)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["original"] == "“"
    assert issue["start"] == text.index("“")
    assert issue["suggestion"] == ""
    assert issue["type"] == "punctuation"
    assert issue["source"] == "format_rule"
    assert "前引号" in issue["explanation"] and "后引号" in issue["explanation"]


def test_unmatched_closing_quote_is_reported():
    text = "你好。”"
    issues = check_paired_punctuation(text)
    assert [(issue["original"], issue["start"]) for issue in issues] == [("”", text.index("”"))]
    assert "后引号" in issues[0]["explanation"]


def test_unmatched_brackets_are_reported():
    assert [(i["original"], i["start"]) for i in check_paired_punctuation("（未闭合")] == [("（", 0)]
    assert [(i["original"], i["start"]) for i in check_paired_punctuation("未闭合）")] == [("）", 3)]


def test_apostrophes_and_halfwidth_punctuation_are_ignored():
    assert find_unmatched_pairing("It’s a (test) [ok] \"quote\"") == {}
    assert find_unmatched_pairing("角度为35°12’30\"。") == {}


def test_format_rules_include_pairing_check():
    issues = check_format_rules("他说：“你好。")
    pairing = [issue for issue in issues if issue["source"] == "format_rule" and issue["original"] == "“"]
    assert len(pairing) == 1


def test_filter_drops_false_pairing_claim_when_full_text_is_balanced():
    text = "他说：“" + "这是一段很长的引语。" * 120 + "”"
    issues = [_llm_issue("“")]
    assert filter_false_pairing_issues(text, issues) == []
    assert verify_llm_issues(text, [_llm_issue("“")]) == []


def test_filter_drops_chunk_boundary_false_positive_for_long_quote():
    text = "他说：“" + "很长的引语内容。" * 120 + "”"
    spans = split_text_into_chunk_spans(text)
    assert len(spans) > 1
    for mark in ("“", "”"):
        position = text.index(mark)
        chunk_index = next(i for i, span in enumerate(spans) if span.start <= position < span.end)
        issue = {**_llm_issue(mark), "chunk_index": chunk_index}
        assert filter_false_pairing_issues(text, [issue], spans) == []


def test_filter_drops_chunk_boundary_false_positive_for_long_bracket():
    text = "（" + "很长的说明内容。" * 120 + "）"
    spans = split_text_into_chunk_spans(text)
    issue = {**_llm_issue("（"), "chunk_index": 0}
    assert filter_false_pairing_issues(text, [issue], spans) == []


def test_filter_keeps_claim_when_quote_really_unclosed():
    text = "他说：“你好。"
    issues = [_llm_issue("“", "引号未闭合，缺少后引号")]
    assert filter_false_pairing_issues(text, issues) == issues
    assert len(verify_llm_issues(text, issues)) == 1


def test_filter_checks_the_cited_occurrence_not_any_same_char_elsewhere():
    balanced = "他说：“长引语内容。”"
    text = balanced + "\n" + "另一句未闭合：“这里没有闭合"
    spans = [
        ChunkSpan(0, len(balanced), 0),
        ChunkSpan(len(balanced) + 1, len(text), len(balanced) + 1),
    ]
    # 分片 0 里的引号已配对——即使全文别处有同字符未闭合也应丢弃
    assert filter_false_pairing_issues(text, [{**_llm_issue("“"), "chunk_index": 0}], spans) == []
    # 分片 1 指向的引号确实未闭合——保留
    issue = {**_llm_issue("“"), "chunk_index": 1}
    assert filter_false_pairing_issues(text, [issue], spans) == [issue]


def test_filter_keeps_other_quote_style_claims():
    text = "他说：“你好。”"
    issue = _llm_issue("“你好”", "引号层级错误，内层引语应使用单引号")
    assert not is_pairing_claim(issue)
    assert filter_false_pairing_issues(text, [issue]) == [issue]


def test_generic_mismatch_is_not_treated_as_pairing_claim():
    issue = _llm_issue("11010519491231002X", "身份证校验位不匹配，号码可能有误", "")
    issue["type"] = "logic"
    assert not is_pairing_claim(issue)
    assert filter_false_pairing_issues("身份证号11010519491231002X。", [issue]) == [issue]

    assert is_pairing_claim(_llm_issue("”", "括号不匹配，缺少左括号"))
    assert is_pairing_claim(_llm_issue("“", "引号未闭合"))


def test_merge_dedupes_rule_pairing_mark_inside_llm_span():
    text = "他说：“你好。"
    rule = check_paired_punctuation(text)[0]
    start = text.index("“")
    for suggestion in ("", "“你好”"):
        llm = {
            "original": "“你好",
            "type": "punctuation",
            "suggestion": suggestion,
            "severity": "warning",
            "explanation": "引号未闭合",
            "source": "llm",
            "start": start,
            "end": len(text),
        }
        merged = merge_issues([llm], [rule])
        assert len(merged) == 1
        assert merged[0]["original"] == "“你好"


def test_filter_never_touches_deterministic_issues():
    text = "他说：“你好。"
    issue = check_paired_punctuation(text)[0]
    assert not is_pairing_claim(issue)
    assert filter_false_pairing_issues(text, [issue]) == [issue]


def test_prompts_guard_against_fragment_pairing_false_positives():
    assert "引号/括号配对由规则引擎按全文核验" in PROOFREAD_SYSTEM_PROMPT
    assert "多段引语" in PROOFREAD_SYSTEM_PROMPT
    assert "片段边界处的单个引号/括号不是错误" in SELF_CHECK_PROMPT
