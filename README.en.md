<div align="center">

# TextMirror — Intelligent Document Proofreading Platform

**Proofreading first, polishing second: find issues, review suggestions, save versions, and deliver a revised draft.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Vue 3](https://img.shields.io/badge/Vue-3.x-4FC08D.svg)](https://vuejs.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)

[简体中文](README.md) | English

</div>

TextMirror is an open-source, self-hostable proofreading platform for everyday documents, official writing, and legal texts. Deterministic rules and configurable language models identify issues; users decide which changes to apply.

## Capabilities

| Capability | Description |
|------------|-------------|
| Text and document review | Paste text or upload DOC / DOCX / PDF / TXT; long-text chunking and task progress |
| Three depths | Quick runs rules only; Standard uses AI; Deep adds thinking and a second pass. Quality and latency depend on the model |
| Comparison and role-based review | Compare 2–4 models, or coordinate rule, language, consistency, and dispute-review stages; not multi-user live editing |
| Review and delivery | Accept, ignore, or undo location-specific suggestions; revised-text preview; manually saved drafts, up to 20 versions, version comparison, and history recovery |
| Coverage reporting | Distinguish complete, partial, and unrecorded coverage; unfinished review is not an issue-free result |
| Dictionaries and domain rules | Global dictionaries, personal corrections, allowlists, editable domain rules, and feedback-assisted dictionary maintenance |
| Quality evaluation | Human-reviewed samples with separate detection and replacement-suggestion checks against accepted or rejected alternatives |
| Optional fact checking | Independent workspace for text, documents, or review records; claim confirmation, single-claim deep checks, human review, and report export; web search or trusted sources, disabled by default |
| AI polishing | 10 styles, three intensity versions, streaming, and multi-model comparison |
| Administration and integration | RBAC, user/guest quotas, branding, optional Feishu login, API keys, webhooks, and a physical-request model usage ledger |

### Boundaries

- **AI suggestions are not correctness guarantees.** Saving, exporting, or a successful task does not certify a document. Inspect coverage and review the result.
- **Previewed content is not necessarily reviewed content.** DOCX extraction currently covers body paragraphs, not tables or other non-body content. PDF processing extracts text without OCR and allows up to 100 pages. The default upload limit is 20MB.
- The document page exports revised TXT, a TXT issue report, and Word. Layout-preserving rewriting is attempted only for DOCX sources; unsafe changes are rejected, with TXT available as an alternative. The text page exports full text or an issue report as TXT.
- Saved records can be recovered after refresh, but **acceptance decisions require manual draft saving**. Guest review state exists only in page memory.
- Both fact-checking modes send content to external services. Trusted-source mode restricts admissible evidence, not all search activity; it is neither offline nor closed retrieval. Findings are advisory and are not automatically applied.
- Quick review has no AI token cost but still uses platform quotas and rate limits. The model ledger records known usage, does not backfill historical estimates, and is not a provider invoice.

## Local Development (Docker recommended)

Install Docker Engine / Docker Desktop with the Compose plugin. This is a development setup, not a public deployment configuration.

```bash
git clone https://github.com/guanlili/TextMirror.git
cd TextMirror
cp backend/.env.example backend/.env
```

Edit the corresponding values in `backend/.env`. The template's `localhost` addresses do not work between containers:

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

The backend initializes tables, migrations, and seed data automatically. Do not run a manual migration `stamp` for a fresh container setup.

| Entry | Address / details |
|-------|-------------------|
| User and admin UI | http://localhost:3022 |
| Backend API docs | http://localhost:3020/docs (DEBUG mode) |
| Open API docs | http://localhost:3022/api/v1/open/docs |
| Development administrator | `admin / admin123`, for DEBUG development only |

In the admin console, add a real provider key under model configuration and activate the model. Model keys are not configured in `.env`. Quick review currently still loads model configuration, but sends no model requests.

Once fact checking is enabled and your account has run permission, sign in and open `/fact-check` to submit text or a document directly, without first proofreading it. Existing review records can also be imported.

Only the backend API hot-reloads in development Compose. Rebuild `frontend` for UI changes; restart both `celery-worker` and `fact-check-worker` after they are idle for worker code changes. Do not reset the database to resolve migration errors.

## Production Deployment

Use [docker-compose.yml](docker-compose.yml) on a Linux server. It serves **HTTP on port 3022** by default and uses host networking, unlike the bridged macOS / Windows development setup.

Configure the root `.env` and `backend/.env.production`: strong database/Redis passwords, `DEBUG=false`, independent application/JWT secrets, persistent upload storage, and allowed origins. A new production administrator receives a random password, not the development default. **Disable the default-enabled one-click login before public access**: it allows passwordless administrator login, and neither a random password nor `DEBUG=false` disables it. Also disable the `demo` account or change its default password `demo123456` in user management; disabling one-click login does not disable password login.

Follow the [operations manual](系统运维操作手册.md) for initialization, migrations, backups, and verification before public access. HTTPS requires a TLS-terminating proxy or explicit certificate mounts, SSL configuration, and health-check changes; copying certificates alone does not enable it.

## Open API

Create a key on the API Keys page and send `Authorization: Bearer tm_…`. `/api/v1/open/docs` is the authoritative source for parameters, enums, and interactive examples.

Endpoints cover text review, model comparison, asynchronous documents and polling, model discovery, usage statistics, polishing, and streaming polishing. Asynchronous documents support signed webhooks. See the [user guide](用户使用手册.md) for examples and quota boundaries.

## Stack and Layout

- Backend: Python **3.11+**, FastAPI, SQLAlchemy, Alembic, Celery.
- Frontend: Vue 3, TypeScript, Element Plus, Pinia, Vite; development and CI use Node.js 22.x (22.13 or newer).
- Storage: PostgreSQL 16 and Redis 7. Compose runs six services: frontend, API, general worker, fact-check worker, database, and cache.
- `backend/app/`: API, services, models, and tasks; `backend/alembic/`: migrations; `backend/tests/`: backend tests.
- `frontend/src/`: views, components, API clients, and tests; `backend/eval/`: proofreading evaluation tools.

## Documentation

The detailed guides are currently in Chinese.

| Document | Purpose |
|----------|---------|
| [User guide](用户使用手册.md) | Proofreading, review delivery, fact checking, quality feedback, and API integration |
| [Operations manual](系统运维操作手册.md) | Development, production configuration, upgrades, backups, rollback, and troubleshooting |
| [System design](系统功能设计文档.md) | Module responsibilities, data flow, and consistency contracts |
| [Contributing](CONTRIBUTING.md) | Validation commands and PR workflow |
| [Changelog](CHANGELOG.md) | Merged milestones and unreleased changes |

## Contributing and License

Issues and pull requests are welcome. Code, tests, and documentation changes go through feature branches and PR review.

Released under the [MIT License](LICENSE). TextMirror builds on TextGuard and retains its original license and copyright notice.
