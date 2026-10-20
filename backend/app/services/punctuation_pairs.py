"""引号/括号配对校验（确定性、全文级）。

LLM 在分片内审校，长引语或跨分片配对会被截断成「不成对」，产生误报。
本模块按全文做确定性配对：跨分片不影响；多段引语按规范
（每段段首前引号、仅末段段末后引号）视为同一对引号。
不用半角引号/括号：它们在英文、代码、尺寸标注中歧义太大。
"""
import re
from typing import Any, Dict, List, Tuple

from loguru import logger

_OPEN_TO_CLOSE = {
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
    "（": "）",
    "【": "】",
    "〔": "〕",
    "《": "》",
    "〈": "〉",
}
_CLOSE_TO_OPEN = {close: open_ for open_, close in _OPEN_TO_CLOSE.items()}
_QUOTE_OPENERS = frozenset("“‘「『")
PAIRING_CHARS = frozenset(_OPEN_TO_CLOSE) | frozenset(_CLOSE_TO_OPEN)

# 多段引语延续判定：前引号所在行、前引号之前允许的最长引导文字。
# 覆盖「他说：“」「- “」「1. “」等段落引导，避免把每段重复的前引号
# 当成新增嵌套层级。
_MAX_PARAGRAPH_LEAD = 20

_PAIR_LABELS = {
    "“": ("前引号", "后引号"),
    "‘": ("前引号", "后引号"),
    "「": ("前引号", "后引号"),
    "『": ("前引号", "后引号"),
    "（": ("左括号", "右括号"),
    "【": ("左括号", "右括号"),
    "〔": ("左括号", "右括号"),
    "《": ("前书名号", "后书名号"),
    "〈": ("前书名号", "后书名号"),
}

# LLM 对配对问题的常见措辞。只匹配「缺失/不成对」语义，避免误伤
# 引号层级、中英文混用等真正需要保留的标点问题。
# 「不匹配」必须和引号/括号同句出现，否则会误伤「身份证校验位不匹配」等普通表述。
_PAIRING_KEYWORD_RE = re.compile(r"引号|括号|括弧|书名号|双引号|单引号|圆括号|方括号|尖括号")
_PAIRING_CLAIM_RE = re.compile(
    r"不成对|不配对|未配对|未成对|未闭合|没闭合|未封闭|配对(?:错误|有误|不当)|"
    r"成对(?:出现|使用|缺失)|"
    r"(?:引号|双引号|单引号|括号|圆括号|方括号|尖括号|括弧|书名号)"
    r"(?:不匹配|不对应|不完整|缺失|缺少|遗漏|多余|奇数)|"
    r"(?:引号|括号|括弧|书名号|双引号|单引号|圆括号|方括号|尖括号).{0,8}"
    r"(?:配对|成对|缺失|缺少|遗漏|多余|未补|补全|不完整|奇数|不对应)|"
    r"(?:缺|少|多|漏|补).{0,4}(?:引号|括号|括弧|书名号)|"
    r"unmatched|unclosed|unbalanced|missing\s+(?:closing|opening)",
    re.IGNORECASE,
)

_DETERMINISTIC_SOURCES = frozenset({
    "dict_scan", "consistency", "format_rule",
    "person_consistency", "numeric_rule", "numeric_guard", "entity_guard",
})


def _line_prefix(text: str, index: int) -> str:
    return text[text.rfind("\n", 0, index) + 1:index]


def _looks_like_apostrophe(text: str, index: int) -> bool:
    """英文撇号（it’s、35°12’）不是中文单引号，无配对时忽略。"""
    prev_ch = text[index - 1] if index > 0 else ""
    next_ch = text[index + 1] if index + 1 < len(text) else ""
    return (prev_ch.isascii() and prev_ch.isalnum()) or (next_ch.isascii() and next_ch.isalnum())


