# API guide

The full interactive reference is served by the app at **`/docs`** (Swagger UI) and **`/redoc`**, generated from OpenAPI. This page covers the endpoints you need most often, with examples.

The base URL locally is `http://localhost:8000`. Errors always use one envelope:

```json
{"error": {"code": "forbidden", "message": "Requires role tenant_admin or higher", "request_id": "3f1c…"}}
```

Every response carries an `X-Request-ID` header. You can send your own `X-Request-ID` to correlate logs across systems.

## Authentication

| Caller | Credential | Header |
|---|---|---|
| Console users | JWT access token from `/auth/login` | `Authorization: Bearer <access_token>` |
| Your backend (REST channel) | Secret API key `sk_…` (Settings → API keys) | `X-API-Key: sk_…` |
| Web widget | Public key `pk_…` + server-signed visitor session | handled by `widget.js` |
| Meta webhooks | `X-Hub-Signature-256` (HMAC with your app secret) | sent by Meta |

```bash
# Log in
curl -s -X POST localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@sunrisedental.in","password":"Demo@12345"}'
# → {"access_token":"…","refresh_token":"…","expires_in":1800,"user":{…},"tenant":{…}}

TOKEN=…   # access_token from the response

# Rotate the refresh token (the old one becomes invalid; replaying it revokes the session family)
curl -s -X POST localhost:8000/api/v1/auth/refresh -H 'Content-Type: application/json' -d '{"refresh_token":"…"}'

# Agency or super admin: act inside a client tenant
curl -s -X POST localhost:8000/api/v1/auth/switch-tenant -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"tenant_id":"<client-tenant-uuid>"}'
```

Roles (each includes everything below it): `super_admin` > `tenant_admin` > `agent` > `viewer`.

## Channels

### REST API channel (server-to-server)

```bash
curl -s -X POST localhost:8000/api/v1/channels/rest/messages \
  -H "X-API-Key: sk_…" -H 'Content-Type: application/json' -d '{
    "session_id": "crm-user-42",
    "text": "Do you have any 3BHK flats in Whitefield under 1.2 crore?",
    "contact": {"name": "Neha", "email": "neha@example.com"},
    "metadata": {"utm_source": "google", "utm_campaign": "diwali-offer"}
  }'
```

Response (`AgentReply`):

```json
{
  "conversation_id": "…", "message_id": "…",
  "text": "Yes. Lakeview Residences in Whitefield has 3 BHK apartments from Rs 1.12 crore [1]. …",
  "handed_over": false, "status": "active",
  "intent": "property_inquiry", "confidence": 0.93,
  "quick_replies": [], "citations": [{"document_id": "…", "title": "Projects & buyer FAQ", "snippet": "…", "score": 0.81}],
  "latency_ms": 1180, "debug": {}
}
```

When a human takes over, `text` is `null` and `handed_over` is `true`. Human replies reach your system as signed `message.outbound` webhooks; see "Outgoing webhooks" below. To fetch the conversation history:

```bash
curl -s localhost:8000/api/v1/channels/rest/conversations/crm-user-42/messages -H "X-API-Key: sk_…"
```

### Web widget

Embed one line on any website:

```html
<script src="https://YOUR-HOST/widget.js" data-key="pk_…" async></script>
```

The widget calls the public endpoints `GET /api/v1/widget/config`, `POST /api/v1/widget/messages`, `GET /api/v1/widget/history` and the WebSocket `GET /api/v1/widget/ws`. To lock the widget to your domains, set **Settings → Channels → Allowed widget websites**.

### WhatsApp / Messenger webhooks

| Method | Path | Purpose |
|---|---|---|
| GET | `/webhooks/whatsapp` | Meta subscription check (`hub.mode`, `hub.verify_token`, `hub.challenge`) |
| POST | `/webhooks/whatsapp` | Inbound messages. `X-Hub-Signature-256` is required. |
| GET/POST | `/webhooks/messenger` | Same, for Messenger |

## Agents & knowledge

```bash
H="Authorization: Bearer $TOKEN"
curl -s localhost:8000/api/v1/agent-templates -H "$H"                           # template library
curl -s -X POST localhost:8000/api/v1/agents/from-template -H "$H" -H 'Content-Type: application/json' \
  -d '{"template_key":"real_estate_lead_qualifier","make_default":true}'         # one-click agent
curl -s -X POST localhost:8000/api/v1/agents/<agent_id>/playground -H "$H" -H 'Content-Type: application/json' \
  -d '{"message":"What does teeth whitening cost?"}'                             # test chat (+ debug info)

curl -s -X POST localhost:8000/api/v1/knowledge/documents -H "$H" -F "file=@faq.pdf" -F "title=Clinic FAQ"
curl -s localhost:8000/api/v1/knowledge/documents -H "$H"
curl -s -X POST localhost:8000/api/v1/knowledge/search -H "$H" -H 'Content-Type: application/json' \
  -d '{"query":"how much is whitening","top_k":4}'
# → {"confidence":0.89,"confident":true,"threshold":0.58,"results":[{"title":"…","similarity":0.89,…}]}
```

