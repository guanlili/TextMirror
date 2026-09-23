# TextMirror 项目周报

**周期：** 2026 年 9 月 15 日 — 9 月 21 日

**本周概览：** 合并 PR 21 个，提交 59 次。完成事实核查独立工作台上线、审阅交付闭环完善、评测集扩容至 178 条；完成前后端依赖批量升级；提出并推进三级优化方案（P0/P1/P2，共 28 项），其中 P0 迁移脚本修复已就绪，P1/P2 待 CI 通过后合并。

---

## 一、功能迭代

### 1.1 事实核查独立工作台（PR #103）

新增独立的事实核查工作台，与审校工作台分离，提供专项事实核验体验。同步完成全站安全审计修复。上线后发现 CI 回归与分片内容丢失问题，已通过 PR #104 紧急修复。

### 1.2 审阅交付闭环（PR #84）

完善审阅交付流程，增加质量核验环节与调用账本功能，确保审校结果可追溯、可审计。

### 1.3 评测集扩容与事实核查修复（PR #105）

评测集从 150 条扩容至 178 条，覆盖更多边界场景。同步修复事实核查三项问题，提升核查准确率。

### 1.4 易混词搭配规则（PR #102）

新增「登陆→登录」易混词搭配规则，持续丰富审校规则库。

---

## 二、性能优化

### 2.1 SSE 连接释放与任务预算约束（PR #101）

释放长时间占用的 SSE 连接，约束文档处理任务的执行时间预算，避免 worker 被单任务独占导致其他请求排队。

---

## 三、依赖升级与 CI 修复

### 3.1 前后端依赖批量升级（PR #85–#100）

完成两轮依赖升级，涉及 14 个依赖项：

**后端：** pydantic 2.10→2.13、pydantic-settings 2.7→2.15、alembic 1.14→1.20、asyncpg 0.30→0.31、pytest 8.4→9.1、redis-py 升级至 6.4（保留 Celery 兼容性）、PyMuPDF 升级至 1.28.2 并补充真实 PDF 回归测试。

**前端：** element-plus 2.14.0→2.14.5、vue-tsc 2.2→3.3、sass 1.104.0→1.104.1、marked 18.0.12→18.0.13、unplugin-vue-components 升级至 32.1.0。升级后暴露的模板类型错误已全部修复。

### 3.2 CI 修复（PR #96、#104）

修复 PR #84 遗留的 ruff I001 导入排序问题，恢复 main 分支 CI 绿灯。修复 PR #103 事实核查工作台引入的 CI 回归与分片内容丢失。

### 3.3 文档精简（PR #95）

精简 README 与三份运维手册，补录遗漏的未发布条目。

---

## 四、三级优化方案（进行中）

基于全面审查，提出 28 项优化，按紧急程度分三级推进：

### P0 — 五项关键优化（PR #107）

| 项目 | 内容 |
|------|------|
| LLMUsage 索引 | 添加 config_id、business、(config_id, created_at) 三个索引，Dashboard 查询从全表扫描变为索引查找 |
| Nginx 安全头 | HTTP/SSL 配置同步添加 Content-Security-Policy 与 Permissions-Policy |
| 容器资源限制 | 全服务添加 mem_limit/cpus，防止 Celery worker OOM 影响 PostgreSQL |
| 数据库备份 | 新增 backup_db.sh 脚本，配合 crontab 定时 pg_dump，默认保留 7 天 |
| Token 刷新修复 | 修复 _retried 标志从未设置导致的无限重试问题 |

### P1 — 八项优化（PR #108）

| 项目 | 内容 |
|------|------|
| LIKE 查询转义 | 防止通配符注入（dictionary/whitelist/global_dict） |
| Nginx 限流 | API 10r/s，登录接口 5r/min |
| Redis 淘汰策略 | noeviction → allkeys-lru |
| 定时任务优化 | 条件查询 + 分批删除，避免全表扫描和长事务 |
| 时区统一 | RPM 限流窗口统一 Asia/Shanghai |
| 测试改进 | client fixture 结束后清理所有表，防止用例间数据泄漏 |
| CI 增强 | 添加 pytest-cov 覆盖率上报 Codecov |
| 前端 healthcheck | docker-compose 补全前端健康检查 |

### P2 — 十五项中期优化（PR #109）

涵盖后端可靠性（Celery autoretry、SSE session 复用、on_failure 异常拆分）、后端性能（审计 defer 重字段、GROUP BY 时间界、model-usage LIMIT、审计日志截断）、前端优化（site store loaded 修复、usage 节流、剪贴板降级、趋势图错误态）、基础设施（deploy.sh 健康检查、PG statement_timeout、SSL OCSP stapling）四个维度。

**当前状态：** P0 迁移脚本修复已完成（b89e901），三个 PR 待 CI 全量通过后合并。

---

## 五、下周计划

1. **合并三级优化 PR：** 待 CI 全量通过后依次合并 P0→P1→P2，部署至生产环境验证。
2. **生产验证：** 验证 Dashboard 索引查询性能、Nginx 限流生效、Token 刷新流程、备份脚本执行。
3. **事实核查持续打磨：** 基于评测集扩容后的数据，继续优化事实核查准确率。
4. **审校规则库扩充：** 根据用户反馈持续补充易混词搭配规则。
