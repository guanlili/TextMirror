"""半角标点规则 + merge_issues 包含去重。

半角标点只抓「中文语句中」的无歧义形态（宁可漏报不可误报）：
- 冒号/分号：前邻汉字即报（后随任意字符——「时间:14:30」「如下:\n」都是错）
- 逗号/叹号/问号：还要求后随汉字/空白/结尾（后随拉丁/数字可能身处英文语境）
- 数字间冒号（比分3:2）、千分位（3,500）、小数点均不命中
"""
from app.services.format_rules import check_halfwidth_punct
from app.services.proofread import merge_issues


def _hits(text: str) -> list:
    return [(i["original"], i["suggestion"]) for i in check_halfwidth_punct(text)]


def test_cjk_context_halfwidth():
    assert _hits("本次培训共有三个环节:签到、听课、考核。") == [(":", "：")]
    assert _hits("本项目包括三个阶段:前期准备,中期实施,后期验收。") == [(":", "："), (",", "，"), (",", "，")]
    assert _hits("第一;第二") == [(";", "；")]
    assert _hits("好的, 收到。") == [(",", "，")]


def test_trailing_runs():
    assert _hits("看到这个结果，所有人都惊呆了!!!") == [("!!!", "！！！")]
    assert _hits("真的吗?") == [("?", "？")]
    assert _hits("清单如下:") == [(":", "：")]
    assert _hits("清单如下:\n一、准备") == [(":", "：")]


def test_colon_any_follower():
    # 冒号后随数字/字母也算错（时间:14:30 / 注:见附件A）
    assert _hits("时间:14:30开始") == [(":", "：")]
    assert _hits("注:见附件A") == [(":", "：")]


def test_number_and_english_context_not_flagged():
    assert _hits("比分3:2领先，下午14:30开始。") == []
    assert _hits("增长率为2.5%，利润率3.1%，均高于行业均值。") == []
    assert _hits("共1,000人参加，其中200人来自总部。") == []
    assert _hits("版本2.1发布，性能大幅提升。") == []
    assert _hits("该站配置了SF6断路器与GIS组合电器，满足N-1供电可靠性要求。") == []


def test_issue_shape_matches_contract():
    issues = check_halfwidth_punct("三个阶段:准备")
    assert len(issues) == 1
    issue = issues[0]
    assert issue["type"] == "punctuation"
    assert issue["source"] == "format_rule"
    assert issue["suggestion"] == "："  # 可直接替换（original 即标点本身）
    assert "全角" in issue["explanation"]


def _llm_issue(original, issue_type="punctuation"):
    return {"original": original, "type": issue_type, "suggestion": "x",
            "severity": "warning", "explanation": ""}


def _scan_issue(original, issue_type="punctuation"):
    return {"original": original, "type": issue_type, "suggestion": "y",
            "severity": "warning", "explanation": "", "source": "format_rule"}


def test_merge_exact_dedup():
    merged = merge_issues([_llm_issue("帐号", "typo")], [_scan_issue("帐号", "typo")])
    assert len(merged) == 1


def test_merge_containment_dedup():
    # LLM 带上下文引用、规则层报裸标点——同一问题只保留 LLM 版本
    merged = merge_issues([_llm_issue("三个环节:")], [_scan_issue(":")])
    assert len(merged) == 1
    assert merged[0]["original"] == "三个环节:"


def test_merge_containment_requires_same_type():
    # 包含但类型不同（LLM 报语法问题、规则报同片段标点）——视为不同问题各自保留
    merged = merge_issues([_llm_issue("三个环节:", "grammar")], [_scan_issue(":")])
    assert len(merged) == 2


def test_merge_distinct_scans_kept():
    scanned = [_scan_issue(":"), _scan_issue(",")]
    merged = merge_issues([_llm_issue("三个环节:")], scanned)
    assert [i["original"] for i in merged] == ["三个环节:", ","]


def test_merge_no_llm_issues_all_scans_kept():
    scanned = [_scan_issue(":"), _scan_issue(",")]
    merged = merge_issues([], scanned)
    assert len(merged) == 2
