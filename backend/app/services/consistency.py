"""
TextMirror 跨片一致性检查器（纯规则引擎，不调 LLM）

分片审校的固有盲区：每片独立送审，全文级的不一致（大小写金额矛盾、
全称/简称混用、编号断档）在单片内看不出来。本模块对全文做确定性
比对，结果与词库扫描同路径合入 issue 流。

设计原则：只报高置信度问题（宁可漏报不可误报），每类检查都有
明确证据（同一金额两种写法、同一实体两种称谓）才产出 issue。
"""
import re
from typing import Any, Dict, List, Optional

from loguru import logger

# 中文数字映射（支持「贰佰万」「两百万元」等常见写法）
_CN_DIGIT = {
    "零": 0, "一": 1, "壹": 1, "二": 2, "贰": 2, "两": 2,
    "三": 3, "叁": 3, "四": 4, "肆": 4, "五": 5, "伍": 5,
    "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}
_CN_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_BIG_UNIT = {"万": 10000, "萬": 10000, "亿": 100000000, "億": 100000000}

# 中文大写金额（壹贰叁…拾佰仟万亿，银行/合同写法）
_CN_AMOUNT_RE = re.compile(r"[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]{2,}元")
# 中文小写金额（一二两三…十百千万亿，普通行文写法）
_CN_AMOUNT_RE_LOOSE = re.compile(r"[零一二两三四五六七八九十百千万亿]{2,}元")
# 数字金额（含千分位）
_NUM_AMOUNT_RE = re.compile(r"[\d,]+(?:\.\d+)?")
# 编号序列（第X条 / X、 / (X) 等开头行）
_SEQ_ITEM_RE = re.compile(r"^第([一二三四五六七八九十\d]+)[条款项步部分章]|^(\d+)[、.．]\s*|^[（(](\d+)[)）]", re.MULTILINE)


def _cn_to_number(cn: str) -> Optional[int]:
    """
    中文数字串转数值（如「贰佰」→200、「一万三千」→13000）。
    解析失败返回 None（宁缺毋滥）。
    """
    total, section, value = 0, 0, 0
    for ch in cn:
        if ch in _CN_DIGIT:
            value = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            section += (value or 1) * _CN_UNIT[ch]
            value = 0
        elif ch in _CN_BIG_UNIT:
            unit = _CN_BIG_UNIT[ch]
            if section == 0 and value == 0:
                # 「万」直接跟在已有 section 后（如「二百五十万」的万）
                section = 1
            total += (section + value) * unit
            section, value = 0, 0
        else:
            return None
    return total + section + value


def _issue(original: str, suggestion: str, explanation: str, severity: str = "warning") -> Dict[str, Any]:
    return {
        "original": original,
        "type": "logic",
        "suggestion": suggestion,
        "explanation": explanation,
        "severity": severity,
        "chunk_index": 0,
        "source": "consistency",
    }


def check_amount_consistency(text: str) -> List[Dict[str, Any]]:
    """
    检查金额一致性（高置信度场景）：
    「中文大写/小写金额 + 括号并注数字」数值对不上，如
    「贰佰万元整（￥2000000元）」一致；「贰佰万元（￥200000元）」矛盾。
    另查同括号语境下「万元」换算错位（如中文两百万 括注 200万→一致；
    括注 2000000万→明显错位）。
    """
    issues: List[Dict[str, Any]] = []
    # 中文金额（可选万/亿单位 + 元/元整）+ 括号并注数字（可选万）
    bracket_pat = re.compile(
        r"([零壹贰叁肆伍陆柒捌玖拾佰仟万亿一二两三四五六七八九十百千万]+)\s*(万|亿)?\s*元[整正]?\s*[（(]\s*[￥¥]?\s*([\d,]+(?:\.\d+)?)\s*(万|亿)?\s*元?[）)]"
    )
    for m in bracket_pat.finditer(text):
        cn_part, cn_unit, num_part, num_unit = m.group(1), m.group(2), m.group(3), m.group(4)
        cn_val = _cn_to_number(cn_part)
        if cn_val is None:
            continue
        if cn_unit:
            cn_val *= _CN_BIG_UNIT[cn_unit]
        num_val = float(num_part.replace(",", ""))
        if num_unit:
            num_val *= _CN_BIG_UNIT[num_unit]
        if cn_val != num_val and cn_val > 0:
            issues.append(_issue(
                m.group(0)[:60],
                m.group(0)[:60],
                f"金额大小写不一致：{cn_part}{cn_unit or ''}元 与 {num_part}{num_unit or ''} 数值不符",
                "error",
            ))
    return issues


def check_naming_consistency(text: str) -> List[Dict[str, Any]]:
    """
    检查全称/简称混用：同一实体以多种称谓出现。
    策略（高置信度，针对「全称多次 + 简称独立出现」的最常见形态）：
    1. 找出现 ≥2 次的机构全称（公司/集团/研究院等结尾，≥6 字）
    2. 取全称的「专名部分」（去机构后缀、去行政区划前缀）
    3. 检测专名部分的连续子串（≥3 字）在文中有独立出现
       （不在任何全称实例内）——即为简称，提示统一
    例：「杭州国电南自自动化有限公司」×N +「国电南自」独立出现 → 报混用
    """
    issues: List[Dict[str, Any]] = []
    suffixes = ("有限公司", "股份公司", "公司", "集团", "中心", "研究院", "研究所",
                "委员会", "大学", "学院", "医院", "银行", "事务所")
    region_prefixes = ("中国", "北京", "上海", "天津", "重庆", "浙江", "江苏", "广东",
                       "山东", "河南", "四川", "杭州", "南京", "苏州", "深圳", "广州", "武汉", "西安")

    from collections import Counter
    name_counter: Counter = Counter()
    for suf in suffixes:
        for m in re.finditer(r"[\u4e00-\u9fa5A-Za-z·]{2,16}" + suf, text):
            # 用 span 去重：同一位置被多个后缀匹配只计一次
            name_counter[m.group()] += 1 if m.group() not in name_counter else 0
    # 直接改用集合 + 手动计数
    all_names = set()
    for suf in suffixes:
        for m in re.finditer(r"[\u4e00-\u9fa5A-Za-z·]{2,16}" + suf, text):
            all_names.add(m.group())

    for full in all_names:
        if text.count(full) < 2 or len(full) < 6:
            continue
        core = full
        for suf in sorted(suffixes, key=len, reverse=True):
            if core.endswith(suf):
                core = core[: -len(suf)]
                break
        for pref in region_prefixes:
            if core.startswith(pref) and len(core) - len(pref) >= 3:
                core = core[len(pref):]
                break
        if len(core) < 4:
            continue
        # 核心专名的连续子串（从长到短，≥3 字）独立出现检测。
        # 必须从完整核心长度开始扫——否则「华宇信息技术」被截成
        # 「华宇信息技」上报，span 与文中实际简称不符
        for size in range(len(core), 2, -1):
            for i in range(len(core) - size + 1):
                abbr = core[i: i + size]
                total = text.count(abbr)
                in_full = text.count(full) * full.count(abbr)
                standalone = total - in_full
                if standalone >= 1 and size >= 3:
                    issues.append(_issue(
                        abbr,
                        full,
                        f"称谓混用：「{abbr}」与全称「{full}」混用，建议统一称谓",
                        "warning",
                    ))
                    break  # 每个全称只报最长的独立简称
            if issues and issues[-1]["original"] in core:
                break
    return issues


def check_sequence_continuity(text: str) -> List[Dict[str, Any]]:
    """
    检查编号序列断档：如「第一条、第二条、第四条」（缺第三条）。
    仅对同一前缀样式连续出现 ≥3 次时启用，避免误报。
    """
    issues: List[Dict[str, Any]] = []
    # 第X条/款/项 序列
    seq_matches = list(re.finditer(r"第([一二三四五六七八九十\d]+)([条款项])", text))
    if len(seq_matches) >= 3:
        unit = seq_matches[0].group(2)
        nums = []
        for m in seq_matches:
            if m.group(2) != unit:
                continue
            s = m.group(1)
            nums.append(int(s) if s.isdigit() else _cn_to_number(s))
        nums = [n for n in nums if n is not None]
        if len(nums) >= 3:
            for i in range(1, len(nums)):
                if nums[i] - nums[i - 1] > 1 and nums[i] > nums[i - 1]:
                    missing = list(range(nums[i - 1] + 1, nums[i]))
                    issues.append(_issue(
                        seq_matches[i].group(0),
                        seq_matches[i].group(0),
                        f"编号断档：{unit}序列从 {nums[i-1]} 跳到 {nums[i]}（缺第{'、第'.join(str(x) for x in missing[:3])}{unit}）",
                        "warning",
                    ))
    return issues


def check_consistency(text: str) -> List[Dict[str, Any]]:
    """跨片一致性检查入口：金额一致 + 金额加总 + 称谓统一 + 编号连续"""
    issues: List[Dict[str, Any]] = []
    try:
        issues.extend(check_amount_consistency(text))
        issues.extend(check_amount_summation(text))
        issues.extend(check_naming_consistency(text))
        issues.extend(check_sequence_continuity(text))
    except Exception as e:
        logger.warning(f"[一致性检查] 执行异常（跳过）: {e}")
    if issues:
        logger.info(f"[一致性检查] 命中 {len(issues)} 项")
    return issues


# 加总语境：合计/总计/小计/总投资/总费用/总支出 后跟数字金额
# （「小计」与「合计」在分节文本中同级共存，均为总额句；「人民币」前缀不影响判定）
_SUM_TOTAL_RE = re.compile(r"(合计|总计|小计|总金额|总投资|总费用|总支出|总额|共计)[为是:：]?\s*(?:人民币)?\s*([0-9]+(?:\.[0-9]+)?)\s*(万元|亿元|元|万|亿)")
# 前文金额项：数字 + 可选万元/亿元单位
_AMOUNT_ITEM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万元|亿元|元|万|亿)")


