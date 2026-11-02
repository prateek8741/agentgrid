# AgentGrid

**AI-powered customer engagement and business automation platform.** This is a multi-tenant SaaS proof of concept for RFP **TTG-AI-AGENTGRID** (The Talent Grid).

AgentGrid gives businesses and agencies AI agents that talk to customers on web chat, WhatsApp, Messenger and a REST API. The agents qualify and score leads, book appointments, answer from a knowledge base with citations, and run follow-up workflows. When a person is needed, they hand the conversation to a human through a live inbox. Everything shows up in an analytics dashboard.

The whole stack runs on free tiers and open source (₹0). It runs on macOS (Apple Silicon and Intel) and Windows 10/11 with 8–12 GB of RAM.

[![CI](https://github.com/prateek8741/agentgrid/actions/workflows/ci.yml/badge.svg)](https://github.com/prateek8741/agentgrid/actions/workflows/ci.yml)

| Dashboard | Live inbox |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Inbox](docs/screenshots/inbox.png) |
| **AI agents (templates, editor, playground)** | **Embeddable chat widget (white-labelled)** |
| ![Agents](docs/screenshots/agents.png) | ![Widget](docs/screenshots/widget.png) |

---

## Features

| RFP requirement | What AgentGrid does |
|---|---|
| **Omnichannel AI agents** | Embeddable web widget (`<script>`, Shadow DOM, live WebSocket with HTTP fallback), REST API channel (secret key), WhatsApp Cloud API and Messenger webhooks (signature-verified). All channels normalise into one message schema and go through one agent. |
| **Lead qualification** | A guided flow captures name, contact, need, budget and timeline. Scoring is explainable and rule-based (hot / warm / cold). The built-in mini CRM has a kanban pipeline. Optional HubSpot sync. |
| **Workflow automation** | Tools for checking availability, booking and cancelling, CRM updates, notifications (SMTP or logged), signed outgoing webhooks, product recommendations, payment links (mock or Razorpay test mode) and support tickets. A durable workflow engine adds retries with exponential backoff, timers (appointment reminders) and an action log. |
| **AI knowledge assistant** | Per-tenant upload of PDF, DOCX, TXT and MD, with section-aware chunking and fastembed `bge-small-en-v1.5` embeddings (ONNX, no PyTorch). Search uses pgvector HNSW. Answers carry `[n]` source citations, and the assistant says "I don't know" when retrieval confidence is low. |
| **Human handover** | Escalates when the customer asks for a person, on low confidence, on negative sentiment, on urgent intents or when a flow gets stuck. A live inbox (WebSocket + Redis pub/sub) shows the full history. Agents reply on the customer's channel and can hand back to the AI. |
| **Multi-tenant SaaS** | Strict tenant isolation in services and at the ORM layer. Roles: `super_admin`, `tenant_admin`, `agent`, `viewer`. JWT access tokens and rotating refresh tokens. Per-tenant agent config. White-label branding for the console and widget. Agency → client hierarchy with a reseller dashboard. |
| **Analytics** | Conversations over time and by channel, lead conversion and qualification, escalation rate, automated resolution, first-response time (vs a 2-second target), appointments, and LLM tokens and cost per tenant. |
| **Pre-read extras** | Four industry templates. A hybrid NLU (keyword rules first, then LLM JSON extraction) feeding a state-machine dialogue manager. PII redaction before every LLM call. Guardrails, token quotas, FAQ caching and data retention. Kafka (Redpanda) event bus. Prometheus metrics, a Grafana dashboard and optional OpenTelemetry/Sentry. A simulation harness. Terraform and Kubernetes manifests. |

## Architecture

```mermaid
flowchart LR
  subgraph Channels
    W[Web widget<br/>widget.js + WebSocket]
    R[REST API<br/>X-API-Key]
    WA[WhatsApp<br/>Cloud API webhook]
    M[Messenger<br/>webhook]
  end
  subgraph API["FastAPI (single image)"]
    CA[Channel adapters<br/>→ unified InboundMessage]
    O[Agent orchestrator]
    NLU[Hybrid NLU<br/>rules → LLM JSON]
    DM[Dialogue manager<br/>state machine flows]
    LOOP[Tool-calling loop]
    RAG[RAG: fastembed + pgvector]
    T[Tools: CRM, booking, notify,<br/>handover, products, payments, tickets]
    INBOX[Inbox API + WS]
  end
  LLM[(Groq → Gemini<br/>failover)]
  PG[(PostgreSQL 16<br/>+ pgvector)]
  RD[(Redis<br/>cache · rate limits · pub/sub)]
  K[(Kafka / Redpanda<br/>or in-process bus)]
  WK[Workers: workflow engine,<br/>analytics consumer, webhooks]

  W & R & WA & M --> CA --> O --> NLU --> DM
  DM -->|structured flow| T
  DM -->|open question| LOOP --> LLM
  LOOP --> T
  O --> RAG --> PG
  T --> PG
  O --> K --> WK --> PG
  K --> RD --> INBOX
  O --> RD
```

More detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): data model, request flow, multi-tenancy, security and scaling.

## Quick start

