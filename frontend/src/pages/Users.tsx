import { UserPlus } from "lucide-react";
import { useState } from "react";
import { api, fmt } from "../api";
import { useAuth, type Role } from "../auth";
import { Badge, Button, Card, Field, Input, Modal, PageHeader, Select, Spinner, Table, useApi, useToast } from "../components/ui";

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: "tenant_admin", label: "Admin", hint: "Everything, including team, branding and settings" },
  { value: "agent", label: "Agent", hint: "Inbox, leads and appointments" },
  { value: "viewer", label: "Viewer", hint: "Read-only dashboards and data" },
];

export default function UsersPage() {
  const { me } = useAuth();
  const toast = useToast();
  const { data, loading, reload } = useApi(() => api("/users", { query: { limit: 200 } }), []);
  const [inviting, setInviting] = useState(false);
  const patch = async (id: string, body: object) => {
    try {
      await api(`/users/${id}`, { method: "PATCH", body });
      toast("User updated");
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };
  return (
    <div>
      <PageHeader title="Team" subtitle="People who can use this workspace"
        actions={<Button onClick={() => setInviting(true)}><UserPlus className="h-4 w-4" />Add member</Button>} />
      <Card>
        {loading && !data ? <Spinner /> : (
          <Table rows={data.items}
            columns={[
              { header: "Name", cell: (u: any) => <div><div className="font-medium">{u.full_name || "—"}</div><div className="text-xs text-slate-400">{u.email}</div></div> },
              {
                header: "Role", cell: (u: any) => u.id === me?.user.id || u.role === "super_admin" ? <Badge color="brand">{u.role.replace("_", " ")}</Badge> : (
                  <Select value={u.role} onChange={(e) => patch(u.id, { role: e.target.value })} className="!w-36 !py-1">
                    {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </Select>
                ),
              },
              { header: "Status", cell: (u: any) => <Badge color={u.is_active ? "green" : "slate"}>{u.is_active ? "active" : "disabled"}</Badge> },
              { header: "Last login", cell: (u: any) => fmt.ago(u.last_login_at) },
              {
                header: "", className: "text-right", cell: (u: any) => u.id !== me?.user.id && u.role !== "super_admin" && (
                  <Button size="sm" variant="ghost" onClick={() => patch(u.id, { is_active: !u.is_active })}>{u.is_active ? "Disable" : "Enable"}</Button>
                ),
              },
            ]} />
        )}
      </Card>
      <Invite open={inviting} onClose={() => setInviting(false)} onDone={() => { setInviting(false); reload(); }} />
    </div>
  );
}

function Invite({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState({ email: "", full_name: "", role: "agent", password: "" });
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await api("/users", { body: form });
      toast(`${form.email} added`);
      setForm({ email: "", full_name: "", role: "agent", password: "" });
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  const set = (k: string) => (e: any) => setForm({ ...form, [k]: e.target.value });
  return (
    <Modal open={open} onClose={onClose} title="Add team member">
      <div className="space-y-3">
        <Field label="Email"><Input type="email" value={form.email} onChange={set("email")} /></Field>
        <Field label="Full name"><Input value={form.full_name} onChange={set("full_name")} /></Field>
        <Field label="Role" hint={ROLES.find((r) => r.value === form.role)?.hint}>
          <Select value={form.role} onChange={set("role")}>{ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}</Select>
        </Field>
        <Field label="Temporary password" hint="At least 8 characters with letters and numbers. They can change it after signing in.">
          <Input type="password" value={form.password} onChange={set("password")} />
        </Field>
        <div className="flex justify-end"><Button onClick={submit} loading={busy}>Add member</Button></div>
      </div>
    </Modal>
  );
}
