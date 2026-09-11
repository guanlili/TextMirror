# 更新日志

本项目所有显著变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。项目暂未发布正式版本号，按合并日期里程碑分组。

## [2026-09-11]

### 新增
- **管理员全量 API 密钥管理页**：跨用户列表（分页/关键词/状态筛选）、吊销/恢复（恢复校验单用户活跃上限）、调配额、改备注。([#53](https://github.com/guanlili/TextMirror/pull/53))
- **API 用量统计**：校对记录归属到调用密钥（`proofread_records.api_key_id`）；`GET /open/usage` 按日/按密钥聚合（近 N 天）；密钥页新增「近7日」列。([#55](https://github.com/guanlili/TextMirror/pull/55))
- **AI 润色开放端点**：`POST /open/polish`（三版本并发）与 `POST /open/polish/stream`（SSE 流式），计费与审校同口径。([#56](https://github.com/guanlili/TextMirror/pull/56))
- **异步任务完成回调（Webhook）**：任务完成/失败向密钥配置的地址推送签名通知（HMAC-SHA256 验签、SSRF 防护、指数退避重试、测试推送）。([#57](https://github.com/guanlili/TextMirror/pull/57))
- **首次部署向导最小版**：容器首启自动初始化种子数据；生产模式管理员初始密码改为随机生成（日志 + 落盘）；首登引导修改密码。([#58](https://github.com/guanlili/TextMirror/pull/58))

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