def _amount_to_yuan(value: float, unit: str) -> float:
    """金额+单位 → 元（统一量纲）"""
    unit = unit or "元"
    if unit in ("万元", "万"):
        return value * 10000
    if unit in ("亿元", "亿"):
        return value * 100000000
    return value


def check_amount_summation(text: str) -> List[Dict[str, Any]]:
    """
    金额加总核验：「合计X」与前文金额项的算术和比对。
    防误报约束（全满足才报）：
    - 前文金额项 ≥2 个且单位一致（同万元/同元）
    - 金额项出现在合计句之前 300 字内（语义相关性）
    - 金额项只取「上一个总额句」之后的段落——分节文本（小计/合计、
      总投资/合计）中各总额只汇总自己段落的明细，前文总额句的金额
      也不计入明细，否则正确文本被重复求和而误报
    - 差值超过 1%（容差处理四舍五入）
    """
    issues: List[Dict[str, Any]] = []
    # 全文所有总额句的匹配区间（区间内的金额是总额本身而非明细项）
    total_spans = [tm.span() for tm in _SUM_TOTAL_RE.finditer(text)]
    for m in _SUM_TOTAL_RE.finditer(text):
        total_str, unit = m.group(2), m.group(3)
        try:
            total_val = float(total_str)
        except ValueError:
            # 中文数字合计（如 合计贰佰万元）暂不处理，交给括号并注规则
            continue
        if not unit:
            continue  # 无单位无法定量纲
        total_yuan = _amount_to_yuan(total_val, unit)

        # 明细段落 = 上一个总额句结束 ~ 当前总额句开始（300 字窗口内）
        section_start = 0
        for ts, te in total_spans:
            if te <= m.start():
                section_start = te
        section = text[max(section_start, m.start() - 300): m.start()]
        items = []
        for am in _AMOUNT_ITEM_RE.finditer(section):
            v = float(am.group(1))
            u = am.group(2)
            yuan = _amount_to_yuan(v, u)
            items.append((yuan, am.group(0)))
        # 单位一致性：按主导单位（万元级）过滤——混单位（元与万元）时
        # 语义上可能各有归属，保守跳过
        unit_kinds = set()
        for _, raw in items:
            if "亿" in raw:
                unit_kinds.add("亿")
            elif "万" in raw:
                unit_kinds.add("万")
            else:
                unit_kinds.add("元")
        if len(items) < 2 or len(unit_kinds) > 1:
            continue
        # 同量纲（都是万元级或都是元级）
        item_yuans = [y for y, _ in items]
        s = sum(item_yuans)
        if total_yuan <= 0 or s <= 0:
            continue
        # 容差 1%
        if abs(s - total_yuan) / total_yuan > 0.01:
            issues.append(_issue(
                m.group(0),
                m.group(0),
                f"金额加总不符：前文各项合计 {s / 10000:.1f} 万元，与「{m.group(0)}」不符（差额 {abs(s - total_yuan) / 10000:.1f} 万元）",
                "error",
            ))
    return issues
