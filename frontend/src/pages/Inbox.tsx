import clsx from "clsx";
import { Bot, Hand, Inbox as InboxIcon, RotateCcw, Send, UserCheck, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmt, getTokens, wsUrl } from "../api";
import { useAuth } from "../auth";
import { Badge, Button, Empty, PageHeader, Spinner, Textarea, statusColor, useToast } from "../components/ui";

type Item = {
  id: string; channel: string; status: string; session_id: string; handover_reason: string | null;
  assigned_user_id: string | null; last_message_at: string; last_intent: string | null; sentiment: number | null;
  contact: { name: string | null; phone: string | null; email: string | null };
  last_message: { role: string; text: string } | null; message_count: number;
};
type Msg = { id: string; role: string; content: string; intent: string | null; confidence: number | null;
  created_at: string; citations: any[]; latency_ms: number | null };

const TABS = [
  { key: "handed_over", label: "Needs a human" }, { key: "active", label: "AI handling" },
  { key: "closed", label: "Closed" }, { key: "", label: "All" },
];
const CHANNEL_LABEL: Record<string, string> = {
  web: "Web chat", api: "API", whatsapp: "WhatsApp", messenger: "Messenger", playground: "Playground",
};

function useInboxSocket(onEvent: (e: any) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const [live, setLive] = useState(false);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let stopped = false;
    let ping: number | undefined;
    const connect = () => {
      const token = getTokens()?.access_token;
      if (!token || stopped) return;
      ws = new WebSocket(wsUrl(`/inbox/ws?token=${encodeURIComponent(token)}`));
      ws.onopen = () => { setLive(true); retry = 0; };
      ws.onmessage = (m) => { try { handler.current(JSON.parse(m.data)); } catch { /* ignore */ } };
      ws.onclose = () => {
        setLive(false);
        if (!stopped) setTimeout(connect, Math.min(30000, 1000 * 2 ** retry++));
      };
    };
    connect();
    ping = window.setInterval(() => ws?.readyState === 1 && ws.send(JSON.stringify({ type: "ping" })), 25000);
    return () => { stopped = true; clearInterval(ping); ws?.close(); };
  }, []);
  return live;
}

