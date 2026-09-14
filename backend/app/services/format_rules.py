"""
TextMirror 格式规则引擎（纯规则，零 LLM 成本）

LLM 对格式类问题（日期合法性、号码位数、单位冗余）判断不稳定且
建议常为不可执行的占位符（如「[请核实]」）。本模块用确定性规则覆盖
高频格式错误，命中即给出可执行 suggestion 或明确的人工核对提示。

设计原则与词库扫描/一致性检查一致：宁可漏报不可误报——每条规则
只匹配无歧义的错误形态。
"""
import re
from datetime import date
from typing import Any, Dict, List

from loguru import logger

# 日期：YYYY年M月D日
_DATE_CN_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
# 日期：YYYY.M.D / YYYY-M-D / YYYY/M/D（分隔符须统一，混合如 2025.1-3 判为格式错）
_DATE_SEP_RE = re.compile(r"(\d{4})([.\-/])(\d{1,2})\2(\d{1,2})")
# 混合分隔符日期
_DATE_MIXED_SEP_RE = re.compile(r"\d{4}[.\-/]\d{1,2}[.\-/,]\s*\d{1,2}")
# 手机号：1 开头 11 位
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
# 疑似手机号（1[3-9] 开头但不是 11 位，前后非数字）
_PHONE_BROKEN_RE = re.compile(r"(?<!\d)(1[3-9]\d{7,10}|\d{9,10}|\d{12})(?!\d)")
# 身份证 18 位（含校验位 X）
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_ID_CARD_15_RE = re.compile(r"(?<!\d)\d{15}(?!\d)")
# 金额冗余单位：数字+万元（除非数字含小数点表示万的具体值域外）
# 「10000万元」应为「1亿元」或「100000000元」；「3000万元」是正常表达（3000个万）
# 高置信度场景仅限：数字本身以万为单位量级过大（如 1万万）——实际常见错误是「X万元」X≥10000
_AMOUNT_WAN_RE = re.compile(r"(\d{4,})万元")
# 编号样式：中文（一、二、）与阿拉伯（1. 2.）在同文混用（按行首统计）
_SEQ_CN_RE = re.compile(r"^[（(]?[一二三四五六七八九十]+[)、.．]", re.MULTILINE)
_SEQ_AR_RE = re.compile(r"^[（(]?\d+[）、.．]", re.MULTILINE)

# 易混词搭配规则：(错误搭配, 正确搭配, 说明)。
# 单看「权力/权利」无法判定对错（权力机关/权利义务都成立），但特定搭配下只有
# 一种正确写法——LLM（尤其 lite 档）对此类高混淆对偶发漏检，规则层兜底。
# 收录标准：错误搭配在规范文本中几乎不出现，命中即高置信（宁可漏报不可误报）。
CONFUSABLE_COLLOCATIONS = [
    ("权力和义务", "权利和义务", "「权利和义务」为固定法律搭配，此处应为「权利」（权利=法定利益，权力=政治力量）"),
    ("基本权力", "基本权利", "宪法与法律术语为「基本权利」"),
    ("民主权力", "民主权利", "规范表述为「民主权利」"),
    ("享有权力", "享有权利", "「享有」搭配的是权利；政治力量语境用「行使权力」"),
    ("权利机关", "权力机关", "「权力机关」指国家权力机关（如人大），非「权利」"),
]

# 半角标点（中文语句应全角）。冒号/分号只看前邻汉字（后随任何字符都算错，
# 「时间:14:30」「清单如下:\n」均报）；逗号/叹号/问号还要求后随汉字/空白/结尾——
# 后随拉丁字母或数字时可能身处英文/数字语境（你好,world、3,500），宁漏报。
# 数字间的冒号（比分3:2）因前邻非汉字天然不命中。mini 对此类漏检稳定
# （punct-4 英文冒号多轮全静默），规则层补位。
_HALFWIDTH_CORE_RE = re.compile(r"(?<=[\u4e00-\u9fa5])([,!?]+)(?=[\u4e00-\u9fa5\s]|$)")
_HALFWIDTH_COLON_RE = re.compile(r"(?<=[\u4e00-\u9fa5])[;:]")
_HALFWIDTH_MAP = str.maketrans({",": "，", "!": "！", "?": "？", ";": "；", ":": "："})


