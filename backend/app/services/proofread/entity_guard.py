"""专名改写保护：拦截 LLM 引入原文之外新名词的幻觉建议。

动机：人名/地名类错误多为脱字、衍字或单字形近（如「梁士俊→粱士俊」），
模型却偶发按多字替换处理，编造原文中不存在的新专名（如生产实测
「相高市苗」被建议改成「相原市」）。幻觉自校验只校验 original 定位，
此类建议在 suggestion 侧一路放行，违反「专有名词不改写」的审校准则。

确定性规则只拦截「多数字符被替换且新字符全文未出现」的建议，放行：
1. 增删字型（原文与建议互为子序列，如 高市苗→高市早苗）；
2. 单字替换的经典错字形（如 帐号→账号、梁士俊→粱士俊）；
3. 新字符在全文他处出现过（属合理改写）。
被拦截的建议降级为人工核实：suggestion 清空、severity=warning。

已知边界：1 换 1 的专名幻觉（如 高市苗→高桥市）形态与正常错字
不可区分，本规则不拦截——覆盖它会误伤单字形近修正。
"""

import re

# 专名样片段：2~4 个纯汉字（更长片段的改写建议不适用此规则）
_CJK_SPAN = re.compile(r"^[一-鿿]{2,4}$")

_GUARDRAIL_SOURCES = (
    "dict_scan", "consistency", "format_rule",
    "person_consistency", "numeric_rule", "numeric_guard", "entity_guard",
)


def _is_subsequence(shorter: str, longer: str) -> bool:
    """shorter 是否为 longer 的子序列（保序，用于识别增删字型修正）。"""
    it = iter(longer)
    return all(ch in it for ch in shorter)


def protect_entity_suggestions(text: str, issues: list[dict]) -> list[dict]:
    """将「多字替换 + 新字符全文未出现」的 LLM 建议降级为人工核实。"""
    protected = []
    for issue in issues:
        if issue.get("source") in _GUARDRAIL_SOURCES:
            protected.append(issue)
            continue
        original = (issue.get("original") or "").strip()
        suggestion = (issue.get("suggestion") or "").strip()
        if (original and suggestion
                and _CJK_SPAN.fullmatch(original) and _CJK_SPAN.fullmatch(suggestion)
                and not _is_subsequence(original, suggestion)
                and not _is_subsequence(suggestion, original)):
            new_chars = set(suggestion) - set(original)
            lost_chars = set(original) - set(suggestion)
            if (new_chars and len(lost_chars) >= 2
                    and not any(ch in text for ch in new_chars)):
                issue = {**issue, "suggestion": "", "severity": "warning", "source": "entity_guard",
                         "explanation": f"建议「{suggestion}」引入了原文中不存在的新名词，疑似幻觉；请人工核实，不提供自动替换。"}
        protected.append(issue)
    return protected

