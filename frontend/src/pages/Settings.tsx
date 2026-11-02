import clsx from "clsx";
import { KeyRound, RotateCcw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { api, fmt } from "../api";
import { useAuth } from "../auth";
import {
  Badge, Button, Card, Field, Input, Modal, PageHeader, Select, Spinner, Table, Textarea, statusColor, useApi, useToast,
} from "../components/ui";

const TABS = ["General", "Channels & integrations", "API keys", "Usage", "Audit log", "Workflow actions"] as const;
const list = (s: string) => s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

export default function SettingsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("General");
  return (
    <div>
      <PageHeader title="Settings" subtitle="Guardrails, channels, keys, usage and audit trail" />
      <div className="mb-5 flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-brand font-medium text-brand" : "border-transparent text-slate-500 hover:text-slate-700")}>{t}</button>
        ))}
      </div>
      {(tab === "General" || tab === "Channels & integrations") && <TenantSettings section={tab} />}
      {tab === "API keys" && <ApiKeys />}
      {tab === "Usage" && <Usage />}
      {tab === "Audit log" && <Audit />}
      {tab === "Workflow actions" && <Actions />}
    </div>
  );
}

function TenantSettings({ section }: { section: string }) {
  const { reload } = useAuth();
  const toast = useToast();
  const { data } = useApi(() => api("/tenant"), []);
  const [s, setS] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setS(data.settings); }, [data]);
  if (!s) return <Spinner />;
  const set = (k: string, v: any) => setS({ ...s, [k]: v });
  const save = async () => {
    setBusy(true);
    try {
      const { monthly_token_quota: _q, rate_limit_per_minute: _r, ...editable } = s;
      await api("/tenant/settings", { method: "PATCH", body: editable });
      toast("Settings saved");
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card actions={<Button size="sm" onClick={save} loading={busy}><Save className="h-3.5 w-3.5" />Save</Button>} title={section}>
      {section === "General" ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Timezone"><Input value={s.timezone} onChange={(e) => set("timezone", e.target.value)} /></Field>
          <Field label="Currency"><Input value={s.currency} maxLength={3} onChange={(e) => set("currency", e.target.value.toUpperCase())} /></Field>
          <Field label="Transcript retention (days)" hint="Older conversations are deleted automatically every night.">
            <Input type="number" min={1} value={s.retention_days} onChange={(e) => set("retention_days", Number(e.target.value))} />
          </Field>
          <Field label="Hand over below NLU confidence"><Input type="number" step={0.05} min={0} max={1} value={s.handover_confidence_threshold} onChange={(e) => set("handover_confidence_threshold", Number(e.target.value))} /></Field>
          <Field label="Blocked terms" hint="One per line. Refused on input and filtered from AI output.">
            <Textarea value={(s.blocked_terms || []).join("\n")} onChange={(e) => set("blocked_terms", list(e.target.value))} />
          </Field>
          <Field label="Allowed topics" hint="Optional. The AI politely refuses anything outside these.">
            <Textarea value={(s.allowed_topics || []).join("\n")} onChange={(e) => set("allowed_topics", list(e.target.value))} />
          </Field>
          <Field label="Monthly token quota" hint="Set by the platform or your agency."><Input disabled value={s.monthly_token_quota ? fmt.num(s.monthly_token_quota) : "Unlimited"} /></Field>
          <Field label="API rate limit (per minute)" hint="Set by the platform or your agency."><Input disabled value={s.rate_limit_per_minute} /></Field>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="WhatsApp phone-number ID" hint="From Meta → WhatsApp → API setup. Routes that number to this workspace.">
            <Input value={s.whatsapp_phone_number_id || ""} onChange={(e) => set("whatsapp_phone_number_id", e.target.value || null)} />
          </Field>
          <Field label="Facebook page ID (Messenger)"><Input value={s.messenger_page_id || ""} onChange={(e) => set("messenger_page_id", e.target.value || null)} /></Field>
          <Field label="Allowed widget websites" hint="One origin per line, e.g. https://www.example.com. Empty = any website.">
            <Textarea value={(s.widget_allowed_origins || []).join("\n")} onChange={(e) => set("widget_allowed_origins", list(e.target.value))} />
          </Field>
          <Field label="Team notification email" hint="Hot leads and AI notifications are sent here.">
            <Input type="email" value={s.notification_email || ""} onChange={(e) => set("notification_email", e.target.value || null)} />
          </Field>
          <Field label="Outgoing webhook URL" hint="Receives lead, appointment, handover, ticket and payment events (HMAC-signed).">
            <Input value={s.outgoing_webhook_url || ""} onChange={(e) => set("outgoing_webhook_url", e.target.value || null)} />
          </Field>
          <Field label="Webhook signing secret" hint="Verify X-AgentGrid-Signature with this. Generated on first delivery.">
            <Input readOnly value={s.outgoing_webhook_secret || "(generated on first delivery)"} />
          </Field>
        </div>
      )}
    </Card>
  );
}

