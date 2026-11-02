import { Flame, Plus } from "lucide-react";
import { useState } from "react";
import { api, fmt } from "../api";
import { atLeast, useAuth } from "../auth";
import {
  Badge, Button, Field, Input, Modal, PageHeader, Select, Spinner, Textarea, statusColor, useApi, useToast,
} from "../components/ui";

const STAGES = ["new", "contacted", "qualified", "proposal", "won", "lost"] as const;
type Lead = {
  id: string; name: string | null; email: string | null; phone: string | null; need: string | null; budget: string | null;
  timeline: string | null; score: number; temperature: string; status: string; source_channel: string | null;
  notes: string | null; custom: { score_reasons?: string[] }; created_at: string;
};

export default function Leads() {
  const { me } = useAuth();
  const canEdit = atLeast(me?.role, "agent");
  const toast = useToast();
  const [temp, setTemp] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Lead | null>(null);
  const [adding, setAdding] = useState(false);
  const { data, loading, reload } = useApi(
    () => api("/leads", { query: { temperature: temp, q, limit: 200 } }), [temp, q],
  );

  const move = async (lead: Lead, status: string) => {
    try {
      await api(`/leads/${lead.id}`, { method: "PATCH", body: { status } });
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const leads: Lead[] = data?.items || [];
  return (
    <div>
      <PageHeader title="Leads" subtitle="Captured and scored by your AI agents across every channel"
        actions={
          <>
            <Input placeholder="Search name, phone, need…" value={q} onChange={(e) => setQ(e.target.value)} className="!w-56" />
            <Select value={temp} onChange={(e) => setTemp(e.target.value)} className="!w-36">
              <option value="">All temperatures</option><option value="hot">Hot</option><option value="warm">Warm</option><option value="cold">Cold</option>
            </Select>
            {canEdit && <Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" />Add lead</Button>}
          </>
        } />
      {loading && !data ? <Spinner /> : (
        <div className="grid gap-3 overflow-x-auto pb-2 md:grid-cols-3 xl:grid-cols-6">
          {STAGES.map((stage) => {
            const col = leads.filter((l) => l.status === stage);
            return (
              <div key={stage} className="min-w-[200px] rounded-xl bg-slate-100/70 p-2">
                <div className="mb-2 flex items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {stage}<span className="rounded bg-white px-1.5 text-[11px]">{col.length}</span>
                </div>
                <div className="space-y-2">
                  {col.map((l) => (
                    <button key={l.id} onClick={() => setOpen(l)} className="block w-full rounded-lg border border-slate-200 bg-white p-3 text-left shadow-sm hover:border-brand/40">
                      <div className="flex items-center justify-between gap-1">
                        <span className="truncate text-sm font-medium">{l.name || l.phone || "Unnamed lead"}</span>
                        <Badge color={statusColor(l.temperature)}>{l.temperature === "hot" && <Flame className="mr-0.5 h-3 w-3" />}{l.score}</Badge>
                      </div>
                      <div className="mt-1 line-clamp-2 text-xs text-slate-500">{l.need || "—"}</div>
                      <div className="mt-2 flex justify-between text-[11px] text-slate-400">
                        <span>{l.budget || ""}</span><span>{l.source_channel}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <LeadModal lead={open} onClose={() => setOpen(null)} canEdit={canEdit}
        onSaved={() => { setOpen(null); reload(); }} onMove={move} />
      <AddLead open={adding} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />
    </div>
  );
}

function LeadModal({ lead, onClose, canEdit, onSaved, onMove }: {
  lead: Lead | null; onClose: () => void; canEdit: boolean; onSaved: () => void; onMove: (l: Lead, s: string) => void;
}) {
  const toast = useToast();
  const [notes, setNotes] = useState("");
  if (!lead) return null;
  const save = async () => {
    try {
      await api(`/leads/${lead.id}`, { method: "PATCH", body: { notes: notes || lead.notes } });
      toast("Lead updated");
      onSaved();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };
  return (
    <Modal open onClose={onClose} title={lead.name || "Lead"}>
      <div className="space-y-4 text-sm">
        <div className="flex items-center gap-2">
          <Badge color={statusColor(lead.temperature)}>{lead.temperature} · score {lead.score}</Badge>
          <Badge color={statusColor(lead.status)}>{lead.status}</Badge>
          <span className="text-xs text-slate-400">via {lead.source_channel} · {fmt.date(lead.created_at)}</span>
        </div>
        <dl className="grid grid-cols-2 gap-3">
          {(["phone", "email", "need", "budget", "timeline"] as const).map((k) => (
            <div key={k} className={k === "need" ? "col-span-2" : ""}>
              <dt className="text-xs text-slate-500">{k}</dt><dd>{lead[k] || "—"}</dd>
            </div>
          ))}
        </dl>
        {lead.custom?.score_reasons && (
          <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
            <div className="mb-1 font-medium">Why this score</div>
            {lead.custom.score_reasons.join(" · ")}
          </div>
        )}
        {canEdit && (
          <>
            <Field label="Pipeline stage">
              <div className="flex flex-wrap gap-1.5">
                {STAGES.map((s) => (
                  <Button key={s} size="sm" variant={lead.status === s ? "primary" : "secondary"} onClick={() => onMove(lead, s)}>{s}</Button>
                ))}
              </div>
            </Field>
            <Field label="Notes"><Textarea defaultValue={lead.notes || ""} onChange={(e) => setNotes(e.target.value)} /></Field>
            <div className="flex justify-end"><Button onClick={save}>Save</Button></div>
          </>
        )}
      </div>
    </Modal>
  );
}

function AddLead({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState<Record<string, string>>({});
  const set = (k: string) => (e: any) => setForm({ ...form, [k]: e.target.value });
  const save = async () => {
    try {
      await api("/leads", { body: form });
      toast("Lead added");
      setForm({});
      onSaved();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };
  return (
    <Modal open={open} onClose={onClose} title="Add lead">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name"><Input onChange={set("name")} /></Field>
        <Field label="Phone"><Input onChange={set("phone")} /></Field>
        <Field label="Email"><Input type="email" onChange={set("email")} /></Field>
        <Field label="Budget"><Input placeholder="e.g. 80 lakh" onChange={set("budget")} /></Field>
        <div className="col-span-2"><Field label="Need"><Input onChange={set("need")} /></Field></div>
        <div className="col-span-2"><Field label="Timeline"><Input placeholder="e.g. within a month" onChange={set("timeline")} /></Field></div>
      </div>
      <div className="mt-4 flex justify-end"><Button onClick={save}>Add lead</Button></div>
    </Modal>
  );
}
