# 贡献指南

感谢你对 TextMirror 的关注！我们欢迎任何形式的贡献。

## 如何贡献

### 报告 Bug

- 在 Issues 中搜索是否已有类似问题
- 使用 Bug Report 模板创建新 Issue
- 提供复现步骤、预期行为和实际行为

### 提交功能建议

- 在 Issues 中描述你的需求场景
- 说明期望的功能表现
- 如果可能，提供设计思路

### 提交代码

1. Fork 项目到你的仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 编写代码并确保通过测试
4. 提交变更：`git commit -m 'feat: add your feature'`
5. 推送分支：`git push origin feature/your-feature`
6. 创建 Pull Request

### Commit 规范

采用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

- `feat:` 新功能
- `fix:` 修复 Bug
- `docs:` 文档更新
- `style:` 代码格式（不影响逻辑）
- `refactor:` 重构
- `test:` 测试
- `chore:` 构建/工具变更

## 开发与验证

服务启动见 [README.md](README.md)；测试环境使用 Python 3.11+、Node.js 22.x（至少 22.13，满足锁定依赖要求）。以下命令分别在对应目录执行，与 [CI](.github/workflows/ci.yml) 保持一致。

后端（建议先创建并激活虚拟环境）：

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
ruff check .
python -m pytest tests/ -q
```

前端（另一个终端，从仓库根目录开始）：

```bash
cd frontend
npm ci
npm run lint
npx vue-tsc --noEmit
npm run test
npm run build
```

后端单元测试主要使用 SQLite、fakeredis 和模拟模型，不需要真实供应商密钥。缺少可选 Lua 运行依赖时部分测试可能跳过，请检查 skip 原因；PostgreSQL 方言、真实 Redis 并发和外部模型行为需另外验证。

- UI 改动要启动本地服务，实际操作正常流程与失败边界；类型检查和构建成功不能替代页面验收。
- 涉及镜像或依赖时验证 Docker 构建；数据模型变更须提交 Alembic 迁移，不用 `stamp` 代替升级。
- `python -m eval.eval` 是真实模型评测，不是离线单元测试；运行前确认使用的配置、样例可对外发送及费用授权。
- PR 中注明测试结果、跳过项和未验收范围；不提交 `.env`、密钥、真实敏感样例或本地数据。
- 代码、测试、文档和维护改动均使用分支 + PR 审核，不直接推送 `main`。

### 文章级评测

除固定集 `python -m eval.eval` 外，`python -m eval.articles` 支持整篇文章评测与离线重评分。在 `backend` 目录执行 `python -m eval.articles --help` 查看参数；输入 JSON 可参考 [article_examples.json](backend/eval/article_examples.json)，须符合 [article_metrics.py](backend/eval/article_metrics.py) 的 `validate_samples` 约定，未确认标注不能当作质量真值。

先在仓库外准备私有输入目录及可写输出目录。以下路径需替换，输出文件必须尚不存在；在线运行前配置可访问的数据库 / Redis，选择本环境实际可用的模型配置 ID，并确认材料外发与费用授权：

```bash
python -m eval.articles --input /私有目录/articles.json --output /私有目录/results.json --config-id "$MODEL_CONFIG_ID" --depth standard --rounds 1
python -m eval.articles --input /私有目录/articles.json --rescore /私有目录/results.json --output /私有目录/rescored.json
```

`MODEL_CONFIG_ID` 需先设为正整数；在线评测必须显式传 `--config-id`，不照抄其他环境的 ID。默认重复 3 轮，示例限制为 1 轮；超时和不完整结果不按零错误计分。`--rescore` 只重新计算已有结果，不调用模型，也不要求模型配置；原文和领域必须与采集时一致，可修订人工标注。原文、标注和逐条结果留在私有目录，不提交到仓库。

## 代码规范

- **后端**：遵循项目 Ruff 配置，使用 Type Hints。
- **前端**：使用 TypeScript、Vue Composition API 和 `<script setup>`。
- **注释**：只解释非显然的约束或原因，不复述代码。
- **文档**：只更新受影响的概览、操作或部署说明；中英文 README 保持同一能力边界，接口细节链接到 Schema / API 文档。

## 协议

参与贡献即表示你同意你的代码遵循项目的 [MIT License](LICENSE)。
