import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .chunking import ChunkSpan
from .constants import _SELF_CHECK_TEXT_LIMIT


def _filter_whitelist_issues(issues: List[Dict[str, Any]],
                              global_words: Dict[str, List[Dict]],
                              user_words: Optional[Dict[str, List[Dict]]] = None) -> List[Dict[str, Any]]:
    """
    过滤命中放行词的问题项（全局放行词 + 用户放行词）。
    匹配语义：original 包含放行词即过滤（模型报"API 接口"而放行词是"API"时同样生效，
    精确相等会漏掉这类含上下文的片段）。
    例外：original 命中用户纠错映射（用户显式要求改）时不放行——用户意图优先于放行词。
    """
    whitelist = {w["word"] for w in global_words.get("whitelist", [])}
    whitelist.update(w["word"] for w in (user_words or {}).get("whitelist", []))
    user_corrections = {w["word"] for w in (user_words or {}).get("correction", [])}
    if not whitelist or not issues:
        return issues

    kept = []
    for issue in issues:
        original = (issue.get("original") or "").strip()
        if original and original not in user_corrections:
            if any(w in original for w in whitelist):
                logger.info(f"[校对] 放行词过滤: '{original}'")
                continue
        kept.append(issue)
    return kept


def _normalize_for_match(s: str) -> str:
    """匹配用归一化：去所有空白（LLM 偶尔增删空格/换行导致逐字匹配失败）"""
    return "".join(s.split())


