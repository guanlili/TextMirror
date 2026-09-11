"""建议有效性自检：改写类建议没修掉问题的确定性降级。

出手条件（保守，只抓确定性无效形态）：
1. suggestion == original（没改任何东西）
2. original 完整包含在更长的 suggestion 里（膨胀式改写——问题片段原样保留只加外围字）
其余（删字修复/正常替换/单字调整）信任 LLM，不误伤。
"""
from app.services.proofread import _check_suggestion_effective


def _run(original, suggestion, issue_type="grammar", severity="error"):
    issue = {"original": original, "suggestion": suggestion, "type": issue_type,
             "severity": severity, "explanation": "搭配不当"}
    return _check_suggestion_effective([issue])[0]


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
    r = _check_suggestion_effective([issue])[0]
    assert r["severity"] == "error"


def test_original_not_included_kept():
    # original 不在 suggestion 中且不同——正常替换
    r = _run("权力和义务", "权利和义务", issue_type="typo")
    assert r["severity"] == "error"