## CRM, booking, commerce

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/api/v1/leads?status=&temperature=hot&q=` | viewer | List leads (scored hot / warm / cold) |
| GET | `/api/v1/leads/pipeline` | viewer | Count of leads per stage |
| POST / PATCH | `/api/v1/leads`, `/api/v1/leads/{id}` | agent | Create, or move through the pipeline |
| GET | `/api/v1/slots?start=2026-11-03&days=14` | viewer | Availability |
| POST | `/api/v1/slots/generate` | admin | Recurring slots (weekdays, hours, length, capacity) |
| GET / PATCH | `/api/v1/appointments`, `/api/v1/appointments/{id}` | viewer / agent | List; mark completed, no-show or cancelled |
| POST | `/api/v1/products/import` | admin | CSV upload (`sku,name,price[,description,category,tags,…]`) |
| GET / PATCH | `/api/v1/tickets`, `/api/v1/tickets/{id}` | viewer / agent | Support tickets raised by the agent |

## Human handover inbox

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/inbox/conversations?status=handed_over\|active\|closed&mine=true` | Queue with contact, last message and counts |
| POST | `/api/v1/inbox/conversations/{id}/takeover` | Take over from the AI |
| POST | `/api/v1/inbox/conversations/{id}/assign` | Assign (`{"user_id": …}`; omit it to assign to yourself) |
| POST | `/api/v1/inbox/conversations/{id}/reply` | Reply (`{"text": …}`), delivered on the customer's channel |
| POST | `/api/v1/inbox/conversations/{id}/handback` | Return the conversation to the AI |
| POST | `/api/v1/inbox/conversations/{id}/close` | Close |
| WS | `/api/v1/inbox/ws?token=<access_token>` | Live feed: `message.received`, `message.sent`, `handover.requested`, `conversation.updated`, `message.human_reply` |
| GET | `/api/v1/conversations/{id}/messages` | Full transcript, including intent, confidence, tool calls and citations |

## Analytics, usage, operations

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/api/v1/analytics/overview?days=30` | viewer | KPIs, daily series, channel / intent / pipeline / handover breakdowns, quota |
| GET | `/api/v1/analytics/agency` | agency admin | Reseller view: KPIs and token usage per client |
| GET | `/api/v1/analytics/platform` | super admin | Every tenant |
| GET | `/api/v1/usage` | viewer | Tokens this month vs quota, active LLM providers |
| GET / POST | `/api/v1/actions`, `/api/v1/actions/{id}/retry` | admin | Workflow action log and manual retry |
| GET | `/api/v1/audit-logs?action=user.` | admin | Audit trail |
| GET | `/health`, `/ready` | none | Liveness, and readiness (DB + Redis) |
| GET | `/metrics` | optional bearer token | Prometheus |

## Tenants & administration

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET / PATCH | `/api/v1/tenant`, `/api/v1/tenant/settings` | viewer / admin | Active tenant and partial settings update (retention, guardrails, channels, webhook) |
| GET / PUT | `/api/v1/tenant/branding` | viewer / admin | White-label branding (console + widget) |
| GET / POST | `/api/v1/tenants` | admin | List visible tenants; create (super admin: any; agency admin: clients) |
| PATCH | `/api/v1/tenants/{id}` | admin | Status / plan / quotas (super admin or owning agency only) |
| CRUD | `/api/v1/users`, `/api/v1/api-keys`, `/api/v1/agents` | admin | Team, keys and agent configuration |

## Outgoing webhooks

Set **Settings → Channels → Outgoing webhook URL**. AgentGrid then POSTs these events: `lead.created`, `lead.updated`, `appointment.booked`, `appointment.cancelled`, `handover.requested`, `ticket.created`, `payment_link.created`, `payment.completed` and `message.outbound`.

Delivery is durable: failed deliveries are retried with exponential backoff and are visible in the action log.

```
POST <your url>
X-AgentGrid-Event: lead.created
X-AgentGrid-Delivery: <event id>
X-AgentGrid-Signature: sha256=<hex hmac of the raw body with your signing secret>

{"id": "…", "type": "lead.created", "data": {"lead_id": "…", "name": "Kiran", "score": 85, "temperature": "hot", …}}
```

To verify a delivery in Python:

```python
import hashlib
import hmac

expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-AgentGrid-Signature"])
```