def _check_suggestion_effective(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    建议有效性自检（确定性后处理）：改写类建议（grammar/style）若替换后
    原错误的核心仍在——典型形态：original 的错误子串被原样保留进 suggestion
    （如「拍擦着→拍擦过」，生造词「拍擦」根本没被修掉）——说明模型只改了
    语气/时态没解决问题。这类建议直接替换会产出新的病句，降级为 warning
    并在说明中标注需人工核对；不丢弃（模型可能只是表述保守，问题本身是真的）。
    错字类（typo）不适用：其 original/suggestion 通常逐字对应，含同字属正常
    （如「帐号→账号」共享「号」）。
    """
    checked: List[Dict[str, Any]] = []
    for issue in issues:
        if issue.get("source") in ("dict_scan", "consistency", "format_rule"):
            checked.append(issue)
            continue
        original = issue.get("original") or ""
        suggestion = issue.get("suggestion") or ""
        if not original or not suggestion:
            checked.append(issue)
            continue
        # 无效建议的可靠形态：suggestion 完整包含 original（膨胀式改写——
        # 报告的问题片段被原封不动保留，只是在外围加了字，如「拍擦着→轻轻地拍擦着」），
        # 或 suggestion 与 original 完全相同。删字修复（「使我们→我们」）、
        # 正常替换（「严格执行→贯彻落实」）都不会命中。
        if original and (original in suggestion and len(suggestion) > len(original) or suggestion == original):
            issue = dict(issue)
            issue["severity"] = "warning"
            issue["explanation"] = f"（建议待改进：原文片段被原样保留，需人工核对）{(issue.get('explanation') or '')[:18]}"
            checked.append(issue)
            continue
        checked.append(issue)
    return checked


def _issue_search_span(text: str, issue: Dict[str, Any],
                       chunk_spans: Optional[List[ChunkSpan]]) -> Tuple[int, int]:
    if issue.get("source") == "self_check":
        if chunk_spans is not None and "chunk_index" in issue:
            idx = issue["chunk_index"]
            if type(idx) is int and 0 <= idx < len(chunk_spans):
                return chunk_spans[idx].start, chunk_spans[idx].end
        return 0, min(len(text), _SELF_CHECK_TEXT_LIMIT)
    if issue.get("source") in ("dict_scan", "consistency", "format_rule"):
        return 0, len(text)
    if chunk_spans is not None and "chunk_index" in issue:
        idx = issue["chunk_index"]
        if type(idx) is int and 0 <= idx < len(chunk_spans):
            return chunk_spans[idx].start, chunk_spans[idx].end
        return 0, 0  # 非法分片号绝不回退到其他分片。
    return 0, len(text)


def locate_issues(text: str, issues: List[Dict[str, Any]],
                  chunk_spans: Optional[List[ChunkSpan]] = None) -> List[Dict[str, Any]]:
    """post-verify 后逐处展开；坐标为原文 Unicode 码点，重叠窗口仅去重同一位置。"""
    # 复用格式层原有判定，裸标点不能被扩展到时间/英文中的合法同字符。
    from app.services.format_rules import _HALFWIDTH_COLON_RE, _HALFWIDTH_CORE_RE

    punctuation_spans = {m.span() for pattern in (_HALFWIDTH_CORE_RE, _HALFWIDTH_COLON_RE)
                         for m in pattern.finditer(text)}
    located, seen, expanded = [], set(), set()
    for original_issue in issues:
        issue = dict(original_issue)
        original = issue.get("original") or ""
        lo, hi = _issue_search_span(text, issue, chunk_spans)
        matches = []
        start, end = issue.pop("start", None), issue.pop("end", None)
        expansion_key = (original, issue.get("type"), issue.get("suggestion"),
                         lo, hi, issue.get("source"), issue.get("chunk_index"), start, end)
        if expansion_key in expanded:
            continue
        expanded.add(expansion_key)
        if (type(start) is int and type(end) is int and lo <= start < end <= hi
                and text[start:end] == original):
            matches = [(start, end)]
        elif original:
            pos = text.find(original, lo, hi)
            while pos != -1:
                matches.append((pos, pos + len(original)))
                pos = text.find(original, pos + 1, hi)
        if issue.get("source") == "format_rule" and re.fullmatch(r"[,!?;:]+", original):
            matches = [span for span in matches if span in punctuation_spans]
        if not matches:
            issue["severity"] = "warning"
            issue["suggestion"] = ""
            if "需人工核对" not in issue.get("explanation", ""):
                issue["explanation"] = f"原文定位失败，需人工核对：{issue.get('explanation', '')[:40]}"
            # 无坐标时按分片保留，不能把不同分片的不确定项冒认为同一处。
            key = (None, issue.get("chunk_index"), original, issue.get("type"), issue.get("suggestion"))
            if key not in seen:
                seen.add(key)
                located.append(issue)
        for start, end in matches:
            key = (start, end, issue.get("type"), issue.get("suggestion"))
            if key not in seen:
                seen.add(key)
                located.append({**issue, "start": start, "end": end})
    return located


def _common_ratio(a: str, b: str) -> float:
    """两等长字符串的字符一致率（O(n)，足够模糊定位用）"""
    if not a or len(a) != len(b):
        return 0.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same / len(a)


def verify_llm_issues(text: str, issues: List[Dict[str, Any]],
                      chunk_spans: Optional[List[ChunkSpan]] = None) -> List[Dict[str, Any]]:
    """
    校验 LLM 报的问题的 original 是否真实存在于原文：
    1. 逐字命中 → 通过
    2. 去空白后命中 → 通过（LLM 增删了空白，不影响高亮定位的主干）
    3. 未命中 → 尝试模糊定位：在原文中找与 original 最相似的片段，
       相似度够高则用原文片段替换 original（修正 LLM 的转述偏差）；
       否则该条降级——suggestion 清空、标记需人工定位（避免前端
       高亮失败呈现为"假问题"）。
    确定性扫描层（dict_scan/consistency/format_rule）的 issue 天然
    来自原文匹配，跳过校验。
    """
    verified: List[Dict[str, Any]] = []
    fuzzy_fixed = 0

    for issue in issues:
        issue = dict(issue)
        if issue.get("source") in ("dict_scan", "consistency", "format_rule"):
            verified.append(issue)
            continue
        lo, hi = _issue_search_span(text, issue, chunk_spans)
        scope_text = text[lo:hi]
        original = issue.get("original") or ""
        if not original.strip():
            continue  # 无原文的问题直接丢弃
        if original in scope_text:
            verified.append(issue)
            continue
        # 容差匹配仅用于校验；没有逐字命中时 locate_issues 不编造坐标。
        if _normalize_for_match(original) in _normalize_for_match(scope_text):
            verified.append(issue)
            continue
        # 模糊定位也只能在所属分片内进行，禁止将问题移到其他分片。
        best_frag, best_score = None, 0.0
        win = len(original)
        for i in range(0, max(len(scope_text) - win, 0) + 1):
            frag = scope_text[i: i + win]
            # 快速剪枝：首字符都不同则跳过（相似度必低）
            common = _common_ratio(original, frag)
            if common > best_score:
                best_score, best_frag = common, frag
        if best_score >= 0.6:
            issue["original"] = best_frag
            issue["explanation"] = f"{issue.get('explanation', '')}（已自动对齐原文位置）".strip("；")
            fuzzy_fixed += 1
            verified.append(issue)
        else:
            # 降级：保留问题提示但不可自动替换
            issue = {**issue, "suggestion": "", "severity": "warning"}
            issue["explanation"] = f"原文定位失败，需人工核对：{issue.get('explanation', '')[:40]}"
            verified.append(issue)

    if fuzzy_fixed or len(verified) != len(issues):
        dropped = len(issues) - len([i for i in issues if (i.get("original") or "").strip()])
        logger.info(f"[自校验] 模糊对齐 {fuzzy_fixed} 条，丢弃空原文 {dropped} 条，降级 {sum(1 for i in verified if '原文定位失败' in i.get('explanation', ''))} 条")
    return verified