function ApiKeys() {
  const toast = useToast();
  const { data, reload } = useApi(() => api<any[]>("/api-keys"), []);
  const [created, setCreated] = useState<any>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("secret");
  const create = async () => {
    try {
      setCreated(await api("/api-keys", { body: { name: name || `${kind} key`, kind } }));
      setName("");
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };
  const revoke = async (id: string) => {
    if (!confirm("Revoke this key? Integrations using it stop working immediately.")) return;
    await api(`/api-keys/${id}/revoke`, { body: {} });
    reload();
  };
  return (
    <Card title="API keys">
      <div className="mb-4 flex flex-wrap gap-2">
        <Input placeholder="Key name, e.g. CRM integration" value={name} onChange={(e) => setName(e.target.value)} className="!w-64" />
        <Select value={kind} onChange={(e) => setKind(e.target.value)} className="!w-48">
          <option value="secret">Secret (REST API)</option><option value="public">Public (web widget)</option>
        </Select>
        <Button onClick={create}><KeyRound className="h-4 w-4" />Create key</Button>
      </div>
      {!data ? <Spinner /> : (
        <Table rows={data} columns={[
          { header: "Name", cell: (k: any) => k.name },
          { header: "Type", cell: (k: any) => <Badge color={k.kind === "secret" ? "amber" : "blue"}>{k.kind}</Badge> },
          { header: "Key", cell: (k: any) => <code className="text-xs">{k.public_key || `${k.prefix}…`}</code> },
          { header: "Last used", cell: (k: any) => fmt.ago(k.last_used_at) },
          { header: "Status", cell: (k: any) => <Badge color={k.revoked_at ? "slate" : "green"}>{k.revoked_at ? "revoked" : "active"}</Badge> },
          { header: "", className: "text-right", cell: (k: any) => !k.revoked_at && <Button size="sm" variant="ghost" onClick={() => revoke(k.id)}>Revoke</Button> },
        ]} />
      )}
      <Modal open={!!created} onClose={() => setCreated(null)} title="Copy your key now">
        <p className="text-sm text-slate-600">{created?.kind === "secret" ? "This secret key is shown only once. Store it in your server's secret manager." : "Public widget key:"}</p>
        <code className="mt-3 block break-all rounded-lg bg-slate-900 p-3 text-xs text-slate-100">{created?.key}</code>
        {created?.kind === "secret" && (
          <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-50 p-3 text-[11px]">{`curl -X POST ${location.origin}/api/v1/channels/rest/messages \\
  -H "X-API-Key: ${created?.key}" -H "Content-Type: application/json" \\
  -d '{"session_id":"user-42","text":"Hi!"}'`}</pre>
        )}
      </Modal>
    </Card>
  );
}

function Usage() {
  const { data } = useApi(() => api("/usage"), []);
  if (!data) return <Spinner />;
  const pct = data.monthly_quota ? Math.min(100, (100 * data.tokens_used) / data.monthly_quota) : 0;
  return (
    <Card title={`LLM usage · ${data.month}`}>
      <div className="text-3xl font-semibold">{fmt.num(data.tokens_used)} <span className="text-base font-normal text-slate-500">tokens</span></div>
      <div className="mt-1 text-sm text-slate-500">Quota: {data.monthly_quota ? fmt.num(data.monthly_quota) : "unlimited"} · providers: {data.llm_providers.join(" → ") || "none configured (rules-only mode)"}</div>
      <div className="mt-3 h-2.5 rounded bg-slate-100"><div className={pct > 85 ? "h-2.5 rounded bg-rose-500" : "h-2.5 rounded bg-emerald-500"} style={{ width: `${pct}%` }} /></div>
      {data.exceeded && <p className="mt-3 text-sm text-rose-600">Quota exhausted: agents run structured flows and knowledge-base answers without the LLM until next month.</p>}
    </Card>
  );
}

function Audit() {
  const [action, setAction] = useState("");
  const { data } = useApi(() => api("/audit-logs", { query: { action, limit: 100 } }), [action]);
  return (
    <Card title="Audit log" actions={<Input placeholder="Filter, e.g. user." value={action} onChange={(e) => setAction(e.target.value)} className="!w-48 !py-1" />}>
      {!data ? <Spinner /> : (
        <Table rows={data.items} columns={[
          { header: "When", cell: (a: any) => fmt.date(a.created_at) },
          { header: "Action", cell: (a: any) => <code className="text-xs">{a.action}</code> },
          { header: "Actor", cell: (a: any) => a.actor_email || "system" },
          { header: "Resource", cell: (a: any) => <span className="text-xs text-slate-500">{a.resource_type} {a.resource_id?.slice(0, 8)}</span> },
          { header: "IP", cell: (a: any) => <span className="text-xs text-slate-400">{a.ip}</span> },
        ]} />
      )}
    </Card>
  );
}

function Actions() {
  const toast = useToast();
  const [status, setStatus] = useState("");
  const { data, reload } = useApi(() => api("/actions", { query: { status, limit: 100 } }), [status]);
  const retry = async (id: string) => {
    try {
      await api(`/actions/${id}/retry`, { body: {} });
      toast("Retry scheduled");
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };
  return (
    <Card title="Workflow actions" actions={
      <Select value={status} onChange={(e) => setStatus(e.target.value)} className="!w-36 !py-1">
        <option value="">All</option><option value="pending">Pending</option><option value="succeeded">Succeeded</option><option value="failed">Failed</option>
      </Select>}>
      {!data ? <Spinner /> : (
        <Table rows={data.items} columns={[
          { header: "Action", cell: (a: any) => <code className="text-xs">{a.action_type}</code> },
          { header: "Status", cell: (a: any) => <Badge color={statusColor(a.status)}>{a.status}</Badge> },
          { header: "Attempts", cell: (a: any) => `${a.attempts}/${a.max_attempts}` },
          { header: "Run at", cell: (a: any) => fmt.date(a.run_at) },
          { header: "Result / error", cell: (a: any) => <span className="text-xs text-slate-500">{a.last_error || JSON.stringify(a.result).slice(0, 80)}</span> },
          { header: "", className: "text-right", cell: (a: any) => a.status === "failed" && <Button size="sm" variant="ghost" onClick={() => retry(a.id)}><RotateCcw className="h-3.5 w-3.5" />Retry</Button> },
        ]} />
      )}
    </Card>
  );
}
