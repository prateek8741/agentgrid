# AgentGrid

AI-powered customer engagement & business automation platform. This is a multi-tenant SaaS proof of concept for RFP **TTG-AI-AGENTGRID** (The Talent Grid).

> Status: **Phase 4 complete.**
> - **Phase 1 foundations:** config, data model and migrations, JWT auth, RBAC, multi-tenancy with agency hierarchy, audit log, API keys, Docker, and the task runner.
> - **Phase 2 agent:** LLM layer (Groq primary, Gemini fallback, automatic failover) and a hybrid NLU (rules, then LLM). A state-machine dialogue manager runs lead qualification, booking and cancellation. An open-question tool loop handles everything else.
> - **Phase 2 tools:** 10 tools: CRM, booking, notifications, handover, products, payment links and tickets. Four industry templates.
> - **Phase 4 channels:** an embeddable web widget (`/widget.js`: Shadow DOM, white-label, live WebSocket with HTTP fallback, signed visitor sessions); a REST API channel (secret key); WhatsApp Cloud API and Messenger webhooks (signature-verified, retry de-duplication, Graph API replies). A demo storefront is served at `/demo/<tenant-slug>`.
> - **Phase 3 knowledge base:** upload PDF, DOCX, TXT or MD; section-aware chunking; fastembed bge-small embeddings (ONNX, no PyTorch). Search uses pgvector (HNSW) or in-process cosine, with a lexical re-rank. Answers are grounded with [n] citations, and the assistant says "I don't know" below the confidence threshold.
> - **Phase 2 platform:** a workflow engine (retries, backoff, reminder timers), signed outgoing webhooks, HubSpot sync, PII redaction before the LLM, guardrails, token quotas and a FAQ cache.
>
> The full documentation (architecture, deployment, demo walkthrough) is written in Phase 9.
> Without LLM keys the agents still run their structured flows (rules-only mode). Add `GROQ_API_KEY` and/or `GEMINI_API_KEY` to `.env` for open-ended answers.

## Quick start (lite mode, no Docker)

Requires **Python 3.12**. The same commands work in macOS Terminal and Windows PowerShell.

```bash
python scripts/dev.py setup      # creates .venv, installs deps, creates .env with a random JWT secret
# edit .env -> DATABASE_URL (Neon/Supabase/local Postgres) and optionally REDIS_URL (Upstash)
python scripts/dev.py migrate
python scripts/dev.py seed       # prints the super-admin login
python scripts/dev.py run        # http://localhost:8000/docs
python scripts/dev.py test
```

On Windows, use `py -3.12 scripts/dev.py setup` if `python` isn't 3.12. After setup, `python scripts/dev.py <cmd>` works from any Python.

## Full mode (Docker Compose)

```bash
python scripts/dev.py docker-up
docker compose exec api python -m app.cli seed
```

Services: api, Postgres 16 + pgvector, Redis 7, and Redpanda (Kafka API). All are memory-capped, about 2.2 GB in total.
