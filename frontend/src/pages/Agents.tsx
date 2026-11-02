import clsx from "clsx";
import { Bot, LayoutTemplate, MessageCircle, Save, Send, Star } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { atLeast, useAuth } from "../auth";
import {
  Badge, Button, Card, Field, Input, Modal, PageHeader, Spinner, Textarea, useApi, useToast,
} from "../components/ui";

const TOOLS: Record<string, string> = {
  knowledge_search: "Knowledge base (RAG)", capture_lead: "Capture lead", update_lead: "Update CRM lead",
  check_availability: "Check availability", book_appointment: "Book appointment", cancel_appointment: "Cancel appointment",
  send_notification: "Send notification", request_handover: "Hand over to human", recommend_products: "Recommend products",
  create_payment_link: "Payment link", create_ticket: "Create ticket",
};

export default function Agents() {
  const { me } = useAuth();
  const isAdmin = atLeast(me?.role, "tenant_admin");
  const toast = useToast();
  const agents = useApi(() => api("/agents"), []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [gallery, setGallery] = useState(false);
  useEffect(() => {
    if (!selectedId && agents.data?.length) setSelectedId(agents.data[0].id);
  }, [agents.data, selectedId]);
  const selected = agents.data?.find((a: any) => a.id === selectedId);

  return (
    <div className="space-y-6">
      <PageHeader title="AI agents" subtitle="Personas, flows and tools: create from an industry template in one click"
        actions={isAdmin && <Button onClick={() => setGallery(true)}><LayoutTemplate className="h-4 w-4" />New from template</Button>} />
      {agents.loading && !agents.data ? <Spinner /> : (
        <div className="grid gap-6 xl:grid-cols-5">
          <div className="space-y-2 xl:col-span-1">
            {agents.data.map((a: any) => (
              <button key={a.id} onClick={() => setSelectedId(a.id)}
                className={clsx("w-full rounded-xl border bg-white p-3 text-left shadow-sm", selectedId === a.id ? "border-brand ring-2 ring-brand/20" : "border-slate-200")}>
                <div className="flex items-center gap-2 font-medium"><Bot className="h-4 w-4 text-brand" /><span className="truncate">{a.name}</span></div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {a.is_default && <Badge color="brand"><Star className="mr-0.5 h-3 w-3" />default</Badge>}
                  {!a.is_active && <Badge>inactive</Badge>}
                  {a.template_key && <Badge>{a.template_key.replaceAll("_", " ")}</Badge>}
                </div>
              </button>
            ))}
          </div>
          <div className="xl:col-span-2">{selected && <AgentEditor key={selected.id} agent={selected} canEdit={isAdmin} onSaved={agents.reload} />}</div>
          <div className="xl:col-span-2">{selected && <Playground agent={selected} />}</div>
        </div>
      )}
      <TemplateGallery open={gallery} onClose={() => setGallery(false)}
        onCreated={(a) => { setGallery(false); agents.reload(); setSelectedId(a.id); toast(`Agent "${a.name}" created`); }} />
    </div>
  );
}

function AgentEditor({ agent, canEdit, onSaved }: { agent: any; canEdit: boolean; onSaved: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState({ ...agent });
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: any) => setForm({ ...form, [k]: v });
  const rules = form.handover_rules || {};
  const save = async () => {
    setBusy(true);
    try {
      const body: any = {};
      for (const k of ["name", "persona", "greeting", "llm_model", "temperature", "enabled_tools", "is_active", "is_default", "handover_rules"]) {
        if (JSON.stringify(form[k]) !== JSON.stringify(agent[k])) body[k] = form[k];
      }
      if (body.llm_model === "") body.llm_model = null;
      await api(`/agents/${agent.id}`, { method: "PATCH", body });
      toast("Agent saved");
      onSaved();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Configuration" actions={canEdit && <Button size="sm" onClick={save} loading={busy}><Save className="h-3.5 w-3.5" />Save</Button>}>
      <fieldset disabled={!canEdit} className="space-y-4">
        <Field label="Name"><Input value={form.name} onChange={(e) => set("name", e.target.value)} /></Field>
        <Field label="Persona / system prompt" hint="Platform safety rules (scope, prompt-injection guard, grounding) are always added on top.">
          <Textarea rows={6} value={form.persona} onChange={(e) => set("persona", e.target.value)} />
        </Field>
        <Field label="Greeting"><Input value={form.greeting} onChange={(e) => set("greeting", e.target.value)} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="LLM model" hint='Blank = platform default. Pin with "groq:model" or "gemini:model".'>
            <Input value={form.llm_model || ""} onChange={(e) => set("llm_model", e.target.value)} placeholder="platform default" />
          </Field>
          <Field label={`Temperature (${Number(form.temperature).toFixed(1)})`}>
            <input type="range" min={0} max={1.2} step={0.1} value={form.temperature} onChange={(e) => set("temperature", Number(e.target.value))} className="w-full accent-[rgb(var(--brand))]" />
          </Field>
        </div>
        <Field label="Tools the agent may use">
          <div className="grid grid-cols-2 gap-1.5">
            {Object.entries(TOOLS).map(([key, label]) => (
              <label key={key} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.enabled_tools.includes(key)} className="accent-[rgb(var(--brand))]"
                  onChange={(e) => set("enabled_tools", e.target.checked ? [...form.enabled_tools, key] : form.enabled_tools.filter((t: string) => t !== key))} />
                {label}
              </label>
            ))}
          </div>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Hand over below confidence">
            <Input type="number" step={0.05} min={0} max={1} value={rules.low_confidence ?? 0.45}
              onChange={(e) => set("handover_rules", { ...rules, low_confidence: Number(e.target.value) })} />
          </Field>
          <Field label="Hand over below sentiment">
            <Input type="number" step={0.1} min={-1} max={0} value={rules.negative_sentiment ?? -0.6}
              onChange={(e) => set("handover_rules", { ...rules, negative_sentiment: Number(e.target.value) })} />
          </Field>
        </div>
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} />Active</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={form.is_default} onChange={(e) => set("is_default", e.target.checked)} />Default agent for all channels</label>
        </div>
        {agent.qualification_questions?.length > 0 && (
          <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
            <div className="mb-1 font-medium">Lead qualification flow (state machine)</div>
            <ol className="list-decimal pl-4">{agent.qualification_questions.map((q: any) => <li key={q.slot}>{q.question} <span className="text-slate-400">({q.slot})</span></li>)}</ol>
          </div>
        )}
      </fieldset>
    </Card>
  );
}

