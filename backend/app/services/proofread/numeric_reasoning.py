import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

_VALUE = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万亿]+)"
_UNITS = {
    "秒": ("time", 1), "分钟": ("time", 60), "小时": ("time", 3600),
    "天": ("time", 86400), "日": ("time", 86400), "周": ("time", 604800),
    "年": ("calendar", 365), "个月": ("calendar", 30), "月": ("calendar", 30),
    "毫米": ("length", 1), "厘米": ("length", 10), "米": ("length", 1000), "千米": ("length", 1000000), "公里": ("length", 1000000),
    "克": ("mass", 1), "千克": ("mass", 1000), "公斤": ("mass", 1000), "吨": ("mass", 1000000),
    "元": ("money", 1), "万元": ("money", 10000), "亿元": ("money", 100000000),
    "人": ("people", 1), "万人": ("people", 10000), "亿人": ("people", 100000000),
    "台": ("machines", 1), "万台": ("machines", 10000), "个": ("items", 1), "万个": ("items", 10000),
    "%": ("percent", 1), "％": ("percent", 1),
}
_QUANTITY = re.compile(
    r"(?<![\d.A-Za-z\-−—~～至到零〇一二两三四五六七八九十百千万亿点])(?:(?P<prefix>不超过|不少于|至少|至多|超过|不足|不满|少于|小于|低于|多于|大于|高于|大约|约|近)\s*)?"
    + rf"(?P<value>{_VALUE})\s*(?P<more>余|多)?\s*(?P<unit>"
    + "|".join(sorted(_UNITS, key=len, reverse=True))
    + r")(?P<suffix>左右|以上|以下|余|多)?(?![A-Za-z²³])"
)
_SENTENCE = re.compile(r"[^。！？；\n]+")
_GROUP = re.compile(r"团队|全体|集体|志愿者们|他们|她们|人次|人时|工作组|服务队|服务团|服务站|组织累计|[\d一二两三四五六七八九十百]+(?:名|位)志愿者")
_UNCERTAIN_SCOPE = re.compile(r"其中|另有|分别|同期|去年|今年|此前|以后|过去|此前|曾经")
_SUM = re.compile(r"(?:合计|总计|总共|共计|共有|总数为|总人数为)\s*$")
_EQUIVALENCE = re.compile(r"\s*(?:[（(]\s*(?:即|等于|相当于|折合|换算为)?|[，,]?\s*(?:即|等于|相当于|折合|换算为|=|＝))\s*$")
_SPECULATION = re.compile(r"夸张|不符合常理|不符合逻辑|明显不合理|不可能|数字过大|数值过大|时长过长|日均过高|量级冗余")
_NUMERIC_REASON = re.compile(r"加总|合计|换算|日均|人均|平均|占比|百分比|数量级|量级|倍数|算术")


@dataclass(frozen=True)
class Quantity:
    raw: str
    value: Decimal
    unit: str
    prefix: str
    more: str
    suffix: str
    start: int
    end: int

    @property
    def exact(self) -> bool:
        return not (self.prefix or self.more or self.suffix)

    @property
    def dimension(self) -> str:
        return _UNITS[self.unit][0]

    @property
    def scaled(self) -> Decimal:
        with localcontext() as context:
            context.prec = 64
            return self.value * _UNITS[self.unit][1]


def _quantities(text: str) -> list[Quantity]:
    quantities = []
    for match in _QUANTITY.finditer(text):
        number = match["value"].replace(",", "")
        if len(number) > 24 or "万亿" in number:
            continue
        try:
            if re.fullmatch(r"[零〇一二两三四五六七八九]+", number):
                number = number.translate(str.maketrans("零〇一二两三四五六七八九", "001223456789"))
            elif not number[0].isdigit():
                from app.services.consistency import _cn_to_number
                number = str(_cn_to_number(number.replace("〇", "零")))
            value = Decimal(number)
        except InvalidOperation:
            continue
        quantities.append(Quantity(match.group(), value, match["unit"], match["prefix"] or "",
                                   match["more"] or "", match["suffix"] or "", match.start(), match.end()))
    return quantities


