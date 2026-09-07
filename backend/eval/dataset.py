"""
TextMirror 审校评测集（固定样本，回归跑分用）

每条样本：text 待审文本 + expected 期望命中的锚点列表 + must_not_miss 必须零误报的锚点
锚点为「原文中应被某条 issue 的 original 覆盖的片段」——用包含匹配
（LLM 的 original 可能比锚点长，确定性层则精确）。

样本来源：会话中实测过的真实案例（含历史漏报案例），覆盖六大维度。
每次改 prompt/规则/模型后跑 eval.py 对比指标变化。
"""

SAMPLES = [
    # ---- 错别字 ----
    {"id": "typo-1", "dim": "错别字", "domain": "general",
     "text": "随着人工智能技术的突飞猛进，各行各业都迎来了翻天复地的变革。",
     "expect": ["翻天复地"]},
    {"id": "typo-2", "dim": "错别字", "domain": "general",
     "text": "我们取得了成绩，但不能骄傲，要再接再励。这个方案两全齐美。",
     "expect": ["再接再励", "两全齐美"]},
    {"id": "typo-3", "dim": "错别字", "domain": "general",
     "text": "请登录帐号查看详细内容。",
     "expect": ["帐号"]},

    # ---- 语法/搭配 ----
    {"id": "grammar-1", "dim": "语法", "domain": "general",
     "text": "通过这次培训，使全体干部的思想觉悟得到了显著提升。",
     "expect": []},  # 「通过…使…」缺主语，LLM 应报（锚点不定，人工验收项）
    {"id": "grammar-2", "dim": "语法", "domain": "general",
     "text": "本产品全国第一，遥遥领先于所有竞争对手，是行业内最最好的选择。",
     "expect": ["最最好"]},

    # ---- 逻辑/事实 ----
    {"id": "logic-1", "dim": "逻辑", "domain": "general",
     "text": "元好问是唐代著名的现实主义诗人。",
     "expect": ["唐代"]},
    {"id": "logic-2", "dim": "逻辑", "domain": "general",
     "text": "该公司去年营收增长30%，利润大幅下滑。总体来看，公司经营状况非常乐观，各项指标全面向好。",
     "expect": []},  # 前后矛盾，验收项
    {"id": "logic-3", "dim": "逻辑", "domain": "general",
     "text": "该项目的三个核心目标分别是降低成本、提高效率。经过两年努力，这一目标已全部实现。",
     "expect": ["这一目标"]},

    # ---- 格式（规则引擎应 100%） ----
    {"id": "fmt-1", "dim": "格式", "domain": "general",
     "text": "会议定于2025年13月45日召开。",
     "expect": ["2025年13月45日"]},
    {"id": "fmt-2", "dim": "格式", "domain": "general",
     "text": "截止2023年2月29日。",
     "expect": ["2023年2月29日"]},
    {"id": "fmt-3", "dim": "格式", "domain": "general",
     "text": "联系方式138001380，欢迎咨询。",
     "expect": ["138001380"]},
    {"id": "fmt-4", "dim": "格式", "domain": "general",
     "text": "项目总投资10000万元。",
     "expect": ["10000万元"]},

    # ---- 一致性（规则引擎应 100%） ----
    {"id": "cons-1", "dim": "一致性", "domain": "general",
     "text": "合同金额为贰佰万元整（￥200000元）。",
     "expect": ["贰佰万元整（￥200000元）"]},
    {"id": "cons-2", "dim": "一致性", "domain": "general",
     "text": "任务分工：\n一、准备\n二、实施\n1. 落实资金\n2. 明确责任",
     "expect": []},
    {"id": "cons-3", "dim": "一致性", "domain": "general",
     "text": "杭州国电南自自动化有限公司负责实施。项目由国电南自统筹，杭州国电南自自动化有限公司验收。",
     "expect": ["国电南自"]},

    # ---- 敏感词（词库引擎应 100%） ----
    {"id": "sens-1", "dim": "敏感词", "domain": "general",
     "text": "该组织企图颠覆国家政权，已被依法处理。",
     "expect": ["颠覆国家政权"]},

    # ---- 零误报对照（正确文本，任何 issue 都算误报） ----
    {"id": "clean-1", "dim": "零误报", "domain": "general",
     "text": "会议定于2025年8月15日召开，联系电话13800138000，项目总投资3000万元。",
     "expect": []},
    {"id": "clean-2", "dim": "零误报", "domain": "general",
     "text": "该站配置了SF6断路器与GIS组合电器，采用了VLAN划分和OSPF动态路由协议，满足N-1供电可靠性要求。",
     "expect": []},
    {"id": "clean-3", "dim": "零误报", "domain": "general",
     "text": "国网浙江供电公司完成了110kV变电站检修。合同金额为人民币壹拾万元整（￥100000）。",
     "expect": []},
    {"id": "clean-4", "dim": "零误报", "domain": "official",
     "text": "各部门要严格执行会议决定，确保各项任务按时完成。特此通知。",
     "expect": []},
    {"id": "clean-5", "dim": "零误报", "domain": "general",
     "text": "合同约定：第一条 总则。第二条 双方权利义务。第三条 违约责任。第四条 争议解决。",
     "expect": []},

    # ---- 领域（公文规则） ----
    {"id": "domain-1", "dim": "领域规则", "domain": "official",
     "text": "发文字号为杭政办[2025]第18号。",
     "expect": []},

    # ---- 标点规范（第二梯队新增） ----
    {"id": "punct-1", "dim": "标点", "domain": "general",
     "text": "本项目包括三个阶段:前期准备,中期实施,后期验收。",
     "expect": [], "note": "LLM标点定位粒度波动大（裸:/:带上下文），人工验收项"},
    {"id": "punct-2", "dim": "标点", "domain": "general",
     "text": "各阶段需提交\"总结报告\"。",
     "expect": ["总结报告"]},

    # ---- 数字体例（GB/T 15835） ----
    {"id": "num-1", "dim": "数字体例", "domain": "general",
     "text": "参加会议的共有五十八人，其中15人来自总部，三十二人来自分支机构。",
     "expect": ["五十八"]},

    # ---- 金额加总（确定性引擎，应 100%） ----
    {"id": "sum-1", "dim": "金额加总", "domain": "general",
     "text": "设备采购花费400万元，安装调试300万元，人员培训150万元，预备费250万元，管理费100万元，合计1250万元。",
     "expect": ["合计1250万元"]},
    {"id": "sum-2", "dim": "金额加总", "domain": "general",
     "text": "设备采购400万元，安装调试300万元，培训150万元，合计850万元。",
     "expect": []},
    {"id": "sum-3", "dim": "金额加总", "domain": "general",
     "text": "A项花费3000元，B项花费2000元，总计5000元。",
     "expect": []},

    # ---- 零误报扩展（本会话实测的正确文本） ----
    {"id": "clean-6", "dim": "零误报", "domain": "general",
     "text": "该站配置了SF6断路器与GIS组合电器，满足N-1供电可靠性要求。",
     "expect": []},
    {"id": "clean-7", "dim": "零误报", "domain": "general",
     "text": "版本2.1发布，性能大幅提升。更新内容包括界面优化和性能提升两个方面。",
     "expect": []},
    {"id": "clean-8", "dim": "零误报", "domain": "general",
     "text": "增长率为2.5%，利润率3.1%，均高于行业均值。",
     "expect": []},
    {"id": "clean-9", "dim": "零误报", "domain": "official",
     "text": "会议学习了党中央的重要讲话精神。要坚决贯彻上级决策部署，扎实推进各项工作。",
     "expect": []},
]