There are three ways to run it, all with the same commands on macOS Terminal and Windows PowerShell:

| Mode | You need | RAM |
|---|---|---|
| **Zero-config** (fastest for evaluators) | Python 3.12 only. SQLite plus an in-process Redis fallback. | ~500 MB |
| **Lite** (recommended) | Python 3.12 plus free Neon or Supabase Postgres and free Upstash Redis | < 1 GB |
| **Full** (Docker) | Docker Desktop | ~2.2 GB (capped) |

### macOS

```bash
# 1. Python 3.12 (skip if `python3.12 --version` works)
brew install python@3.12            # or: uv python install 3.12

# 2. Clone and set up (creates .venv, installs deps, writes .env with a random JWT secret)
git clone https://github.com/prateek8741/agentgrid.git && cd agentgrid
python3.12 scripts/dev.py setup

# 3. Choose a database in .env:
#    zero-config:  DATABASE_URL=sqlite+aiosqlite:///./data/agentgrid.db
#    lite:         DATABASE_URL=<your Neon/Supabase URL>   (see "Free accounts" below)

# 4. Create tables, load demo data, run
python3 scripts/dev.py migrate
python3 scripts/dev.py seed         # prints the demo logins
python3 scripts/dev.py run          # http://localhost:8000
```

### Windows 10/11 (PowerShell)

```powershell
# 1. Python 3.12
winget install Python.Python.3.12     # then open a new PowerShell window

# 2. Clone and set up
git clone https://github.com/prateek8741/agentgrid.git
cd agentgrid
py -3.12 scripts/dev.py setup

# 3. Edit .env (notepad .env) and set DATABASE_URL as above

# 4. Create tables, load demo data, run
py scripts/dev.py migrate
py scripts/dev.py seed
py scripts/dev.py run
```

Then open:
- **Admin console:** http://localhost:8000
- **API docs (OpenAPI):** http://localhost:8000/docs
- **Demo websites with the chat widget:** http://localhost:8000/demo/sunrise-dental and http://localhost:8000/demo/urbannest-realty

Without LLM keys, the agents still run their guided flows (lead qualification, booking, cancellation), answer from the knowledge base by quoting it, and hand over to humans. Add a free `GROQ_API_KEY` for natural conversation.

### Full mode (Docker Compose)

```bash
python scripts/dev.py docker-up                    # api + postgres(pgvector) + redis + redpanda
docker compose exec api python -m app.cli seed
python scripts/dev.py docker-up --observability    # adds Prometheus :9090 + Grafana :3000
```

Every service has a memory limit: api 768 MB, Postgres 512 MB (tuned with `shared_buffers=128MB` and `max_connections=50`), Redis 128 MB (`maxmemory 100mb`, `allkeys-lru`), and Redpanda 768 MB (`--smp 1 --memory 512M`). All images are multi-arch (amd64 and arm64).

## Demo credentials

Created by `python scripts/dev.py seed`. The password for all demo users is **`Demo@12345`**.

| Workspace | Email | Role |
|---|---|---|
| BrightReach Digital (agency that owns both clients) | `admin@brightreach.agency` | Agency admin: reseller dashboard, switch into clients |
| Sunrise Dental Clinic (appointment-heavy) | `admin@sunrisedental.in` | Tenant admin |
| | `agent@sunrisedental.in` | Agent (inbox) |
| | `viewer@sunrisedental.in` | Viewer (read-only) |
| UrbanNest Realty (lead-heavy) | `admin@urbannest.in` | Tenant admin |
| | `agent@urbannest.in` | Agent |
| Platform | `admin@agentgrid.dev` / `SUPERADMIN_PASSWORD` from `.env` | Super admin |

A 5-minute guided walkthrough is in [docs/DEMO.md](docs/DEMO.md).

## Free accounts (all optional)

