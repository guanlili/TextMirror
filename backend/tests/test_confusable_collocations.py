"""易混词搭配规则：权力/权利等高混淆对，仅搭配无歧义时报告。

核心约束（宁可漏报不可误报）：正确的权力/权利用法绝不能被报——
「权力机关」「行使权力」是正确表述，规则只匹配无歧义的错误搭配。
"""
from app.services.format_rules import check_confusable_collocations


def _hits(text: str) -> list:
    return [(i["original"], i["suggestion"]) for i in check_confusable_collocations(text)]


def test_catches_wrong_collocations():
    assert _hits("公民依法享有权力和义务。") == [("权力和义务", "权利和义务")]
    assert _hits("宪法保障公民的基本权力。") == [("基本权力", "基本权利")]
    assert _hits("扩大基层民主权力。") == [("民主权力", "民主权利")]
    assert _hits("当事人享有权力。") == [("享有权力", "享有权利")]
    assert _hits("全国人大是国家权利机关。") == [("权利机关", "权力机关")]


def test_correct_usages_not_flagged():
    # 正确的「权力」用法
    assert _hits("人民代表大会是国家权力机关。") == []
    assert _hits("行政机关应当依法行使权力。") == []
    assert _hits("一切权力属于人民。") == []
    assert _hits("规范权力运行，把权力关进制度的笼子。") == []
    # 正确的「权利」用法
    assert _hits("公民享有广泛的权利和义务。") == []
    assert _hits("基本权利受宪法保护。") == []
    assert _hits("依法保障当事人权利。") == []
    # 两个词同时正确出现
    assert _hits("规范权力运行，保障公民权利。") == []


def test_multiple_occurrences_all_reported():
    hits = _hits("基本权力之一是保障基本权力。")
    assert hits == [("基本权力", "基本权利"), ("基本权力", "基本权利")]


def test_issue_shape_matches_contract():
    issues = check_confusable_collocations("公民的基本权力")
    assert len(issues) == 1
    issue = issues[0]
    # suggestion 必须可替换（敏感词类教训：说明文字不能进 suggestion）
    assert issue["suggestion"] == "基本权利"
    assert issue["type"] == "typo"
    assert issue["source"] == "format_rule"
    assert "权利" in issue["explanation"]
