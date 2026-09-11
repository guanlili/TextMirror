<div align="center">

# 🛡️ TextMirror — Intelligent Document Proofreading Platform

**An AI-driven document proofreading and polishing platform: AI rewriting, text proofreading, document upload review, online preview, issue highlighting, one-click or item-by-item fixes, and export.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB.svg)](https://www.python.org/)
[![Vue 3](https://img.shields.io/badge/Vue-3.x-4FC08D.svg)](https://vuejs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)

*Make every document stand up to scrutiny — intelligent proofreading · AI polishing · multi-model driven · works out of the box*

**[简体中文](README.md)** | English

</div>

---

## ✨ Why TextMirror?

Most document proofreading tools rely on closed-source commercial services — expensive and inflexible. **TextMirror** is a fully open-source, self-hostable proofreading platform that gives teams and individuals commercial-grade document review capability.

- **🚀 Works out of the box** — one-command Docker deployment, online in 5 minutes
- **🤖 Multi-model support** — built-in adapters for 16 AI providers (DeepSeek, OpenAI, Volcengine Ark, Qwen, Kimi, MiniMax, ...), switchable live from the admin console
- **🎚️ Three proofreading depths** — Quick (rules-only, instant, zero cost) / Standard (AI, seconds) / Deep (AI with extended thinking, fewer false positives)
- **⚡ Streaming output** — polish results stream word by word via SSE with real-time progress
- **⚖️ Multi-model comparison** — run the same text through 2–4 models concurrently and compare quality and latency
- **📝 Full format coverage** — DOCX / PDF / TXT upload review with original layout preserved
- **🎨 AI rewriting** — 10 styles, three intensity versions (light / standard / deep) with streaming output
- **🔍 Precise issue location** — highlighted results, review item by item, accept or ignore each
- **📚 Deterministic lexicon engine** — sensitive/error words matched with 100% recall (not probabilistic), three-layer lexicon + user feedback flywheel
- **📖 Operable domain rules** — built-in official-document / legal domain rules; edit industry rules in the admin console with instant effect, no redeploy
- **🔌 Open API** — API keys + 8 endpoints to integrate proofreading and AI polishing into your own workflows, always-on Swagger docs, unified error contract
- **🏢 Enterprise management** — full RBAC, audit logs, per-user daily quotas (unlimited supported), guest rate limiting
- **🔐 Auth security** — password change instantly invalidates all old tokens; short-lived access tokens with auto-renewal
- **📱 Responsive** — complete PC and mobile layouts

---

## 🎯 Core Features

### 📋 Text Proofreading
> Paste text and get instant review. Long texts are chunked automatically; results are highlighted and fixable item by item. Export either the corrected full text or an issue report (original / suggestion / explanation / status per issue).
>
> **Three depths**: **Quick** (rules engine only — instant, zero AI cost) / **Standard** (default — AI with deep thinking disabled, seconds) / **Deep** (AI with extended thinking + second-pass review, for final review of important documents)

### 📄 Document Upload Review
> Upload DOCX / PDF / TXT; documents are parsed and proofed asynchronously with SSE progress updates (per-chunk granularity for long documents). Preview preserves layout; export a corrected document after fixes.

### 🎨 AI Rewriting
> 10 rewriting styles, three intensity versions generated concurrently with streaming word-by-word rendering; generation can be stopped mid-flight.
>
> **Multi-model comparison mode**: pick 2–4 configured models, polish the same text in parallel, compare output and latency; results saved to history automatically.

### 🔁 Re-run from History
> From any history entry, one click re-runs polish or proofreading with the original text pre-filled.

### 📚 Lexicon Engine (Deterministic Scanning)
> - **100% recall**: sensitive / forbidden / correction words use deterministic string scanning (not LLM guessing) — guaranteed hits at zero token cost; the LLM focuses on grammar, logic, and expression
> - **Three-layer lexicon**: global (admin) + personal (user's wrong→right rules) + whitelist (do-not-disturb list, personal corrections take priority)
> - **Hit source labels**: results are tagged 〔My lexicon〕/〔Global lexicon〕so you can see your config working
> - **Suggestion flywheel**: every accept/ignore is recorded; the admin console aggregates whitelist / correction candidates for one-click adoption — gets sharper with use

### 📖 Domain Rules
> Built-in rules for general / official-document / legal domains (document format, legal terminology, amount-in-words consistency, ...). The admin "Review Rules" page lets you edit domain rules — saved rules take effect immediately, no release needed.

### 🤖 Multi-model Management
> 16 built-in provider adapters: DeepSeek / OpenAI / Volcengine Ark (Doubao) / Qwen / Kimi / Tencent Hunyuan / Zhipu GLM / Baidu Qianfan / iFlytek Spark / MiniMax / SiliconFlow / Azure OpenAI / LiteLLM / custom OpenAI-compatible gateways ...
> Configure and switch models in the admin console without restarts; deep-thinking parameters adapt per provider (verified on Volcengine Ark and Qwen).

### 🛡️ Quotas and Rate Limiting
> - **Per-user daily quota**: set per user, blank = unlimited, resets at midnight Beijing time
> - **Guest rate limiting**: per-IP daily limits for anonymous users, with configurable count and text-length caps

### 🔐 Enterprise Admin Console
> User management / RBAC roles / model configuration / lexicon management / audit logs / platform settings / branding

### 🔌 Open API
> Integrate proofreading into your workflows (scripts / CI / enterprise systems):
> - **Self-service API keys**: create in the web UI (SHA-256 hashed storage, RPM + daily-quota two-layer limiting, automatic refund on failure)
> - **8 endpoints**: text proofread / multi-model compare / async document review (upload → poll) / job status / model list / usage statistics / AI polish (incl. streaming)
> - **Unified error contract**: `{code, message}` with always-on Swagger docs (`/api/v1/open/docs`) — Try it out works out of the box

### 🎨 Branding (White-label)
> Configure in real time from the admin console, no code changes:
> **platform name / subtitle / favicon / login slogan / footer (ICP number) / guest mode toggle**
> Internal deployments can disable guest mode (login required); public deployments keep the guest experience — one codebase, two shapes.

### 🔗 Feishu Integration (optional)
> Feishu QR login / SSO / auto user provisioning — off by default, configure as needed.

---

## 🛠️ Tech Stack

| Layer | Solution |
|:-----:|----------|
| **Frontend** | Vue 3 + TypeScript + Vite + Element Plus + Pinia |
| **Editor** | Tiptap rich text + docx-preview + pdf.js |
| **Backend** | Python 3.10+ / FastAPI / SQLAlchemy 2.0 |
| **Database** | PostgreSQL + Redis |
| **Async tasks** | Celery Worker |
| **Auth** | JWT + RBAC permission model |
| **Deployment** | Docker Compose (5 containers: frontend / backend / Celery / PostgreSQL / Redis) |

---

## 🚀 Quick Start

### Requirements

- Python 3.10+
- Node.js 22+ (vite 7 requires 20.19+; 22 LTS recommended)
- PostgreSQL 14+
- Redis 6+

### Local Development (Docker, recommended)

```bash
# 1. Configure the backend (DB/Redis point to the compose services)
cp backend/.env.example backend/.env

# 2. Build and start everything (postgres + redis + backend hot-reload + celery + frontend)
docker compose -f docker-compose.dev.yml up -d --build

# 3. Initialize the database (schema + seed data: admin / roles / global lexicon)
docker exec textmirror-dev-backend python -m app.core.seed
docker exec textmirror-dev-backend alembic stamp head   # stamp migration baseline
```

### Local Development (bare metal)

```bash
# 1. Clone
git clone https://github.com/guanlili/TextMirror.git
cd TextMirror

# 2. Backend setup
cd backend
cp .env.example .env          # copy and edit (DB / Redis / API keys)
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate
pip install -r requirements.txt

# 3. Initialize the database (schema + seed data)
python -m app.core.seed       # tables, admin account, roles, global lexicon
alembic stamp head            # stamp migration baseline

# 4. Frontend setup
cd ../frontend
npm install

# 5. Start both (two terminals)
# backend:
uvicorn app.main:app --reload --port 3020
# frontend:
cd ../frontend && npm run dev
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3022 |
| Backend API docs | http://localhost:3020/docs |
| Default account | admin / admin123 |

---

## 🐳 Docker Deployment

Server-side build, three steps to production:

```bash
# 1. Clone
git clone https://github.com/guanlili/TextMirror.git /opt/TextMirror && cd /opt/TextMirror

# 2. Configure environment
#    Root .env: POSTGRES_PASSWORD / REDIS_PASSWORD (read by compose)
#    backend/.env.production: copy backend/.env.example, then fill in DB, Redis, and AI provider API keys

# 3. Build and start (5 containers: frontend / backend / Celery / PostgreSQL / Redis)
docker compose up -d --build
```

### Enable HTTPS (optional)

1. Put SSL certificates into `frontend/ssl/`
2. Rename `frontend/nginx.production.ssl.conf` to `nginx.production.conf`
3. Adjust certificate paths and rebuild the frontend image

> First-run initialization, updates, rollback, backup, and troubleshooting are covered in the ops manual (`系统运维操作手册.md`, in Chinese).

---

## 📁 Project Structure

```
TextMirror/
├── backend/                # Backend (FastAPI)
│   ├── app/
│   │   ├── api/           # API routes
│   │   ├── core/          # Config, security, database
│   │   ├── models/        # Data models
│   │   └── services/      # Business logic
│   ├── alembic/           # Database migrations
│   ├── .env.example       # Config template
│   └── requirements.txt
├── frontend/              # Frontend (Vue 3)
│   ├── src/
│   │   ├── views/         # Pages
│   │   ├── stores/        # Pinia state
│   │   ├── api/           # API clients
│   │   └── utils/         # Utilities
│   ├── nginx.production.conf      # Nginx config (HTTP)
│   ├── nginx.production.ssl.conf  # Nginx config (HTTPS)
│   └── Dockerfile
├── docker-compose.yml     # Container orchestration
├── deploy.sh              # Server deployment script
└── README.md
```

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [系统功能设计文档](系统功能设计文档.md) | Architecture, data models, API design, feature list, dev conventions (Chinese) |
| [系统运维操作手册](系统运维操作手册.md) | Local dev, production deployment, DB init, update/rollback, troubleshooting (Chinese) |
| [用户使用手册](用户使用手册.md) | End-user guide (Chinese) |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

---

## 🤝 Contributing

Issues and pull requests are welcome!

1. Fork this repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a pull request

> See [CONTRIBUTING.md](CONTRIBUTING.md) for the detailed guide.

---

## 📄 License

This project is released under the [MIT License](LICENSE).

TextMirror is developed on top of TextGuard and retains the original project's MIT license and copyright notice.

---

<div align="center">

**TextMirror** — let AI guard every word

Made with ❤️ by TextMirror Contributors

</div>
