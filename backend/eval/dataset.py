"""
TextMirror 审校评测集（固定样本，回归跑分用）

每条样本：text 待审文本 + expect 期望命中的锚点列表。
锚点为「原文中应被某条 issue 的 original 覆盖的片段」——用包含匹配
（LLM 的 original 可能比锚点长，确定性层则精确）。

锚点设计原则：
- 确定性锚点只绑规则引擎层（词库扫描/格式规则/一致性检查），稳定 100%
- LLM 层锚点选形态稳定的：多轮验证 original 一致才收录；带上下文的
  引用比裸符号稳定（如「三个环节:」比裸「:」稳）
- expect=[] 且非零误报维度的样本为人工验收项（LLM 应产出 issue 但
  引用形态不定，跑分只看有没有产出）
- 零误报维度：任何 issue 都算误报，防误报回归的主闸门

覆盖度：LLM 判定类（语法/逻辑/标点/数字体例）每类 ≥5 个锚点；
确定性类由单元测试锁回归（tests/），eval 锚点做全链路冒烟。
样本来源：会话中实测过的真实案例（含历史漏报/误报案例）。
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
    {"id": "typo-6", "dim": "错别字", "domain": "general",
     "text": "时间紧迫，这套方案就先凑和用一下。",
     "expect": ["凑和"]},
    {"id": "typo-7", "dim": "错别字", "domain": "general",
     "text": "设备已于昨日完成按装调试，运行正常。",
     "expect": ["按装"]},
    {"id": "typo-8", "dim": "错别字", "domain": "general",
     "text": "他们一家三口去海南渡假一周。",
     "expect": ["渡假"]},
    {"id": "typo-9", "dim": "错别字", "domain": "general",
     "text": "医生检查发现他的脉膊十分微弱。",
     "expect": ["脉膊"]},
    {"id": "typo-10", "dim": "错别字", "domain": "general",
     "text": "做为项目负责人，他全程参与了方案评审。",
     "expect": ["做为"]},
    {"id": "typo-4", "dim": "易混词搭配", "domain": "general",
     "text": "宪法保障公民的基本权力，任何人不得非法侵犯。",
     "expect": ["基本权力"]},  # 确定性规则锚点（format_rules 混淆词搭配）
    {"id": "typo-5", "dim": "易混词搭配", "domain": "general",
     "text": "双方应依法明确彼此的权力和义务，并规范权力运行。",
     "expect": ["权力和义务"]},  # 同上；后半句「权力运行」为正确用法不得误报
    {"id": "conf-1", "dim": "易混词搭配", "domain": "general",
     "text": "要进一步扩大基层民主权力，保障人民权益。",
     "expect": ["民主权力"]},
    {"id": "conf-2", "dim": "易混词搭配", "domain": "general",
     "text": "当事人依法享有权力，同时承担相应义务。",
     "expect": ["享有权力"]},
    {"id": "conf-3", "dim": "易混词搭配", "domain": "general",
     "text": "全国人大是国家权利机关。",
     "expect": ["权利机关"]},

    # ---- 生造词（LLM 检出；2026-09-13 实测多轮稳定。PR#70 prompt 约束后
    #      建议质量已通顺（拍擦→拍打/擦拭），本组同时锁定检出与建议水位） ----
    {"id": "coin-1", "dim": "生造词", "domain": "general",
     "text": "他轻轻地拍擦着我的肩膀，动作很温柔。",
     "expect": ["拍擦"]},
    {"id": "coin-2", "dim": "生造词", "domain": "general",
     "text": "请把桌面拍擦干净再摆放物品。",
     "expect": ["拍擦"]},
    {"id": "coin-3", "dim": "生造词", "domain": "general",
     "text": "他对这件事的看法很执固。",
     "expect": ["执固"]},
    {"id": "coin-4", "dim": "生造词", "domain": "general",
     "text": "请把这份数据做成一个可视化的图表面。",
     "expect": ["图表面"]},
    {"id": "coin-5", "dim": "生造词", "domain": "general",
     "text": "他对下属的要求过于尖苛。",
     "expect": ["尖苛"]},

    # ---- 语法/搭配 ----
    {"id": "grammar-1", "dim": "语法", "domain": "general",
     "text": "通过这次培训，使全体干部的思想觉悟得到了显著提升。",
     "expect": []},  # 「通过…使…」缺主语，LLM 应报（锚点不定，人工验收项）
    {"id": "grammar-2", "dim": "语法", "domain": "general",
     "text": "本产品全国第一，遥遥领先于所有竞争对手，是行业内最最好的选择。",
     "expect": ["最最好"]},
    {"id": "grammar-3", "dim": "语法", "domain": "general",
     "text": "参加这次活动的有大约50人左右。",
     "expect": ["大约50人左右"]},  # 「大约…左右」语义重复
    {"id": "grammar-4", "dim": "语法", "domain": "general",
     "text": "只有刻苦训练，就能取得好成绩。",
     "expect": ["就能取得好成绩"]},  # 关联词误配（只有…才）
    {"id": "grammar-5", "dim": "语法", "domain": "general",
     "text": "我们讨论并听取了代表们的意见。",
     "expect": ["讨论并听取"]},  # 语序颠倒（应先听取后讨论）
    {"id": "grammar-6", "dim": "语法", "domain": "general",
     "text": "他的写作水平比去年明显改进了。",
     "expect": ["改进"]},  # 搭配不当（水平应「提高」）
    {"id": "grammar-7", "dim": "语法", "domain": "general",
     "text": "战士们冒着倾盆大雨和泥泞的小路前进。",
     "expect": ["泥泞的小路"]},  # 动宾搭配不当（「冒着」不能带「小路」）

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
    {"id": "logic-4", "dim": "逻辑", "domain": "general",
     "text": "《红楼梦》的作者是罗贯中。",
     "expect": ["罗贯中"]},
    {"id": "logic-5", "dim": "逻辑", "domain": "general",
     "text": "他2015年入职，2020年离职，在公司工作了十年。",
     "expect": ["十年"]},  # 年限算术矛盾（实为五年）
    {"id": "logic-6", "dim": "逻辑", "domain": "general",
     "text": "本店全年无休，每周一闭店休息。",
     "expect": ["每周一闭店休息"]},  # 前后矛盾（LLM 报在后一分句，锚点绑实测形态）
    {"id": "logic-7", "dim": "逻辑", "domain": "general",
     "text": "会议应到50人，实到40人，出勤率达到120%。",
     "expect": ["120%"]},  # 出勤率算术错误（应为80%）
    {"id": "logic-8", "dim": "逻辑", "domain": "general",
     "text": "长江是中国第一长河，黄河是中国第二长河，珠江也是中国第二长河。",
     "expect": ["珠江也是中国第二长河"]},  # 两个「第二」并列冲突

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
    {"id": "fmt-5", "dim": "格式", "domain": "general",
     "text": "登记的身份证号为110105194912310021。",
     "expect": ["110105194912310021"]},  # 校验位错误
    {"id": "fmt-6", "dim": "格式", "domain": "general",
     "text": "活动时间为2025.6-15，请提前安排。",
     "expect": ["2025.6-15"]},  # 日期分隔符混用
    {"id": "fmt-7", "dim": "格式", "domain": "general",
     "text": "工作安排：\n一、市场调研\n二、方案设计\n1. 需求分析\n2. 原型设计",
     "expect": ["1."]},  # 列表编号样式混用（中文/阿拉伯）

    # ---- 一致性（规则引擎应 100%） ----
    {"id": "cons-1", "dim": "一致性", "domain": "general",
     "text": "合同金额为贰佰万元整（￥200000元）。",
     "expect": ["贰佰万元整（￥200000元）"]},
    {"id": "cons-2", "dim": "一致性", "domain": "general",
     "text": "本方案第一条为总体要求，第二条为实施步骤，第三条为保障措施。",
     "expect": []},  # 编号连续，不得报断档
    {"id": "cons-3", "dim": "一致性", "domain": "general",
     "text": "杭州国电南自自动化有限公司负责实施。项目由国电南自统筹，杭州国电南自自动化有限公司验收。",
     "expect": ["国电南自"]},
    {"id": "cons-4", "dim": "一致性", "domain": "general",
     "text": "违约金为拾万元整（￥50000元）。",
     "expect": ["拾万元整（￥50000元）"]},  # 大小写金额不一致（拾万≠5万）
    {"id": "cons-5", "dim": "一致性", "domain": "general",
     "text": "本协议第一条为总则，第二条为权利义务，第四条为违约责任。",
     "expect": ["第四条"]},  # 编号断档（缺第三条）
    {"id": "cons-6", "dim": "一致性", "domain": "general",
     "text": "北京华宇信息技术有限公司负责系统开发，华宇信息技术提供了运维支持，北京华宇信息技术有限公司组织终验。",
     "expect": ["华宇信息技术"]},  # 全称/简称混用

    # ---- 敏感词（词库引擎应 100%） ----
    {"id": "sens-1", "dim": "敏感词", "domain": "general",
     "text": "该组织企图颠覆国家政权，已被依法处理。",
     "expect": ["颠覆国家政权"]},
    {"id": "sens-2", "dim": "敏感词", "domain": "general",
     "text": "该团伙长期从事非法集资活动，涉案金额巨大。",
     "expect": ["非法集资"]},
    {"id": "sens-3", "dim": "敏感词", "domain": "general",
     "text": "巡查发现多个赌博网站链接，已上报处置。",
     "expect": ["赌博网站"]},
    {"id": "sens-4", "dim": "敏感词", "domain": "general",
     "text": "该账号发布的内容含有种族歧视言论。",
     "expect": ["种族歧视"]},
    {"id": "sens-5", "dim": "敏感词", "domain": "general",
     "text": "警方成功捣毁一个制造爆炸物的窝点。",
     "expect": ["制造爆炸"]},
    {"id": "sens-6", "dim": "敏感词", "domain": "general",
     "text": "弹幕里刷满了牛逼、卧槽等不文明词汇，需要清理。",
     "expect": ["牛逼", "卧槽"]},  # 禁词（banned）命中

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

    # ---- 标点规范（半角混用由 format_rules 确定性检出，其余 LLM） ----
    {"id": "punct-1", "dim": "标点", "domain": "general",
     "text": "本项目包括三个阶段:前期准备,中期实施,后期验收。",
     "expect": [":", ","]},  # 半角冒号+逗号（确定性规则锚点）
    {"id": "punct-2", "dim": "标点", "domain": "general",
     "text": "各阶段需提交\"总结报告\"。",
     "expect": ["总结报告"]},
    {"id": "punct-3", "dim": "标点", "domain": "general",
     "text": "他说了很多，然后。。。就走了。",
     "expect": ["。。。"]},  # 省略号规范（应为……）
    {"id": "punct-4", "dim": "标点", "domain": "general",
     "text": "本次培训共有三个环节:签到、听课、考核。",
     "expect": [":"]},  # 英文冒号应为中文全角（mini 稳定漏检，确定性规则补位）
    {"id": "punct-5", "dim": "标点", "domain": "general",
     "text": "采购清单：电脑、打印机，复印机、扫描仪。",
     "expect": ["打印机，复印机"]},  # 并列项间逗号应为顿号
    {"id": "punct-6", "dim": "标点", "domain": "general",
     "text": "看到这个结果，所有人都惊呆了!!!",
     "expect": ["!!!"]},  # 连续英文叹号应为中文叹号（确定性规则锚点）

    # ---- 数字体例（GB/T 15835） ----
    {"id": "num-1", "dim": "数字体例", "domain": "general",
     "text": "参加会议的共有五十八人，其中15人来自总部，三十二人来自分支机构。",
     "expect": ["五十八"]},
    {"id": "num-2", "dim": "数字体例", "domain": "general",
     "text": "参加培训的有12人，另有二十一人在线学习。",
     "expect": ["二十一人"]},
    {"id": "num-3", "dim": "数字体例", "domain": "general",
     "text": "该项目分两期建设：第一期投资1.2亿元，第二期投资八千万元。",
     "expect": ["八千万元"]},
    {"id": "num-4", "dim": "数字体例", "domain": "general",
     "text": "训练集包含五万条样本，测试集包含5000条样本。",
     "expect": ["五万条"]},  # 同句同框架混用（跨框架混用如「成立X年/第Y个年头」mini 检出不稳）
    {"id": "num-5", "dim": "数字体例", "domain": "general",
     "text": "本次评选共评出一等奖3项、二等奖十二项、三等奖两项。",
     "expect": ["十二项", "两项"]},

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
    {"id": "sum-4", "dim": "金额加总", "domain": "general",
     "text": "差旅费2000元，餐费1500元，住宿费2500元，总计5000元。",
     "expect": ["总计5000元"]},  # 6000≠5000，应报
    {"id": "sum-5", "dim": "金额加总", "domain": "general",
     "text": "装修费用合计8万元：地板3万元，涂料2万元，人工3万元。",
     "expect": []},  # 总额在前明细在后（向后不核验，不得误报）

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
    {"id": "clean-10", "dim": "零误报", "domain": "general",
     "text": "项目总投资3000万元，其中设备费1200万元，材料费800万元，人工费1000万元，合计3000万元。",
     "expect": []},  # 总额句+明细+合计的正确文本（锁定加总防误报）
    {"id": "clean-11", "dim": "零误报", "domain": "general",
     "text": "该方案经董事会审议通过后组织实施。",
     "expect": []},
    {"id": "clean-12", "dim": "零误报", "domain": "general",
     "text": "人民代表大会依法行使权力，公民的基本权利受法律保障。",
     "expect": []},  # 权力/权利双词正确用法（混淆词规则防误报陷阱）
    {"id": "clean-13", "dim": "零误报", "domain": "general",
     "text": "该线路全长3.5公里，共设12座车站，平均站间距约800米，最高时速100公里。",
     "expect": []},
]