def _issue(original: str, suggestion: str, explanation: str, severity: str = "warning", issue_type: str = "punctuation") -> Dict[str, Any]:
    return {
        "original": original,
        "type": issue_type,
        "suggestion": suggestion,
        "explanation": explanation,
        "severity": severity,
        "chunk_index": 0,
        "source": "format_rule",
    }


def _days_in_month(year: int, month: int) -> int:
    if month in (1, 3, 5, 7, 8, 10, 12):
        return 31
    if month in (4, 6, 9, 11):
        return 30
    # 闰年 2 月
    return 29 if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0) else 28


def check_dates(text: str) -> List[Dict[str, Any]]:
    """日期合法性：月 1-12、日不超当月天数、分隔符混合。"""
    issues: List[Dict[str, Any]] = []
    for m in _DATE_CN_RE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo < 1 or mo > 12:
            issues.append(_issue(
                m.group(0), m.group(0),
                f"月份非法：{mo}月不存在（1-12月）", "error",
            ))
        elif d < 1 or d > _days_in_month(y, mo):
            max_d = _days_in_month(y, mo)
            issues.append(_issue(
                m.group(0), m.group(0),
                f"日期非法：{y}年{mo}月最多{max_d}天", "error",
            ))
    # 分隔符混合：2025.1-3（. 和 - 混用）
    for m in _DATE_MIXED_SEP_RE.finditer(text):
        s = m.group(0)
        seps = {c for c in s[4:] if c in ".-/,、"}
        if len(seps) > 1:
            issues.append(_issue(
                s, s,
                "日期分隔符混用（如 . 与 - 混用），建议统一为一种", "warning",
            ))
    return issues


def check_phones(text: str) -> List[Dict[str, Any]]:
    """
    手机号完整性：1[3-9] 开头的 8-12 位数字（11 位合法除外）——
    位数不对的号码碎片。前后有数字的排除（是长数字的一部分）。
    """
    issues: List[Dict[str, Any]] = []
    # 提取上下文中明确的"电话/手机/联系"语境的数字串
    for m in re.finditer(r"(?:电话|手机|联系方式|热线|致电)[号码]?[：:为是\s]*([1-9]\d{6,12})", text):
        num = m.group(1)
        if len(num) == 11 and _PHONE_RE.fullmatch(num):
            continue  # 合法手机号
        if len(num) in (10, 12):
            # 10 位固话区号形式（0101234567）或 400 热线——不确定，跳过
            if num.startswith("0") or num.startswith("400") or num.startswith("1"):
                continue
        issues.append(_issue(
            num, num,
            f"号码位数异常：{len(num)}位（手机号应为11位，请核对）", "warning",
        ))
    return issues


def check_id_cards(text: str) -> List[Dict[str, Any]]:
    """身份证校验位验证（18 位号）。"""
    issues: List[Dict[str, Any]] = []
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    codes = "10X98765432"
    for m in _ID_CARD_RE.finditer(text):
        id_num = m.group(0)
        try:
            # 出生日期校验
            birth = date(int(id_num[6:10]), int(id_num[10:12]), int(id_num[12:14]))
            if not (1900 < birth.year <= 2100):
                raise ValueError
        except ValueError:
            issues.append(_issue(
                id_num, id_num,
                "身份证出生日期段非法", "error",
            ))
            continue
        # 校验位
        total = sum(int(id_num[i]) * weights[i] for i in range(17))
        if codes[total % 11] != id_num[17].upper():
            issues.append(_issue(
                id_num, id_num,
                "身份证校验位不匹配，号码可能有误", "warning",
            ))
    return issues


