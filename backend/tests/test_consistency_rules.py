"""一致性引擎：金额加总防误报 + 称谓混用 span 回归。

金额加总的三个历史误报形态（前文总额句被当明细重复计入、「人民币」
前缀导致总额句漏配、「小计」不在总额词表）与两个正向命中锁定在此；
称谓混用须上报文中实际出现的简称完整串，不能是截断的前缀。
"""
from app.services.consistency import check_amount_summation, check_naming_consistency


def _sums(text: str) -> list:
    return [(i["original"], i["explanation"]) for i in check_amount_summation(text)]


def _names(text: str) -> list:
    return [i["original"] for i in check_naming_consistency(text)]


def test_fp_total_phrase_not_double_counted():
    # 前文「总投资3000万元」是总额句，其金额不是明细项
    text = "项目总投资3000万元，其中设备费1200万元占40%，材料费800万元，人工费1000万元，合计3000万元。"
    assert _sums(text) == []


def test_fp_rmb_prefixed_total():
    text = "总投资为人民币3000万元，其中设备费1200万元，材料费1800万元，合计3000万元。"
    assert _sums(text) == []


def test_fp_subtotal_phrase():
    # 小计/合计两级总额各自核验，上级总额句的金额不污染下级求和
    text = "设备费100万元，材料费200万元，小计300万元；管理费50万元，其他费用30万元，合计80万元。"
    assert _sums(text) == []


def test_correct_sum_silent():
    assert _sums("设备采购400万元，安装调试300万元，培训150万元，合计850万元。") == []
    assert _sums("A项花费3000元，B项花费2000元，总计5000元。") == []
    # 总额句在前、明细在后的正确文本（向后不核验，不得误报）
    assert _sums("装修费用合计8万元：地板3万元，涂料2万元，人工3万元。") == []


def test_mismatch_still_fires():
    hits = _sums("差旅费2000元，餐费1500元，住宿费2500元，总计5000元。")
    assert len(hits) == 1 and hits[0][0] == "总计5000元"
    hits = _sums("设备采购花费400万元，安装调试300万元，人员培训150万元，预备费250万元，管理费100万元，合计1250万元。")
    assert len(hits) == 1 and hits[0][0] == "合计1250万元"


def test_mixed_units_skip():
    assert _sums("设备费400万元，运费100元，合计4000100元。") == []


def test_naming_reports_actual_abbr_span():
    hits = _names(
        "北京华宇信息技术有限公司负责系统开发，华宇信息技术提供了运维支持，"
        "北京华宇信息技术有限公司组织终验。"
    )
    assert hits == ["华宇信息技术"]


def test_naming_existing_behavior_unchanged():
    hits = _names(
        "杭州国电南自自动化有限公司负责实施。项目由国电南自统筹，杭州国电南自自动化有限公司验收。"
    )
    assert hits == ["国电南自"]


def test_naming_no_mixed_usage_silent():
    assert _names("北京华宇信息技术有限公司负责系统开发，北京华宇信息技术有限公司组织终验。") == []
