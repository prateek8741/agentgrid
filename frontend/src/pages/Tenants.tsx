import { ArrowLeftRight, Building2, Plus } from "lucide-react";
import { useState } from "react";
import { api, fmt } from "../api";
import { useAuth } from "../auth";
import { Badge, Button, Card, Field, Input, Modal, PageHeader, Select, Spinner, Table, statusColor, useApi, useToast } from "../components/ui";

export default function Tenants() {
  const { me, switchTenant } = useAuth();
  const toast = useToast();
  const isSuper = me?.role === "super_admin";
  const atAgency = me?.tenant.kind === "agency";
  const actingAsClient = me && me.tenant.id !== me.home_tenant_id;
  const endpoint = isSuper ? "/analytics/platform" : "/analytics/agency";
  const { data, loading, error, reload } = useApi(
    () => (isSuper || atAgency ? api<any[]>(endpoint, { query: { days: 30 } }) : Promise.resolve([])), [endpoint, me?.tenant.id],
  );
  const [creating, setCreating] = useState(false);

  const setStatus = async (id: string, status: string) => {
    try {
      await api(`/tenants/${id}`, { method: "PATCH", body: { status } });
      toast(`Tenant ${status}`);
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  if (!isSuper && !atAgency) {
    return (
      <Card>
        <p className="text-sm text-slate-600">You are working inside a client workspace.</p>
        {actingAsClient && <Button className="mt-3" onClick={() => switchTenant(me!.home_tenant_id)}><ArrowLeftRight className="h-4 w-4" />Back to my agency</Button>}
      </Card>
    );
  }
  return (
    <div>
      <PageHeader title={isSuper ? "Tenants" : "Clients"}
        subtitle={isSuper ? "Every business and agency on the platform (last 30 days)" : "Your client workspaces, usage and KPIs (reseller view, last 30 days)"}
        actions={<Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" />{isSuper ? "New tenant" : "New client"}</Button>} />
      <Card>
        {loading && !data ? <Spinner /> : error ? <p className="text-sm text-rose-600">{error}</p> : (
          <Table rows={data || []} empty="No clients yet"
            columns={[
              { header: "Name", cell: (t: any) => <div className="flex items-center gap-2"><Building2 className="h-4 w-4 text-slate-400" /><span className="font-medium">{t.name}</span>{isSuper && <Badge>{t.kind}</Badge>}</div> },
              { header: "Status", cell: (t: any) => <Badge color={statusColor(t.status)}>{t.status}</Badge> },
              { header: "Conversations", cell: (t: any) => fmt.num(t.conversations) },
              { header: "Leads", cell: (t: any) => fmt.num(t.leads) },
              { header: "Appts", cell: (t: any) => fmt.num(t.appointments) },
              { header: "AI resolution", cell: (t: any) => fmt.pct(t.automated_resolution_rate) },
              { header: "Tokens (month)", cell: (t: any) => <div>{fmt.num(t.tokens_month)}{t.quota_used_pct != null && <div className="text-[11px] text-slate-400">{t.quota_used_pct}% of quota</div>}</div> },
              {
                header: "", className: "text-right", cell: (t: any) => (
                  <div className="flex justify-end gap-1">
                    <Button size="sm" variant="secondary" onClick={() => switchTenant(t.tenant_id)}><ArrowLeftRight className="h-3.5 w-3.5" />Open</Button>
                    <Button size="sm" variant="ghost" onClick={() => setStatus(t.tenant_id, t.status === "active" ? "suspended" : "active")}>
                      {t.status === "active" ? "Suspend" : "Activate"}
                    </Button>
                  </div>
                ),
              },
            ]} />
        )}
      </Card>
      <CreateTenant open={creating} isSuper={isSuper} onClose={() => setCreating(false)} onDone={() => { setCreating(false); reload(); }} />
    </div>
  );
}

function CreateTenant({ open, isSuper, onClose, onDone }: { open: boolean; isSuper: boolean; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState({ name: "", kind: "business", industry: "", email: "", password: "", full_name: "" });
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: any) => setForm({ ...form, [k]: e.target.value });
  const submit = async () => {
    setBusy(true);
    try {
      await api("/tenants", {
        body: {
          name: form.name, kind: isSuper ? form.kind : "business", industry: form.industry || null,
          admin: form.email ? { email: form.email, password: form.password, full_name: form.full_name } : null,
        },
      });
      toast(`${form.name} created with a default agent and widget key`);
      setForm({ name: "", kind: "business", industry: "", email: "", password: "", full_name: "" });
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={onClose} title={isSuper ? "New tenant" : "New client workspace"}>
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2"><Field label="Business name"><Input value={form.name} onChange={set("name")} /></Field></div>
        {isSuper && (
          <Field label="Type"><Select value={form.kind} onChange={set("kind")}><option value="business">Business</option><option value="agency">Agency (reseller)</option></Select></Field>
        )}
        <Field label="Industry"><Input value={form.industry} onChange={set("industry")} placeholder="e.g. Healthcare" /></Field>
        <div className="col-span-2 mt-2 text-xs font-medium text-slate-500">First admin (optional)</div>
        <Field label="Admin email"><Input type="email" value={form.email} onChange={set("email")} /></Field>
        <Field label="Admin name"><Input value={form.full_name} onChange={set("full_name")} /></Field>
        <div className="col-span-2"><Field label="Temporary password"><Input type="password" value={form.password} onChange={set("password")} /></Field></div>
      </div>
      <div className="mt-4 flex justify-end"><Button onClick={submit} loading={busy} disabled={form.name.length < 2}>Create</Button></div>
    </Modal>
  );
}
