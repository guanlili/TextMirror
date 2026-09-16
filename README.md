<div align="center">

# TextMirror - 智能文档审校平台

**主打审校，润色辅助：发现问题、人工采纳、保存版本，再交付修订稿。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Vue 3](https://img.shields.io/badge/Vue-3.x-4FC08D.svg)](https://vuejs.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)

简体中文 | [English](README.en.md)

</div>

TextMirror 是可私有化部署的开源审校平台，适合日常文稿、公文和法律文本。规则引擎与可配置的大模型共同发现问题，最终是否修改由用户决定。

## 核心能力

| 能力 | 说明 |
|------|------|
| 文本与文档审校 | 文本粘贴，DOC / DOCX / PDF / TXT 上传，长文本分片与任务进度 |
| 三档深度 | 快查仅运行规则；标准使用 AI；深度增加思考与二次自检，实际效果和耗时依赖模型 |
| 多模型对比与角色协作 | 2–4 个模型横向对比；协作模式串联规则、语言、一致性和争议复核，并非多人在线编辑 |
| 人工审阅与交付 | 按位置采纳、忽略、撤销；修订预览；手动保存草稿、最多 20 个版本、版本对比与历史继续审阅 |
| 覆盖状态 | 区分完整、部分完成和未记录覆盖；未审完不能视为没有问题 |
| 词库与行业规则 | 全局词库、个人纠错、放行词；领域规则后台维护；用户反馈辅助词库运营 |
| 质量反馈与评测 | 人工审核反馈样例，独立统计是否检出、替换建议是否符合认可或禁止的改法 |
| 事实核查（可选） | 联网搜索 / 可信信源两种模式，初始与反证检索、逐字引文和证据上下文；默认关闭 |
| AI 润色 | 10 种风格，轻量 / 标准 / 深度三个版本，支持流式输出与多模型对比 |
| 管理与集成 | RBAC、用户及游客配额、品牌设置、可选飞书登录、API 密钥、Webhook、实际模型调用账本 |

### 使用边界

- **AI 建议不是正确性保证**。保存、导出和任务成功不代表全文无误；需要检查覆盖范围并人工复核。
- **预览不等于全部内容已审校**。DOCX 当前提取正文段落，表格等非正文内容不在该提取范围；PDF 只提取文本，不做 OCR，最多 100 页。上传默认上限 20MB。
- 文档页支持修订 TXT、问题报告 TXT 和 Word 导出。仅来源为 DOCX 时尝试保留排版回写；无法安全应用的修改会拒绝导出，可改用 TXT。文本页导出全文或问题报告 TXT。
- 登录记录可刷新恢复，**采纳决定仍需手动保存草稿**；游客审阅只保留在页面内存中。
- 事实核查两种模式都会向外部服务发送待核查内容；可信信源限制可采纳证据，不是离线或封闭检索。结论只供参考，不自动采纳。
- 快查没有 AI Token 成本，但仍受平台配额和限流约束。模型调用账本记录已知用量，不回填历史估算，也不等于供应商费用账单。

## 本地启动（Docker 推荐）

需要 Docker Engine / Docker Desktop 及 Compose 插件。以下为开发环境，不要直接用于公网部署。

```bash
git clone https://github.com/guanlili/TextMirror.git
cd TextMirror
cp backend/.env.example backend/.env
```

编辑 `backend/.env`，将对应配置改为开发容器地址；模板中的 `localhost` 不能用于容器间连接：

```dotenv
DEBUG=true
DATABASE_URL=postgresql+asyncpg://postgres:textmirror@postgres:5432/textmirror
REDIS_HOST=redis
REDIS_PASSWORD=
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
```

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml ps
```

后端首启自动建表、执行迁移并初始化种子数据，无需手动 `stamp`。

| 入口 | 地址 / 说明 |
|------|-------------|
| 用户端与管理后台 | http://localhost:3022 |
| 后端接口文档 | http://localhost:3020/docs（DEBUG 模式） |
| 开放接口文档 | http://localhost:3022/api/v1/open/docs |
| 开发管理员 | `admin / admin123`，仅用于 DEBUG 开发环境 |

登录管理后台「大模型配置」，填写真实供应商密钥并激活模型；模型密钥不在 `.env` 中设置。当前快查也会加载模型配置，但不发起模型请求。

开发 Compose 仅后端 API 热加载：前端变更需重新构建 `frontend`，worker 代码变更需空闲后重启 `celery-worker`。不要用重建或清空数据库解决迁移问题。

## 生产部署

Linux 服务器使用 [docker-compose.yml](docker-compose.yml)，默认通过 **HTTP 3022** 提供服务；它与 macOS / Windows 开发用的桥接网络配置不同。

生产需配置根目录 `.env` 和 `backend/.env.production`：强数据库及 Redis 密码、`DEBUG=false`、独立应用/JWT 密钥、上传持久化路径与访问域名。生产管理员首启使用随机密码，不是开发默认密码。**公网开放前必须关闭默认启用的一键登录**，该入口可免密登录管理员；随机密码及 `DEBUG=false` 不会关闭它。

按 [系统运维操作手册](系统运维操作手册.md) 完成配置、迁移、备份与验收后再对外开放。HTTPS 需额外配置 TLS 终止代理，或证书挂载、SSL 配置和健康检查；不是放入证书后自动启用。

## 开放 API

在网页「API 密钥」创建密钥，使用 `Authorization: Bearer tm_…`。完整参数、枚举与在线调试以 `/api/v1/open/docs` 为准。

支持文本审校、多模型对比、异步文档与任务轮询、模型列表、用量统计、润色及流式润色；异步文档可配置签名 Webhook。集成示例和额度边界见 [用户使用手册](用户使用手册.md)。

## 技术栈与目录

- 后端：Python **3.11+**、FastAPI、SQLAlchemy、Alembic、Celery。
- 前端：Vue 3、TypeScript、Element Plus、Pinia、Vite；开发与 CI 使用 Node.js 22。
- 存储：PostgreSQL 16、Redis 7；Compose 包含前端、API、worker、数据库和缓存五个服务。
- `backend/app/`：API、业务服务、模型与任务；`backend/alembic/`：数据库迁移；`backend/tests/`：后端测试。
- `frontend/src/`：页面、组件、API 客户端及测试；`backend/eval/`：审校评测工具。

## 文档

| 文档 | 用途 |
|------|------|
| [用户使用手册](用户使用手册.md) | 审校、审阅交付、事实核查、质量反馈及开放接口 |
| [系统运维操作手册](系统运维操作手册.md) | 开发环境、生产配置、更新、备份、回滚与排障 |
| [系统功能设计文档](系统功能设计文档.md) | 模块职责、数据流与关键一致性约定 |
| [贡献指南](CONTRIBUTING.md) | 开发验证与 PR 流程 |
| [更新日志](CHANGELOG.md) | 已合并里程碑及未发布变更 |

## 贡献与许可

欢迎通过 Issue 和 Pull Request 参与；代码、测试和文档变更均走分支与 PR 审核。

本项目采用 [MIT License](LICENSE)，基于 TextGuard 开发，保留原项目许可及版权声明。
