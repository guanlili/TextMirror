# 更新日志

本项目所有显著变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。项目暂未发布正式版本号，已合并变更按日期里程碑分组；开发分支内容列入未发布区，不代表已部署。

## [Unreleased]

暂无待发布条目。以下为已合并里程碑，不代表具体环境已部署或完成验收。

## [2026-09-22]

### 修复
- 操作审计列表改为查询时生成截断预览，避免异步会话访问延迟加载正文引发 500；保留全文搜索、分页与详情读取。([#111](https://github.com/guanlili/TextMirror/pull/111))

## [2026-09-21]

### 新增
- 文章级质量评测 `python -m eval.articles`：显式固定模型配置、重复采集、保存配置与源码指纹，支持离线重评分；私有文章及逐条结果不随代码提交。([#110](https://github.com/guanlili/TextMirror/pull/110))

### 修复与可靠性
- 修复审校自检与替换建议安全边界；检测结果和建议质量分别评估，不把失败、超时或未完成当作零错误。([#110](https://github.com/guanlili/TextMirror/pull/110))
- 合入 P0–P2 优化，包括用量查询索引、接口安全头、容器资源限制、备份脚本、登录令牌刷新与限流等，并修复索引迁移兼容问题。([#109](https://github.com/guanlili/TextMirror/pull/109))
- 测试同步 Redis 客户端也使用隔离的 fakeredis，避免共享分片缓存污染用例。([#106](https://github.com/guanlili/TextMirror/pull/106))

## [2026-09-18]

### 新增
- 独立事实核查工作台：文本、文档与审校记录入口，事实项确认、单条深查、人工复核、HTML / JSON 导出及材料清理；独立 `fact-check-worker` 消费核查队列。新增工作台迁移，升级须同步 API、两个 worker 和前端。([#103](https://github.com/guanlili/TextMirror/pull/103))
- 扩充审校评测集，减少事实核查进度回调重复校验，补材料清理并发锁，并按事实项独立统计检索成功次数；评测结果依赖样例与模型，不代表普遍准确率。([#105](https://github.com/guanlili/TextMirror/pull/105))

### 修复
- 修复工作台合并后的 CI 回归与分片内容丢失。([#104](https://github.com/guanlili/TextMirror/pull/104))

## [2026-09-16]

### 性能与工程化
- 释放 SSE 长连接持有的资源，约束文档任务执行预算；普通 worker 改用 prefork 池。([#101](https://github.com/guanlili/TextMirror/pull/101))
- 更新前后端依赖及类型适配，保留 Celery 与 Redis 客户端兼容性。([#97](https://github.com/guanlili/TextMirror/pull/97)–[#100](https://github.com/guanlili/TextMirror/pull/100))
- 精简中英文 README 与使用、运维、设计文档，纠正运行环境、默认 HTTP、额度及文件导出边界。([#95](https://github.com/guanlili/TextMirror/pull/95))

## [2026-09-15]

### 新增
- 审阅交付工作区：可靠定位与仅采纳项导出、手动草稿、最多 20 个版本、版本对比、角色协作及历史继续审阅。
- 质量反馈人工审核与目标评测，独立核对检测结果和认可 / 禁止替换；不把未评估的建议算作通过。
- 可选事实核查：模型原生联网或 Tavily，初始与反证检索、正文逐字引文、主体 / 时间 / 统计口径检查和可复核上下文；默认关闭，不自动采纳。
- 按实际模型请求记录用量账本，覆盖重试、自检、润色、评测、协作和原生搜索；未知用量不冒充零消耗，不回填历史估算。

### 修复
- 文本和文档首次完成绑定审阅地址；历史区分完整、部分完成、未记录覆盖，避免把未审完当作无问题。
- 普通文档按原扣费日与任务幂等标记退款，补齐排队取消和终态补偿；无原预扣凭据的旧任务不猜测退款。

本节功能对应 [PR #84](https://github.com/guanlili/TextMirror/pull/84)，其五个数据库迁移涵盖审阅、质量反馈、事实核查配置及任务、搜索服务选择、用量账本；不是后续版本的完整迁移清单。升级前需备份并同步更新 API、worker 和前端；目标版本的完整迁移链与步骤见 [运维手册](系统运维操作手册.md)。事实核查仍需在实际部署中启用并完成真实联网验收。

## [2026-09-13]

### 性能
- **审校悬停联动解耦全文重算**：高亮不再依赖悬停索引（此前每次 mouseenter 触发整篇 escape+替换+DOMPurify，长文档必卡），改 watcher 局部切换 mark 类；高亮两阶段占位替换（长原文优先）修复多处漏高亮与 mark 嵌套。([#76](https://github.com/guanlili/TextMirror/pull/76))

### 修复
- **重复错词只改首处**：接受/撤销/批量操作统一 replaceAll，同一错词多处出现全部修正（此前 UI 提示完成但正文残留）；重复上报的同原文同建议问题一并核销；SSE 断流提示结果可能不完整；词库/放行词列表 200 条截断改分页加载更多。([#76](https://github.com/guanlili/TextMirror/pull/76))
- **配额/限流原子化**：登录用户配额从 check-then-write（读 DB 记录数、校对完成才落库，竞态窗口=整个 LLM 调用，并发可全部越过上限）改为 Redis 原子预扣+失败退还；游客限流 INCR 先行（并发不再全部放行）且窗口对齐上海自然日；被拒请求不虚增计数（当日上调配额立即生效）；游客模式关闭后文档校对/异步提交入口补 403 拒绝。([#75](https://github.com/guanlili/TextMirror/pull/75))
- **审计统计时区对齐**：「今日」从 UTC 会话时区切日（每天 8 小时错位）改为 Asia/Shanghai 业务日；仪表盘趋势与开放 API 用量的逐日循环查询合并为单条 SQL；批量放行词补 1000 条上限并去除逐词查重 N+1；审计日志后台任务持强引用防 GC；`uploaded_documents.created_at` 加索引。([#77](https://github.com/guanlili/TextMirror/pull/77))
- **半角标点确定性规则**：冒号/分号前邻汉字即报（LLM 稳定漏检项 100% 补位）；生造词评测锚点锁定检出+建议水位。([#74](https://github.com/guanlili/TextMirror/pull/74))

### 新增
- 模型列表 60s 缓存（文本校对/文档校对/润色三页共用，管理端改配置即失效）。([#76](https://github.com/guanlili/TextMirror/pull/76))

## [2026-09-11]

### 新增
- **管理员全量 API 密钥管理页**：跨用户列表（分页/关键词/状态筛选）、吊销/恢复（恢复校验单用户活跃上限）、调配额、改备注。([#53](https://github.com/guanlili/TextMirror/pull/53))
- **API 用量统计**：校对记录归属到调用密钥（`proofread_records.api_key_id`）；`GET /open/usage` 按日/按密钥聚合（近 N 天）；密钥页新增「近7日」列。([#55](https://github.com/guanlili/TextMirror/pull/55))
- **AI 润色开放端点**：`POST /open/polish`（三版本并发）与 `POST /open/polish/stream`（SSE 流式），计费与审校同口径。([#56](https://github.com/guanlili/TextMirror/pull/56))
- **异步任务完成回调（Webhook）**：任务完成/失败向密钥配置的地址推送签名通知（HMAC-SHA256 验签、SSRF 防护、指数退避重试、测试推送）。([#57](https://github.com/guanlili/TextMirror/pull/57))
- **首次部署向导最小版**：容器首启自动初始化种子数据；生产模式管理员初始密码改为随机生成（日志 + 落盘）；首登引导修改密码。([#58](https://github.com/guanlili/TextMirror/pull/58))
- **易混词搭配规则**：权力/权利等高混淆对的确定性兜底（5 条搭配级规则，零 token 100% 召回）。([#60](https://github.com/guanlili/TextMirror/pull/60))
- **Webhook 投递可见性 + 密钥页回调管理界面**：投递状态写 Redis（最近状态 + 最近 20 次明细）；`/api-keys` 列表透出 `webhook_last`；补齐回调设置/测试/清除的完整 UI。([#63](https://github.com/guanlili/TextMirror/pull/63))

### 修复
- **对比配额真实计量**：多模型对比此前只预检不落库，配额实际不消耗（可无限对比）；改为按成功模型数落 `quota_weight` 加权记录，配额/用量/dashboard 统一切换 SUM 口径。([#62](https://github.com/guanlili/TextMirror/pull/62))
- **对比历史问题去重**：对比记录的问题按（原文+建议）去重——同一错误被多个模型发现不再重复出现，`found_by` 标注发现模型（多模型共识可见）。

### 工程化
- `open.py` 模块拆分（1118 行 → open/open_polish/open_usage/open_documents/open_common 五模块）。([#61](https://github.com/guanlili/TextMirror/pull/61))
- CI 新增 Docker 镜像构建 job（buildx + gha 缓存）——此前 CI 不验证 Dockerfile 路径，vite7 升级时镜像构建崩了但 PR 全绿。([#64](https://github.com/guanlili/TextMirror/pull/64))
- 前端测试基建（vitest）：format 工具与 user store 单测，结束前端零测试状态。([#64](https://github.com/guanlili/TextMirror/pull/64))
- pre-commit 新增 `detect-private-key` hook。([#65](https://github.com/guanlili/TextMirror/pull/65))

### 文档
- 新增 CHANGELOG.md 与英文 README（README.en.md，与中文版互链）。([#54](https://github.com/guanlili/TextMirror/pull/54))

## [2026-09-10]

### 安全
- **密码变更即刻失效所有旧登录凭证**：`users.password_changed_at` + JWT `iat` 校验闭环，改密后旧 Access/Refresh Token 立即拒绝（秒粒度比较，避免同秒登录被误杀）；Access Token 有效期 7 天 → 1 小时（Refresh 自动续期）。([#49](https://github.com/guanlili/TextMirror/pull/49))

### 文档
- 运维手册 / 使用手册 / README 准确性修正（LLM Key 配置路径、忘记密码流程、供应商数等勘误）。([#50](https://github.com/guanlili/TextMirror/pull/50)–[#52](https://github.com/guanlili/TextMirror/pull/52))

## [2026-09-09]

### 新增
- **审校深度三档**：快查（纯规则引擎，秒回零成本）/ 标准（AI 关思考模式，秒级）/ 深度（AI 开思考 + 强制二次自检）；思考参数按供应商方言自动适配（火山方舟 / 通义千问已验证）。([#47](https://github.com/guanlili/TextMirror/pull/47))
- **Prompt 误报抑制**：四条显式禁报规则（专有名词不改写、同义词润色不报、行业惯用写法按原文、正常省略结构），标准/深度档零误报率显著提升。([#48](https://github.com/guanlili/TextMirror/pull/48))
- **分片进度实时推送**：异步文档审校进度按分片粒度上报（原 10% → 80% 黑洞消除）。([#48](https://github.com/guanlili/TextMirror/pull/48))

### 修复
- 润色多模型对比的中止竞态（compareAbort）与流式中止。([#37](https://github.com/guanlili/TextMirror/pull/37) / [#20](https://github.com/guanlili/TextMirror/pull/20))
- 后端健壮性与前端类型安全第二轮修复：LIKE 通配符注入转义、FK/status 索引、启动期错误、弃用 API 清理、前端防抖与 404 路由等。([#45](https://github.com/guanlili/TextMirror/pull/45))
- **安全加固**：数据库错误信息泄露、IP 伪造（代理头信任）、CORS 收窄、N+1 查询、前端 401 并发刷新去重、日志轮转、弱密钥启动守卫。([#43](https://github.com/guanlili/TextMirror/pull/43))

### 依赖与工具链
- 12 项后端依赖批量升级 + pytest/pytest-asyncio 1.4；前端 vue-router 5 / pinia 4 / vite 7 / Node 22 整链升级；14 个 dependabot PR 清零。([#38](https://github.com/guanlili/TextMirror/pull/38)–[#40](https://github.com/guanlili/TextMirror/pull/40))
- 前端 Dockerfile 基础镜像升级 node:22-alpine（vite 7 构建兼容）；.gitignore 去重。([#41](https://github.com/guanlili/TextMirror/pull/41) / [#42](https://github.com/guanlili/TextMirror/pull/42))

## [2026-09-08]

### 新增
- **测试与 CI 基建**：pytest 冒烟 + 单元测试、GitHub Actions（ruff / pytest / eslint / vue-tsc / build）、pre-commit、dependabot。([#15](https://github.com/guanlili/TextMirror/pull/15) / [#22](https://github.com/guanlili/TextMirror/pull/22))

### 修复
- **异步审校任务可靠性**：任务持久化、可取消、失败退款移至 `on_failure`、幂等重投递前重置状态（修复重试永远不执行的死任务）。([#13](https://github.com/guanlili/TextMirror/pull/13) / [#14](https://github.com/guanlili/TextMirror/pull/14) / [#21](https://github.com/guanlili/TextMirror/pull/21))
- 上传安全与生命周期：流式落盘 + 内容校验、频率限制、存储泄漏回收、文档归属判定收紧、预览 HTML 属性注入阻断、有漏洞依赖升级。([#12](https://github.com/guanlili/TextMirror/pull/12))

### 性能与重构
- LLM 客户端复用（SSE 会话复用、管理端角色缓存、Celery 同步引擎按子进程缓存）。([#16](https://github.com/guanlili/TextMirror/pull/16))
- 前后端去重（formatSize 等重复实现合并）；Dockerfile 与文档清理。([#17](https://github.com/guanlili/TextMirror/pull/17)–[#19](https://github.com/guanlili/TextMirror/pull/19))

## [2026-09-07]

### 新增
- **跨片一致性检查器**（纯规则）：金额括号并注一致、全称简称混用、编号断档、金额加总核验。([#5](https://github.com/guanlili/TextMirror/pull/5))
- **格式规则引擎**：日期合法性（含闰年边界）、电话位数、身份证校验位、金额量级冗余、编号样式混用。
- **LLM 幻觉自校验**：逐字核验 + 滑窗模糊对齐，定位失败降级。
- **审校评测集**：32 固定样本（召回 100% / 零误报 / 幻觉 0 基线）。
- 高危文本二次自检；领域自动识别（domain=auto 特征词路由）。
- 润色敏感词扫描（三版本警示条）；分片重叠窗口（跨切点指代可见）。([#5](https://github.com/guanlili/TextMirror/pull/5))
- **性能**：Element Plus 按需引入（产物 -35%）、文档解析异步化、词库三表补索引。([#10](https://github.com/guanlili/TextMirror/pull/10))

### 修复
- 敏感词交互链：删除该词按钮（建议文字误插正文）、撤销失效（删除类走不进恢复逻辑）、一键修改全部与单条语义对齐。([#7](https://github.com/guanlili/TextMirror/pull/7))
- 高危缺陷五处：迁移不进镜像、游客拦截失效、弱默认密钥、禁用账号降级、任务先投递后校验。([#8](https://github.com/guanlili/TextMirror/pull/8))
- **游客策略真正生效**：日限/文本长度/上传开关接入执行点，删除无执行点的用户默认策略。([#11](https://github.com/guanlili/TextMirror/pull/11))

## [2026-09-05]

### 新增
- **开放 API 平台**：API 密钥（SHA-256 哈希存储、RPM + 日配额双层限流、失败退额度）、5 端点（文本审校 / 多模型对比 / 异步文档 / 任务状态 / 模型列表）、统一错误契约 `{code, message}`、Swagger 文档常开。([#2](https://github.com/guanlili/TextMirror/pull/2)–[#4](https://github.com/guanlili/TextMirror/pull/4))
- **确定性词库引擎**：敏感词/纠错词 100% 召回零 token；三层词库（全局/个性化/放行词）；命中来源标识；反馈飞轮（优化建议看板一键采纳）；领域规则后台化。([#4](https://github.com/guanlili/TextMirror/pull/4))
- 白标化第一步：平台名称/标语/页脚/游客模式进站点配置；审计日志 90 天自动清理。

## [2026-09-03]

### 初始版本
- 项目初始化（基于 TextGuard，MIT 协议）。
- LLM 供应商改多供应商动态配置（16 家适配），移除 DeepSeek 专用集成。([#1](https://github.com/guanlili/TextMirror/pull/1))
- API Key 加密存储与多模型对比配额预检。