def _fmt(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 64
        if value and abs(value) < Decimal("0.01"):
            return format(value.normalize(), "f")
        return format(value.quantize(Decimal("0.01")), "f").rstrip("0").rstrip(".")


def _issue(quantity: Quantity, offset: int, explanation: str, severity: str = "warning") -> dict:
    return {"original": quantity.raw, "suggestion": "", "type": "logic", "severity": severity,
            "explanation": explanation, "source": "numeric_rule",
            "start": offset + quantity.start, "end": offset + quantity.end}


def _service_intensity(sentence: str, quantities: list[Quantity], offset: int) -> list[dict]:
    if "志愿" not in sentence or _GROUP.search(sentence):
        return []
    periods = [q for q in quantities if q.unit in ("年", "月", "个月", "天", "日", "周")]
    hours = [q for q in quantities if q.unit == "小时"]
    if len(periods) != 1 or len(hours) != 1:
        return []
    period, total = periods[0], hours[0]
    if period.value <= 0 or period.value >= 1000 or total.value <= 0:
        return []
    if (total.prefix in ("不超过", "至多", "不足", "不满", "少于", "小于", "低于") or total.suffix == "以下"
            or period.prefix in ("不少于", "至少", "超过", "多于", "大于", "高于")
            or period.more or period.suffix in ("以上", "余", "多")):
        return []
    before_period = sentence[:period.start]
    after_period = sentence[period.end:total.start]
    if not (re.search(r"志愿(?:服务|者工作|者)(?:已有|已经|已|了|达|满)?\s*$", before_period)
            or (sentence[period.end:].startswith(("来", "内", "间")) and "志愿" in after_period)):
        return []
    if total.start < period.end or _UNCERTAIN_SCOPE.search(after_period):
        return []
    if not re.fullmatch(r"[\s，,：:]*(?:(?:来|内|间|累计|总计|共计|共有|共|服务|志愿|时长|总时长|达|为|了|已|长达|的)[\s，,：:]*)+", after_period):
        return []
    if not re.search(r"服务|累计|时长", after_period):
        return []
    if period.unit == "年":
        days, max_days, assumption = Decimal(365), Decimal(366), "每年365天估算，未计闰日"
    elif period.unit in ("月", "个月"):
        days, max_days, assumption = Decimal(30), Decimal(31), "每月30天估算"
    else:
        days = max_days = Decimal(7 if period.unit == "周" else 1)
        assumption = "按连续自然日计算"
    average = total.value / (period.value * days)
    # 3小时仅是志愿服务核实提醒的触发线，不是“不合理”的事实标准。
    if average < 3:
        return []
    formula = f"{_fmt(total.value)} ÷ ({_fmt(period.value)} × {_fmt(days)}) ≈ {_fmt(average)}小时/天"
    qualifiers = "原文含约数或上下限，结果仅作估算；" if not period.exact or not total.exact else ""
    explanation = f"按{assumption}：{formula}；{qualifiers}请核实是否为个人累计、统计时段及小时单位，不据此认定数据错误。"
    individual = re.search(r"个人|本人|(?<!其)[他她](?!们)", sentence) is not None
    total_has_lower_bound = (total.prefix in ("", "超过", "至少", "不少于")
                             and total.suffix not in ("左右", "以下"))
    if individual and period.exact and total_has_lower_bound and total.value > period.value * max_days * 24:
        explanation = (f"按同一人连续服务计算：即使每{period.unit}按{_fmt(max_days)}天，"
                       f"{_fmt(total.value)} ÷ ({_fmt(period.value)} × {_fmt(max_days)}) > 24小时/天，超过个人自然时间上限；"
                       "请核实是否重复计时、团队累计或统计时段/单位有误，不自动改数。")
        return [_issue(total, offset, explanation, "error")]
    return [_issue(total, offset, explanation)]


def _unit_conversions(sentence: str, quantities: list[Quantity], offset: int) -> list[dict]:
    issues = []
    for left, right in zip(quantities, quantities[1:]):
        bridge = sentence[left.end:right.start]
        if not bridge.strip() or not _EQUIVALENCE.fullmatch(bridge):
            continue
        if any(char in bridge for char in "（(") and not sentence[right.end:].lstrip().startswith((")", "）")):
            continue
        if left.dimension != right.dimension or left.dimension == "calendar" or left.unit == right.unit:
            continue
        if left.scaled == right.scaled:
            continue
        if not left.exact or not right.exact:
            if abs(left.scaled - right.scaled) <= max(abs(left.scaled), abs(right.scaled)) * Decimal("0.05"):
                continue
            severity, qualifier = "warning", "原文含约数，仅提示核实"
        else:
            severity, qualifier = "error", "两种表述不等值"
        converted = left.scaled / _UNITS[right.unit][1]
        explanation = f"单位核算：{left.raw.strip()} = {_fmt(converted)}{right.unit}，原文为{right.raw.strip()}；{qualifier}，请核对数值和单位。"
        issues.append(_issue(right, offset, explanation, severity))
    return issues


def _ratios(sentence: str, quantities: list[Quantity], offset: int) -> list[dict]:
    if "其中" not in sentence or not re.search(r"占比|占总|占全部|比例|占", sentence):
        return []
    if len(quantities) != 3 or re.search(r"去年|今年|上年|同比|环比|增长|目标|预算|计划|相比", sentence):
        return []
    total, part, percent = quantities
    if percent.dimension != "percent" or total.dimension != part.dimension or total.dimension not in ("people", "machines", "items", "money"):
        return []
    if not re.search(r"共|总数|总计|总额|总人数|总量", sentence[:total.start]):
        return []
    if "其中" not in sentence[total.end:part.start] or total.value <= 0:
        return []
    if not re.fullmatch(r"[\s，,]*(?:(?:参加|参与|完成|达标|合格|及格)[\s，,]*)?(?:占比为|占比|占总数的?|占全部的?|占|比例为|比例是|比例)[\s：:]*", sentence[part.end:percent.start]):
        return []
    if not all(q.exact for q in quantities):
        return []
    computed = part.scaled / total.scaled * 100
    if part.scaled > total.scaled:
        return [_issue(part, offset, f"同口径部分量{part.raw.strip()}超过总量{total.raw.strip()}，占比核算为{_fmt(computed)}%；请核实是否统计对象或口径不一致。", "error")]
    precision = max(0, -percent.value.as_tuple().exponent)
    tolerance = Decimal("0.5") * Decimal(10) ** -precision
    if abs(computed - percent.value) <= tolerance:
        return []
    formula = f"{_fmt(part.scaled)} ÷ {_fmt(total.scaled)} × 100% ≈ {_fmt(computed)}%"
    return [_issue(percent, offset, f"同口径占比核算：{formula}，与原文{percent.raw.strip()}不符；请核实分子、分母及占比，不自动改数。", "error")]


def _count_sums(sentence: str, quantities: list[Quantity], offset: int) -> list[dict]:
    if any(q.dimension == "percent" for q in quantities) or re.search(r"其中|去重|包含|包括|重叠|交叉|重复|去年|今年|同比|计划|实际|目标", sentence):
        return []
    issues, items = [], []
    previous_end = 0
    for quantity in quantities:
        prefix = sentence[previous_end:quantity.start]
        previous_end = quantity.end
        if _SUM.search(prefix) or re.search(r"全校|全班|总量|总数|总人数", prefix):
            if (len(items) >= 2 and quantity.dimension in ("people", "machines", "items")
                    and all(q.dimension == quantity.dimension and q.exact for q in items) and quantity.exact):
                summed = sum(q.scaled for q in items)
                if summed != quantity.scaled:
                    formula = " + ".join(_fmt(q.scaled) for q in items)
                    unit = {"people": "人", "machines": "台", "items": "个"}[quantity.dimension]
                    labels = [re.split(r"[，,、]", sentence[:q.start])[-1].strip() for q in items]
                    disjoint = (set(labels) in ({"男生", "女生"}, {"男性", "女性"})
                                or all(re.fullmatch(r"[甲乙丙丁戊己庚辛壬癸A-Z]组", label) for label in labels)
                                or "互不重叠" in sentence)
                    issues.append(_issue(quantity, offset, f"若明细同口径且互不重叠：{formula} = {_fmt(summed)}{unit}，与原文{quantity.raw.strip()}不符；请核实统计口径与明细完整性。", "error" if disjoint else "warning"))
            items = []
        else:
            items.append(quantity)
    return issues


def check_numeric_reasoning(text: str) -> list[dict]:
    issues = []
    previous = None
    for match in _SENTENCE.finditer(text):
        sentence = match.group()
        quantities = _quantities(sentence)
        if (previous is not None and len(quantities) == 1
                and re.match(r"\s*(?:累计(?:志愿)?服务|服务(?:总)?时长)", sentence)
                and len(_quantities(previous.group())) == 1 and "志愿" in previous.group()):
            combined = text[previous.start():match.end()]
            combined = combined.translate(str.maketrans({"。": "，", "\n": "，", "；": "，", "！": "，", "？": "，"}))
            issues.extend(_service_intensity(combined, _quantities(combined), previous.start()))
        previous = match
        if len(quantities) < 2:
            continue
        issues.extend(_service_intensity(sentence, quantities, match.start()))
        issues.extend(_unit_conversions(sentence, quantities, match.start()))
        issues.extend(_ratios(sentence, quantities, match.start()))
        issues.extend(_count_sums(sentence, quantities, match.start()))
    return issues


def _unmeasured_numbers(text: str, quantities: list[Quantity]) -> list[str]:
    return [match.group().replace(",", "") for match in re.finditer(r"\d+(?:[.,]\d+)*", text)
            if not any(q.start <= match.start() and match.end() <= q.end for q in quantities)]


def protect_numeric_suggestions(issues: list[dict]) -> list[dict]:
    protected = []
    for issue in issues:
        if issue.get("source") in ("dict_scan", "consistency", "format_rule", "person_consistency", "numeric_rule", "numeric_guard"):
            protected.append(issue)
            continue
        original, suggestion = issue.get("original") or "", issue.get("suggestion") or ""
        before, after = _quantities(original), _quantities(suggestion)
        old_numbers = re.findall(r"\d+(?:\.\d+)?", original.replace(",", ""))
        new_numbers = re.findall(r"\d+(?:\.\d+)?", suggestion.replace(",", ""))
        explanation = issue.get("explanation") or ""
        if (before or old_numbers) and (_SPECULATION.search(explanation)
                or (issue.get("type") == "logic" and _NUMERIC_REASON.search(explanation))):
            continue
        changed_numbers = old_numbers != new_numbers
        facts_before = [(q.dimension, q.scaled, q.exact) for q in before]
        facts_after = [(q.dimension, q.scaled, q.exact) for q in after]
        equivalent = (bool(before) and facts_before == facts_after
                      and _unmeasured_numbers(original, before) == _unmeasured_numbers(suggestion, after))
        changed_units = (facts_before != facts_after or (
            original.strip() in _UNITS and suggestion.strip() in _UNITS and _UNITS[original.strip()] != _UNITS[suggestion.strip()]
        ))
        if suggestion and not equivalent and (changed_numbers or changed_units):
            issue = {**issue, "suggestion": "", "type": "logic", "severity": "warning", "source": "numeric_guard",
                     "explanation": "数字或单位的修改缺少确定性依据，请核对原始数据；不提供自动替换。"}
        protected.append(issue)
    return protected
