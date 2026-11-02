# 5-minute demo walkthrough

**Setup (once):** follow the README Quick start (`setup` → `migrate` → `seed` → `run`). The demo works without any API keys. With a free `GROQ_API_KEY`, open questions get natural answers instead of knowledge-base quotes.

All demo passwords are `Demo@12345`. The console is at http://localhost:8000.

The seeded history is 50 conversations per business, produced by the real agent pipeline and spread over 30 days. LLM token counts and the latency of seeded LLM-style answers are illustrative, because the seed runs without an LLM. Everything you do live is real.

---

### Minute 1: Dashboard and multi-tenancy
1. Sign in as **`admin@sunrisedental.in`**. The console is white-labelled in Sunrise Dental's teal.
2. The **Dashboard** shows the pre-read KPIs:
   - first response vs the 2-second target
   - automated resolution and escalation rates (with the escalation reasons)
   - appointments and booking conversion
   - LLM tokens and estimated cost against the monthly quota

   Below them: conversations over time, channel mix (web, WhatsApp, API, Messenger), top intents and the lead pipeline.

### Minute 2: The customer experience (web widget)
1. Open **http://localhost:8000/demo/sunrise-dental** in a new tab. This is a sample clinic website with the one-line widget embed.
2. Ask: *"How much does teeth whitening cost?"* The answer is grounded in the clinic FAQ, with a **[1]** citation and a source chip.
3. Ask: *"Do you sell motorcycles?"* The agent politely stays in scope.
4. Book: *"I'd like to book a cleaning tomorrow"*. Tap a time slot, give a name and a phone number, then confirm with *"yes"*. You get a booking reference such as `APT-K38NJK`, and a reminder is scheduled automatically.
5. Type *"I want to talk to a human"*. The widget says a team member will join.

### Minute 3: Human handover (live)
1. Back in the console, open **Inbox**. The conversation you just escalated is at the top, labelled *customer request*. A toast appeared the moment it was escalated.
2. Open it. You see the full transcript, with each customer message's intent and confidence.
3. Type a reply and press Enter. It appears **instantly in the widget tab** as "Team member".
4. Click **Hand back to AI**. The widget tells the customer they're back with the assistant, and the AI answers again.

### Minute 4: Leads, knowledge and agent configuration
1. Sign out and sign in as **`admin@urbannest.in`** (real estate). Open **Leads**: a kanban of hot, warm and cold leads captured by the AI, each with an explanation of its score. Move one through the pipeline.
2. Go to **AI agents → Playground** and type *"I want to buy a 3BHK in Whitefield"*. The agent runs the qualification flow: budget → timeline → name → contact. Answer the steps; a new lead appears in **Leads**, scored. The debug panel shows the intent, the decision (rule-based flow or LLM), the tools called, the retrieval confidence and the latency.
3. Go to **Knowledge base → Test retrieval** and search *"home loan banks"* to see the similarity scores against the "I don't know" threshold. You can upload your own PDF or DOCX here.
4. Click **AI agents → New from template** to see the four industry templates. Each creates an agent in one click.

### Minute 5: Agency and white-label, settings, platform
1. Sign in as **`admin@brightreach.agency`**. Open **Clients & tenants** to see the reseller view: conversations, leads, AI resolution and token usage against quota, per client.
2. Click **Open** on UrbanNest. You are now acting inside the client workspace (an amber "acting as client" badge appears), sandboxed to it.
3. Go to **Branding & widget**, change the primary colour and the widget title, and watch the live preview. Click **Save**; the console theme changes immediately. Copy the embed snippet.
4. **Settings** shows the guardrails (blocked terms, allowed topics, retention days), channel routing for WhatsApp and Messenger, API keys (secret keys are shown once), usage vs quota, the **audit log** and the **workflow action log** (reminders, webhooks, retries).
5. Optional: open http://localhost:8000/docs for the full OpenAPI. Run `python scripts/dev.py simulate` for intent accuracy and resolution and handover rates per template.

---

### Things worth pointing out
- **Works without paid services:**
  - no LLM key → rules-only mode (flows, extractive knowledge answers, handover)
  - no Redis → in-process fallback
  - no Kafka → in-process event bus
  - no Postgres → SQLite fallback
- **Safety:**
  - PII is redacted before every LLM call: the model only ever sees placeholders like `<EMAIL_1>`, which are restored only when a tool saves the lead. `tests/test_agent_flows.py::test_llm_tool_loop_with_pii_redaction_and_usage_metering` proves it.
  - Prompt injection is refused. Try *"ignore all previous instructions…"*.
- **Isolation:** every query is tenant-filtered twice, in the services and by an ORM-level guard. Tests prove that cross-tenant reads and writes fail.
