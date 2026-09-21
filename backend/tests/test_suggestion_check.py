"""建议安全性与精确分隔符边界回归。"""
import pytest

from app.services.proofread import _check_suggestion_effective


def _run(original, suggestion, issue_type="grammar", severity="error"):
    issue = {"original": original, "suggestion": suggestion, "type": issue_type,
             "severity": severity, "explanation": "搭配不当"}
    return _check_suggestion_effective([issue], issue["original"])[0]


def test_identical_suggestion_downgraded():
    r = _run("再接再励", "再接再励")
    assert r["severity"] == "warning"
    assert "需人工核对" in r["explanation"]


def test_inflated_suggestion_downgraded():
    r = _run("他拍擦着", "轻轻地他拍擦着")
    assert r["severity"] == "warning"


def test_valid_deletion_fix_kept():
    # 删「使」修复主语残缺——合法建议不降级
    r = _run("通过这次活动，使我们开阔了眼界。", "通过这次活动，我们开阔了眼界。")
    assert r["severity"] == "error"


def test_valid_replacement_kept():
    r = _run("严格执行", "贯彻落实", issue_type="style", severity="warning")
    assert r["severity"] == "warning"  # 未被改写（explanation 无标记）
    assert "需人工核对" not in r["explanation"]


def test_single_char_change_kept():
    # 「着→过」式单字调整：无法确定性判定好坏，信任 LLM 不降级
    r = _run("他拍擦着我的肩膀", "他拍擦过我的肩膀")
    assert r["severity"] == "error"
    assert "需人工核对" not in r["explanation"]


def test_typo_identical_also_caught():
    r = _run("完膳", "完膳", issue_type="typo")
    assert r["severity"] == "warning"


def test_deterministic_sources_skipped():
    # 确定性层（词库/规则）的建议不经过此检查
    issue = {"original": "帐号", "suggestion": "账号", "type": "typo", "severity": "error",
             "explanation": "x", "source": "dict_scan"}
    r = _check_suggestion_effective([issue], issue["original"])[0]
    assert r["severity"] == "error"


def test_original_not_included_kept():
    # original 不在 suggestion 中且不同——正常替换
    r = _run("权力和义务", "权利和义务", issue_type="typo")
    assert r["severity"] == "error"


def test_noop_suggestion_cannot_be_applied():
    assert _run("完膳", "完膳", issue_type="typo")["suggestion"] == ""


def test_withheld_noop_is_not_scored_as_correct_deletion():
    from eval.article_metrics import score_article

    text = "通过这次活动，使我们开阔了眼界。"
    start = text.index("使")
    issue = {"original": "使", "suggestion": "使", "type": "grammar", "severity": "error",
             "explanation": "主语残缺", "start": start, "end": start + 1}
    sample = {"id": "withheld", "text": text, "domain": "general", "gold_status": "candidate",
              "complete_gold": False, "targets": [{"start": start, "end": start + 1, "original": "使",
              "category": "grammar", "expectation": "report", "accepted_suggestions": [""]}]}
    score = score_article(sample, {"issues": _check_suggestion_effective([issue], text),
                                   "coverage": {"status": "complete", "failed_chunks": []}})
    assert score["suggestions"] == {"pass": 0, "fail": 0, "not_evaluated": 1}


def test_added_words_are_not_assumed_to_be_ineffective():
    result = _run("不得", "不得不")
    assert result["suggestion"] == "不得不"
    assert result["severity"] == "warning"


def test_sensitive_noop_is_not_converted_to_deletion():
    result = _run("示例", "示例", issue_type="sensitive")
    assert result["suggestion"] == "示例"
    assert result["severity"] == "warning"


def test_explicit_no_change_notice_is_not_an_issue():
    issue = {"original": "黄瓜", "suggestion": "黄瓜", "type": "typo",
             "severity": "error", "explanation": "原文写法正确，无需修改"}
    assert _check_suggestion_effective([issue], "黄瓜") == []


@pytest.mark.parametrize("issue_type", ["grammar", "style", "punctuation"])
def test_adjacent_separator_is_not_inserted_twice(issue_type):
    text = "课程包括舞蹈表演的、学术分享和器乐体验。"
    original = "包括舞蹈表演的"
    start = text.index(original)
    issue = {"original": original, "suggestion": "包括舞蹈表演、", "type": issue_type,
             "severity": "error", "explanation": "删除多余助词", "start": start, "end": start + len(original)}
    result = _check_suggestion_effective([issue], text)[0]
    assert text[:start] + result["suggestion"] + text[issue["end"]:] == "课程包括舞蹈表演、学术分享和器乐体验。"
    assert issue["suggestion"] == "包括舞蹈表演、"


def test_separator_repair_requires_valid_coordinates():
    issue = {"original": "表演的", "suggestion": "表演、", "type": "grammar", "severity": "error",
             "explanation": "删除多余助词", "start": 1, "end": 4}
    assert _check_suggestion_effective([issue], "表演的、交流。")[0]["suggestion"] == "表演、"


def test_separator_already_inside_original_is_preserved():
    issue = {"original": "表演的、", "suggestion": "表演、", "type": "grammar", "severity": "error",
             "explanation": "删除多余助词", "start": 0, "end": 4}
    assert _check_suggestion_effective([issue], "表演的、、交流。")[0]["suggestion"] == "表演、"


def test_ellipsis_is_not_trimmed():
    issue = {"original": "沉思的", "suggestion": "沉思…", "type": "grammar", "severity": "error",
             "explanation": "删除多余助词", "start": 0, "end": 3}
    assert _check_suggestion_effective([issue], "沉思的…")[0]["suggestion"] == "沉思…"