| Service | Used for | How to get it | Where it goes in `.env` |
|---|---|---|---|
| **Groq** (primary LLM) | Conversations, tool calling, NLU | Sign up at [console.groq.com](https://console.groq.com) → **API Keys** → **Create API Key** | `GROQ_API_KEY=gsk_...` |
| **Google Gemini** (fallback LLM) | Automatic failover on rate limits and errors | [aistudio.google.com](https://aistudio.google.com) → **Get API key** → **Create API key** | `GEMINI_API_KEY=...` |
| **Neon** (Postgres + pgvector) | Database (lite mode) | [neon.tech](https://neon.tech) → New project (Postgres 16) → **Connection string** | `DATABASE_URL=postgresql://...?sslmode=require` (any form works) |
| **Supabase** (alternative) | Database | [supabase.com](https://supabase.com) → Project → **Connect** → session pooler URI. The transaction pooler (port 6543) also works. | `DATABASE_URL=...` |
| **Upstash** (Redis) | Shared cache, rate limits, live inbox across processes | [upstash.com](https://upstash.com) → **Create database** → copy the `rediss://` URL | `REDIS_URL=rediss://default:...@...upstash.io:6379` |

The default models are `openai/gpt-oss-120b` for chat and `openai/gpt-oss-20b` for NLU. Both were verified against Groq's model list on 2 Nov 2026. You can change them with `GROQ_MODEL` and `GROQ_NLU_MODEL`.

## Connecting the WhatsApp test number (free)

1. Go to [developers.facebook.com](https://developers.facebook.com) → **My Apps** → **Create app** → **Business**, and add the **WhatsApp** product.
2. Under **WhatsApp → API Setup**, Meta gives you a free test number. Add your own phone as a recipient. Copy:
   - the **temporary access token** → `WHATSAPP_ACCESS_TOKEN`
   - the **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`
3. Under **App settings → Basic**, copy the **App secret** → `WHATSAPP_APP_SECRET`. AgentGrid uses it to verify `X-Hub-Signature-256` on every webhook.
4. Choose any random string as `WHATSAPP_VERIFY_TOKEN`, and set `CHANNELS_DEFAULT_TENANT=sunrise-dental`. Alternatively, put the phone number ID in the tenant under **Settings → Channels**.
5. Expose your local server, for example with `cloudflared tunnel --url http://localhost:8000` (free).
6. In **WhatsApp → Configuration**, set the callback URL to `https://<tunnel>/webhooks/whatsapp`, enter the same verify token, and subscribe to the **messages** field.
7. Send "Hi" from your phone to the test number. The agent replies, and quick replies arrive as WhatsApp buttons.

Messenger works the same way with `/webhooks/messenger`, `MESSENGER_APP_SECRET`, `MESSENGER_VERIFY_TOKEN` and `MESSENGER_PAGE_ACCESS_TOKEN`.

## Configuration

Every setting has a safe default; see [.env.example](.env.example) for the full annotated list. The most important ones:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local Postgres | Postgres (Neon / Supabase / Docker) or `sqlite+aiosqlite:///...` |
| `REDIS_URL` | empty (in-process) | Upstash / Docker Redis. Needed when you run more than one process. |
| `KAFKA_BOOTSTRAP_SERVERS` | empty (in-process bus) | e.g. `redpanda:9092` |
| `GROQ_API_KEY`, `GEMINI_API_KEY` | empty | LLM providers (primary and fallback) |
| `JWT_SECRET` | generated by setup | **Required in production.** The app refuses to start with the default. |
| `VECTOR_BACKEND` | `auto` | `pgvector` on Postgres; `json` for a Postgres without the extension |
| `RAG_MIN_SCORE` | `0.58` | Below this retrieval score the assistant says "I don't know" (calibrated on bge-small) |
| `WEB_CONCURRENCY` | `1` | Uvicorn workers (raise in production) |
| `RUN_WORKERS_IN_API` | `true` | Set `false` when running `python -m app.workers` separately (needs Kafka) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies trusted for `X-Forwarded-For` (your load balancer's range) |
| `DEFAULT_MONTHLY_TOKEN_QUOTA` | `2000000` | Per-tenant LLM token quota. When it's reached, agents degrade to rules-only mode. |

## Development

```bash
python scripts/dev.py test              # 248 tests (SQLite); set TEST_DATABASE_URL to run them on Postgres
python scripts/dev.py lint              # ruff
python scripts/dev.py simulate          # synthetic conversations per template: intent accuracy, resolution, handover
python scripts/dev.py simulate --llm    # the same, against the real LLM
python scripts/dev.py makemigration "message"
cd frontend && npm install && npm run dev   # console dev server on :5173 (proxies the API)
```

Node is only needed to *edit* the console. Its production build is committed in `app/static/console` and served by FastAPI.

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs:
- lint and tests on Ubuntu, macOS and Windows
- the full suite on PostgreSQL 16 + pgvector, plus migration up/down/drift checks, the seed and the simulation
- the console build
- a multi-arch Docker build

## Project structure

```
app/
  api/          FastAPI routers (v1 REST, widget, webhooks, inbox, analytics, console SPA)
  core/         config, DB, tenancy guard, security, RBAC, logging, PII, rate limits, metrics
  models/       SQLAlchemy models (tenants, users, conversations, CRM, booking, KB, workflow, usage)
  agents/       LLM layer, NLU, dialogue manager, tool loop, templates, guardrails, tools
  rag/          extraction, chunking, embeddings, retrieval, grounded answers
  channels/     unified message schema, Meta adapters, delivery, widget sessions
  services/     business logic (leads, booking, inbox, analytics, usage, ...)
  integrations/ email, signed webhooks, HubSpot, payments
  workers/      event bus (in-process / Kafka), workflow engine, analytics consumer, maintenance
  static/       widget.js and the built admin console
frontend/       React + Vite + Tailwind console source
migrations/     Alembic migrations
infra/          Terraform (AWS reference) and Kubernetes manifests
deploy/         Postgres init, Prometheus and Grafana config
scripts/dev.py  cross-platform task runner
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data model, request flow, multi-tenancy, security, scaling plan, and how the PoC maps to the pre-read phases
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): free deployment (Render + Neon + Upstash) and the production AWS reference architecture
- [docs/API.md](docs/API.md): key endpoints with examples
- [docs/DEMO.md](docs/DEMO.md): a 5-minute evaluator walkthrough