type Turn = { role: "user" | "bot"; text: string; meta?: any };

function Playground({ agent }: { agent: any }) {
  const [turns, setTurns] = useState<Turn[]>([{ role: "bot", text: agent.greeting }]);
  const [session, setSession] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setTurns([{ role: "bot", text: agent.greeting }]); setSession(null); }, [agent.id, agent.greeting]);
  const send = async (msg = text) => {
    if (!msg.trim()) return;
    setTurns((t) => [...t, { role: "user", text: msg }]);
    setText("");
    setBusy(true);
    try {
      const r = await api(`/agents/${agent.id}/playground`, { body: { message: msg, session_id: session } });
      setSession(r.debug.session_id);
      setTurns((t) => [...t, { role: "bot", text: r.text ?? "(handed over: a human will reply from the Inbox)", meta: r }]);
    } catch (e: any) {
      setTurns((t) => [...t, { role: "bot", text: `Error: ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  };
  const last = [...turns].reverse().find((t) => t.meta)?.meta;
  return (
    <Card title={<span className="flex items-center gap-2"><MessageCircle className="h-4 w-4" />Playground</span>}
      actions={<Button size="sm" variant="ghost" onClick={() => { setTurns([{ role: "bot", text: agent.greeting }]); setSession(null); }}>Reset</Button>}>
      <div className="scrollbar-thin h-80 space-y-2 overflow-y-auto rounded-lg bg-slate-50 p-3">
        {turns.map((t, i) => (
          <div key={i} className={clsx("flex", t.role === "user" ? "justify-end" : "justify-start")}>
            <div className={clsx("max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm", t.role === "user" ? "bg-brand text-white" : "bg-white shadow-sm")}>
              {t.text}
              {t.meta?.citations?.length > 0 && <div className="mt-1 text-[11px] text-slate-500">Sources: {[...new Set(t.meta.citations.map((c: any) => c.title))].join(", ")}</div>}
              {t.meta?.quick_replies?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {t.meta.quick_replies.map((q: string) => <button key={q} onClick={() => send(q)} className="rounded-full border border-brand/40 px-2 py-0.5 text-[11px] text-brand">{q}</button>)}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && <div className="text-xs text-slate-400">Thinking…</div>}
      </div>
      <div className="mt-3 flex gap-2">
        <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Message the agent…" onKeyDown={(e) => e.key === "Enter" && send()} />
        <Button onClick={() => send()} loading={busy}><Send className="h-4 w-4" /></Button>
      </div>
      {last && (
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg border border-slate-200 p-3 text-[11px] text-slate-600">
          <span>Intent: <b>{last.intent}</b> ({Math.round((last.confidence ?? 0) * 100)}%)</span>
          <span>Decision: <b>{last.debug.decision}</b> via {last.debug.nlu_source}</span>
          <span>Tools: {last.debug.tools.map((t: any) => t.name).join(", ") || "none"}</span>
          <span>RAG: {last.debug.rag || "—"} {last.debug.rag_confidence != null && `(${last.debug.rag_confidence})`}</span>
          <span>Latency: {last.latency_ms} ms {last.debug.cache_hit && "(cache)"}</span>
          <span>Flow: {last.debug.flow || "—"} · tokens {last.debug.tokens}</span>
        </div>
      )}
    </Card>
  );
}

function TemplateGallery({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (a: any) => void }) {
  const toast = useToast();
  const { data } = useApi(() => (open ? api("/agent-templates") : Promise.resolve(null)), [open]);
  const [busy, setBusy] = useState<string | null>(null);
  const [makeDefault, setMakeDefault] = useState(true);
  const create = async (key: string) => {
    setBusy(key);
    try {
      onCreated(await api("/agents/from-template", { body: { template_key: key, make_default: makeDefault } }));
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(null);
    }
  };
  return (
    <Modal open={open} onClose={onClose} title="Industry templates" wide>
      {!data ? <Spinner /> : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {data.map((t: any) => (
              <div key={t.key} className="flex flex-col rounded-xl border border-slate-200 p-4">
                <div className="font-medium">{t.name}</div>
                <div className="text-xs text-slate-400">{t.industry}</div>
                <p className="mt-2 flex-1 text-sm text-slate-600">{t.description}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {t.intents.slice(0, 4).map((i: any) => <Badge key={i.name}>{i.name.replaceAll("_", " ")}</Badge>)}
                </div>
                <div className="mt-2 text-[11px] text-slate-400">Integrations: {t.recommended_integrations.join(", ")}</div>
                <Button className="mt-3" size="sm" onClick={() => create(t.key)} loading={busy === t.key}>Use template</Button>
              </div>
            ))}
          </div>
          <label className="mt-4 flex items-center gap-2 text-sm"><input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} />Make it the default agent for all channels</label>
        </>
      )}
    </Modal>
  );
}