def check_amounts(text: str) -> List[Dict[str, Any]]:
    """金额冗余量级：X万元 中 X≥10000（如 10000万元 = 1亿元），几乎必是单位用错。"""
    issues: List[Dict[str, Any]] = []
    for m in _AMOUNT_WAN_RE.finditer(text):
        val = int(m.group(1))
        if val >= 10000:
            issues.append(_issue(
                m.group(0), m.group(0),
                f"金额量级冗余：{val}万元建议写作 {val // 10000}亿元（或直接用完整数字）", "warning",
            ))
    return issues


def check_sequence_style(text: str) -> List[Dict[str, Any]]:
    """同级列表编号样式混用：（一）（二）与 1. 2. 混用。两组各≥2 项才报。"""
    issues: List[Dict[str, Any]] = []
    cn_items = _SEQ_CN_RE.findall(text)
    ar_items = _SEQ_AR_RE.findall(text)
    if len(cn_items) >= 2 and len(ar_items) >= 2:
        issues.append(_issue(
            (ar_items[0] if ar_items else "").strip() or "编号",
            (ar_items[0] if ar_items else "").strip() or "编号",
            "列表编号样式混用：中文编号（一、二、）与阿拉伯数字编号（1. 2.）混用，建议统一", "warning",
        ))
    return issues


def check_confusable_collocations(text: str) -> List[Dict[str, Any]]:
    """易混词搭配：权力/权利等高混淆对，仅搭配层面无歧义时报告。

    「享有权力和义务」同时命中「享有权力」与「权力和义务」——按最长匹配
    优先去重，重叠区间只报一次（更长的搭配判定更可靠）。
    """
    matches: List[tuple] = []  # (start, wrong, right, note)
    for wrong, right, note in CONFUSABLE_COLLOCATIONS:
        start = 0
        while True:
            idx = text.find(wrong, start)
            if idx == -1:
                break
            matches.append((idx, wrong, right, note))
            start = idx + len(wrong)

    matches.sort(key=lambda m: -len(m[1]))  # 长的优先
    taken: List[tuple] = []  # 已报告区间
    issues: List[Dict[str, Any]] = []
    for start, wrong, right, note in matches:
        end = start + len(wrong)
        if any(start < t_end and t_start < end for t_start, t_end in taken):
            continue
        taken.append((start, end))
        issues.append(_issue(wrong, right, note, "warning", "typo"))
    issues.sort(key=lambda i: text.find(i["original"]))
    return issues


def check_halfwidth_punct(text: str) -> List[Dict[str, Any]]:
    """半角标点混入中文语句：命中即建议全角替换（逐字符映射，run 整体替换）。"""
    issues: List[Dict[str, Any]] = []
    for pattern in (_HALFWIDTH_CORE_RE, _HALFWIDTH_COLON_RE):
        for m in pattern.finditer(text):
            run = m.group(0)
            fixed = run.translate(_HALFWIDTH_MAP)
            issues.append(_issue(
                run, fixed,
                f"中文语句中的半角标点「{run}」应为全角「{fixed}」", "warning",
            ))
    issues.sort(key=lambda i: text.find(i["original"]))
    return issues


def check_format_rules(text: str) -> List[Dict[str, Any]]:
    """格式规则引擎入口"""
    issues: List[Dict[str, Any]] = []
    try:
        issues.extend(check_dates(text))
        issues.extend(check_phones(text))
        issues.extend(check_id_cards(text))
        issues.extend(check_amounts(text))
        issues.extend(check_sequence_style(text))
        issues.extend(check_confusable_collocations(text))
        issues.extend(check_halfwidth_punct(text))
    except Exception as e:
        logger.warning(f"[格式规则] 执行异常（跳过）: {e}")
    if issues:
        logger.info(f"[格式规则] 命中 {len(issues)} 项")
    return issues
