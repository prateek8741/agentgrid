import { CalendarPlus } from "lucide-react";
import { useState } from "react";
import { api, fmt } from "../api";
import { atLeast, useAuth } from "../auth";
import {
  Badge, Button, Card, Field, Input, Modal, PageHeader, Spinner, Table, statusColor, useApi, useToast,
} from "../components/ui";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function Appointments() {
  const { me } = useAuth();
  const toast = useToast();
  const [upcoming, setUpcoming] = useState(true);
  const [genOpen, setGenOpen] = useState(false);
  const appts = useApi(() => api("/appointments", { query: { upcoming, limit: 100 } }), [upcoming]);
  const slots = useApi(() => api("/slots", { query: { days: 14 } }), []);

  const update = async (id: string, status: string) => {
    try {
      await api(`/appointments/${id}`, { method: "PATCH", body: { status } });
      toast(`Appointment ${status.replace("_", " ")}`);
      appts.reload();
      slots.reload();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const byDay: Record<string, any[]> = {};
  (slots.data || []).forEach((s: any) => {
    const d = new Date(s.start_at).toDateString();
    (byDay[d] ||= []).push(s);
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Appointments" subtitle="Booked by the AI on any channel, with automatic reminders"
        actions={atLeast(me?.role, "tenant_admin") && <Button onClick={() => setGenOpen(true)}><CalendarPlus className="h-4 w-4" />Add availability</Button>} />
      <Card title={upcoming ? "Upcoming appointments" : "All appointments"}
        actions={<Button size="sm" variant="secondary" onClick={() => setUpcoming(!upcoming)}>{upcoming ? "Show all" : "Upcoming only"}</Button>}>
        {appts.loading && !appts.data ? <Spinner /> : (
          <Table rows={appts.data?.items || []} empty="No appointments yet"
            columns={[
              { header: "When", cell: (a: any) => <span className="font-medium">{fmt.date(a.start_at)}</span> },
              { header: "Customer", cell: (a: any) => <div>{a.customer_name}<div className="text-xs text-slate-400">{a.customer_phone || a.customer_email}</div></div> },
              { header: "Service", cell: (a: any) => a.service || "—" },
              { header: "Reference", cell: (a: any) => <code className="text-xs">{a.reference}</code> },
              { header: "Status", cell: (a: any) => <Badge color={statusColor(a.status)}>{a.status.replace("_", " ")}</Badge> },
              {
                header: "", className: "text-right", cell: (a: any) => a.status === "booked" && atLeast(me?.role, "agent") ? (
                  <div className="flex justify-end gap-1">
                    <Button size="sm" variant="secondary" onClick={() => update(a.id, "completed")}>Done</Button>
                    <Button size="sm" variant="ghost" onClick={() => update(a.id, "no_show")}>No-show</Button>
                    <Button size="sm" variant="ghost" onClick={() => update(a.id, "cancelled")}>Cancel</Button>
                  </div>
                ) : null,
              },
            ]} />
        )}
      </Card>
      <Card title="Availability (next 14 days)">
        {slots.loading && !slots.data ? <Spinner /> : Object.keys(byDay).length === 0 ? (
          <p className="text-sm text-slate-500">No availability yet. Add recurring slots so the AI can book appointments.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Object.entries(byDay).map(([day, list]) => (
              <div key={day} className="rounded-lg border border-slate-200 p-3">
                <div className="mb-2 text-xs font-semibold text-slate-600">{day}</div>
                <div className="flex flex-wrap gap-1">
                  {list.map((s) => (
                    <span key={s.id} title={s.resource || ""}
                      className={`rounded px-1.5 py-0.5 text-[11px] ${s.booked_count >= s.capacity ? "bg-slate-200 text-slate-400 line-through" : "bg-emerald-50 text-emerald-700"}`}>
                      {new Date(s.start_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
      <GenerateSlots open={genOpen} onClose={() => setGenOpen(false)} onDone={() => { setGenOpen(false); slots.reload(); }} />
    </div>
  );
}

function GenerateSlots({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const inTwoWeeks = new Date(Date.now() + 13 * 86400000).toISOString().slice(0, 10);
  const [form, setForm] = useState({ start_date: today, end_date: inTwoWeeks, day_start: "10:00", day_end: "18:00",
    slot_minutes: 30, capacity: 1, resource: "", service: "" });
  const [weekdays, setWeekdays] = useState([0, 1, 2, 3, 4, 5]);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const r = await api("/slots/generate", { body: { ...form, weekdays, resource: form.resource || null, service: form.service || null } });
      toast(`${r.created} slots created`);
      onDone();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  const set = (k: string) => (e: any) => setForm({ ...form, [k]: e.target.type === "number" ? Number(e.target.value) : e.target.value });
  return (
    <Modal open={open} onClose={onClose} title="Add recurring availability">
      <div className="grid grid-cols-2 gap-3">
        <Field label="From"><Input type="date" value={form.start_date} onChange={set("start_date")} /></Field>
        <Field label="To"><Input type="date" value={form.end_date} onChange={set("end_date")} /></Field>
        <Field label="Day starts"><Input type="time" value={form.day_start} onChange={set("day_start")} /></Field>
        <Field label="Day ends"><Input type="time" value={form.day_end} onChange={set("day_end")} /></Field>
        <Field label="Slot length (minutes)"><Input type="number" min={10} value={form.slot_minutes} onChange={set("slot_minutes")} /></Field>
        <Field label="Capacity per slot"><Input type="number" min={1} value={form.capacity} onChange={set("capacity")} /></Field>
        <Field label="Resource (optional)" hint="e.g. Dr. Mehta"><Input value={form.resource} onChange={set("resource")} /></Field>
        <Field label="Service (optional)"><Input value={form.service} onChange={set("service")} /></Field>
        <div className="col-span-2">
          <Field label="Days of week">
            <div className="flex gap-1">
              {DAYS.map((d, i) => (
                <Button key={d} size="sm" variant={weekdays.includes(i) ? "primary" : "secondary"}
                  onClick={() => setWeekdays(weekdays.includes(i) ? weekdays.filter((x) => x !== i) : [...weekdays, i])}>{d}</Button>
              ))}
            </div>
          </Field>
        </div>
      </div>
      <div className="mt-4 flex justify-end"><Button onClick={submit} loading={busy}>Create slots</Button></div>
    </Modal>
  );
}