export default function InboxPage() {
  const { me } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState("handed_over");
  const [items, setItems] = useState<Item[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<Item | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  const loadList = useCallback(async () => {
    const r = await api("/inbox/conversations", { query: { status: tab, limit: 100 } });
    setItems(r.items);
    setCounts(r.counts);
  }, [tab]);
  const loadMessages = useCallback(async (id: string) => {
    setMessages(await api(`/conversations/${id}/messages`));
    setTimeout(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), 50);
  }, []);

  useEffect(() => { loadList().catch((e) => toast(e.message, "error")); }, [loadList, toast]);
  useEffect(() => { if (selected) loadMessages(selected.id).catch(() => undefined); }, [selected?.id, loadMessages]);

  const live = useInboxSocket((e) => {
    loadList().catch(() => undefined);
    if (selected && e.conversation_id === selected.id) {
      loadMessages(selected.id).catch(() => undefined);
      if (e.status) setSelected((s) => (s ? { ...s, status: e.status } : s));
    }
    if (e.type === "handover.requested") toast(`New handover: ${e.reason?.replace("_", " ") || "customer"} (${e.channel})`);
  });

  const act = async (path: string, body?: unknown) => {
    if (!selected) return;
    setBusy(true);
    try {
      const conv = await api(`/inbox/conversations/${selected.id}/${path}`, { body: body ?? {} });
      if (conv?.status) setSelected({ ...selected, status: conv.status, assigned_user_id: conv.assigned_user_id });
      await Promise.all([loadList(), loadMessages(selected.id)]);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  const send = async () => {
    if (!draft.trim()) return;
    await act("reply", { text: draft.trim() });
    setDraft("");
  };

  return (
    <div className="flex h-[calc(100vh-7.5rem)] flex-col">
      <PageHeader title="Inbox" subtitle="Live conversations that need a human, plus everything the AI is handling"
        actions={<Badge color={live ? "green" : "slate"}>{live ? "● Live" : "Connecting…"}</Badge>} />
      <div className="flex min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex w-80 shrink-0 flex-col border-r border-slate-200">
          <div className="flex gap-1 border-b border-slate-100 p-2">
            {TABS.map((t) => (
              <button key={t.key} onClick={() => { setTab(t.key); setSelected(null); }}
                className={clsx("flex-1 rounded-md px-1.5 py-1.5 text-xs", tab === t.key ? "bg-brand/10 font-medium text-brand" : "text-slate-500 hover:bg-slate-50")}>
                {t.label}{t.key && counts[t.key] ? <span className="ml-1 text-[10px] opacity-70">{counts[t.key]}</span> : null}
              </button>
            ))}
          </div>
          <div className="scrollbar-thin flex-1 overflow-y-auto">
            {items === null ? <Spinner /> : items.length === 0 ? (
              <Empty icon={<InboxIcon className="h-8 w-8" />} title="All caught up">No conversations in this view.</Empty>
            ) : items.map((c) => (
              <button key={c.id} onClick={() => setSelected(c)}
                className={clsx("block w-full border-b border-slate-100 px-4 py-3 text-left hover:bg-slate-50", selected?.id === c.id && "bg-brand/5")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{c.contact.name || c.contact.phone || c.contact.email || "Visitor"}</span>
                  <span className="shrink-0 text-[11px] text-slate-400">{fmt.ago(c.last_message_at)}</span>
                </div>
                <div className="mt-0.5 truncate text-xs text-slate-500">{c.last_message?.text || "—"}</div>
                <div className="mt-1.5 flex gap-1">
                  <Badge>{CHANNEL_LABEL[c.channel] || c.channel}</Badge>
                  <Badge color={statusColor(c.status)}>{c.status.replace("_", " ")}</Badge>
                  {c.handover_reason && c.status === "handed_over" && <Badge color="red">{c.handover_reason.replace("_", " ")}</Badge>}
                </div>
              </button>
            ))}
          </div>
        </div>

        {!selected ? (
          <div className="flex flex-1 items-center justify-center">
            <Empty icon={<InboxIcon className="h-10 w-10" />} title="Select a conversation">
              Handovers appear here instantly. Reply from here and your message is delivered on the customer's channel.
            </Empty>
          </div>
        ) : (
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-3">
              <div>
                <div className="font-medium">{selected.contact.name || "Visitor"}</div>
                <div className="text-xs text-slate-500">
                  {[selected.contact.phone, selected.contact.email, CHANNEL_LABEL[selected.channel]].filter(Boolean).join(" · ")}
                  {selected.sentiment != null && ` · sentiment ${selected.sentiment.toFixed(2)}`}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {selected.status !== "handed_over" && selected.status !== "closed" && (
                  <Button size="sm" variant="secondary" onClick={() => act("takeover")} loading={busy}><Hand className="h-3.5 w-3.5" />Take over</Button>
                )}
                {selected.status === "handed_over" && selected.assigned_user_id !== me?.user.id && (
                  <Button size="sm" variant="secondary" onClick={() => act("assign", {})} loading={busy}><UserCheck className="h-3.5 w-3.5" />Assign to me</Button>
                )}
                {selected.status === "handed_over" && (
                  <Button size="sm" variant="secondary" onClick={() => act("handback")} loading={busy}><RotateCcw className="h-3.5 w-3.5" />Hand back to AI</Button>
                )}
                {selected.status !== "closed" && (
                  <Button size="sm" variant="ghost" onClick={() => act("close")} loading={busy}><XCircle className="h-3.5 w-3.5" />Close</Button>
                )}
              </div>
            </div>
            <div className="scrollbar-thin flex-1 space-y-3 overflow-y-auto bg-slate-50 px-5 py-4">
              {messages.map((m) => (
                <div key={m.id} className={clsx("flex", m.role === "user" ? "justify-start" : "justify-end")}>
                  <div className={clsx("max-w-[75%] rounded-2xl px-3.5 py-2 text-sm shadow-sm",
                    m.role === "user" && "rounded-bl-sm bg-white",
                    m.role === "assistant" && "rounded-br-sm bg-brand/10",
                    m.role === "human_agent" && "rounded-br-sm bg-emerald-100")}>
                    <div className="mb-0.5 flex items-center gap-1 text-[10px] uppercase tracking-wide text-slate-400">
                      {m.role === "assistant" && <Bot className="h-3 w-3" />}
                      {m.role === "user" ? "Customer" : m.role === "assistant" ? "AI agent" : "Team"} · {fmt.time(m.created_at)}
                      {m.intent && m.role === "user" && <span className="normal-case">· {m.intent} {m.confidence != null && `(${Math.round(m.confidence * 100)}%)`}</span>}
                    </div>
                    <div className="whitespace-pre-wrap">{m.content}</div>
                    {m.citations?.length > 0 && <div className="mt-1 text-[11px] text-slate-500">Sources: {[...new Set(m.citations.map((c) => c.title))].join(", ")}</div>}
                  </div>
                </div>
              ))}
              <div ref={bottom} />
            </div>
            {selected.status !== "closed" ? (
              <div className="flex gap-2 border-t border-slate-100 p-3">
                <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Reply to the customer… (Enter to send)"
                  className="!min-h-[44px] resize-none" rows={1}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
                <Button onClick={send} loading={busy} disabled={!draft.trim()}><Send className="h-4 w-4" />Send</Button>
              </div>
            ) : <div className="border-t p-3 text-center text-xs text-slate-400">Conversation closed. A new customer message reopens it.</div>}
            {selected.status === "active" && <div className="px-4 pb-2 text-[11px] text-slate-400">Replying takes the conversation over from the AI.</div>}
          </div>
        )}
      </div>
    </div>
  );
}