def find_unmatched_pairing(text: str) -> Dict[int, str]:
    """返回无法配对的引号/括号位置 → 字符。位置为 Unicode 码点。

    多段引语：若上一段仍有未闭合的同族引号，且新段前引号前只有很短
    的引导文字（不是正文嵌套），视为该引语的延续而不是新的开引号。
    括号没有这种跨段约定，始终按严格配对处理。
    """
    if not text:
        return {}

    stacks: Dict[str, List[Tuple[int, int]]] = {opener: [] for opener in _OPEN_TO_CLOSE}
    unmatched: Dict[int, str] = {}
    paragraph = 0

    for index, char in enumerate(text):
        if char == "\n":
            paragraph += 1
            continue

        opener = _CLOSE_TO_OPEN.get(char)
        if opener is not None:
            stack = stacks[opener]
            if stack:
                stack.pop()
            elif char != "’" or not _looks_like_apostrophe(text, index):
                unmatched[index] = char
            continue

        stack = stacks.get(char)
        if stack is None:
            continue
        if char in _QUOTE_OPENERS and stack and stack[-1][1] < paragraph:
            prefix = _line_prefix(text, index)
            if len(prefix) <= _MAX_PARAGRAPH_LEAD and not any(c in PAIRING_CHARS for c in prefix):
                continue
        stack.append((index, paragraph))

    for opener, stack in stacks.items():
        for index, _ in stack:
            unmatched[index] = opener
    return unmatched


def is_pairing_closer(char: str) -> bool:
    return char in _CLOSE_TO_OPEN


def pairing_labels(char: str) -> Tuple[str, str]:
    """返回该配对字符所属家族的 (前半部分名称, 后半部分名称)。"""
    return _PAIR_LABELS.get(_CLOSE_TO_OPEN.get(char, char), ("前半部分", "后半部分"))


def is_pairing_claim(issue: Dict[str, Any]) -> bool:
    """LLM 是否在声称引号/括号不成对（而非其他标点问题）。

    没有点明引号/括号的措辞（如笼统的「不匹配」）只有在原文确实
    含有配对字符时才按配对问题处理。
    """
    if issue.get("source") in _DETERMINISTIC_SOURCES:
        return False
    explanation = issue.get("explanation") or ""
    if not _PAIRING_CLAIM_RE.search(explanation):
        return False
    if _PAIRING_KEYWORD_RE.search(explanation):
        return True
    return bool(set(issue.get("original") or "") & PAIRING_CHARS)


def _issue_occurrences(text: str, issue: Dict[str, Any], chunk_spans) -> List[Tuple[int, int]]:
    """定位 LLM 所报原文的出现区间：优先精确坐标，其次所属分片。"""
    original = issue.get("original") or ""
    if not original:
        return []
    start, end = issue.get("start"), issue.get("end")
    if (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
            and text[start:end] == original):
        return [(start, end)]
    lo, hi = 0, len(text)
    index = issue.get("chunk_index")
    if chunk_spans is not None and type(index) is int and 0 <= index < len(chunk_spans):
        lo, hi = chunk_spans[index].start, chunk_spans[index].end
    occurrences = []
    position = text.find(original, lo, hi)
    while position != -1:
        occurrences.append((position, position + len(original)))
        position = text.find(original, position + 1, hi)
    return occurrences


def _contains_unmatched(start: int, end: int, unmatched: Dict[int, str]) -> bool:
    return any(position in unmatched for position in range(start, end))


def filter_false_pairing_issues(text: str, issues: List[Dict[str, Any]],
                                chunk_spans=None) -> List[Dict[str, Any]]:
    """丢弃「所报位置在全文里实际配对成功」的 LLM 配对误报。

    只处理 explanation 明确声称配对缺失、且能在原文定位的条目。
    同一原文在所属分片内出现多次时，任一处未配对即保留（保守）。
    """
    if not any(is_pairing_claim(issue) for issue in issues):
        return issues

    unmatched = find_unmatched_pairing(text)
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for issue in issues:
        if not is_pairing_claim(issue):
            kept.append(issue)
            continue
        occurrences = _issue_occurrences(text, issue, chunk_spans)
        if occurrences and not any(_contains_unmatched(s, e, unmatched) for s, e in occurrences):
            dropped += 1
            continue
        kept.append(issue)
    if dropped:
        logger.info(f"[配对校验] 过滤指定位置已配对的误报 {dropped} 条")
    return kept
