import { Activity, Bot, CalendarCheck, Clock, Coins, Gauge, MessagesSquare, ShieldAlert, Target } from "lucide-react";
import { useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, fmt } from "../api";
import { Card, PageHeader, Select, Spinner, Stat, useApi } from "../components/ui";

const BRAND = "rgb(var(--brand))";
const PALETTE = ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#0EA5E9", "#8B5CF6"];

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const { data, loading, error } = useApi(() => api("/analytics/overview", { query: { days } }), [days]);
  if (loading && !data) return <Spinner />;
  if (error || !data) return <p className="text-rose-600">{error}</p>;
  const k = data.kpis;
  const fr = k.first_response_ms;
  const channels = Object.entries(data.by_channel).map(([name, value]) => ({ name, value }));
  const pipeline = Object.entries(data.pipeline).map(([stage, count]) => ({ stage, count }));
  const daily = data.daily.map((d: any) => ({ ...d, label: d.day.slice(5) }));
  const quotaPct = data.quota.monthly_quota ? Math.min(100, (100 * data.quota.used) / data.quota.monthly_quota) : 0;

  return (
    <div className="space-y-6">
      <PageHeader title="Dashboard" subtitle={`${data.tenant.name} · last ${days} days`}
        actions={
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))} className="!w-36">
            <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
          </Select>
        } />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Conversations" value={fmt.num(k.conversations)} icon={<MessagesSquare className="h-4 w-4" />}
          sub={`${fmt.num(k.tokens.tokens_per_conversation)} tokens / conversation`} />
        <Stat label="First response (avg)" value={fmt.ms(fr.avg)} icon={<Clock className="h-4 w-4" />}
          tone={fr.web_within_target_rate >= 0.9 ? "good" : "warn"}
          sub={`${fmt.pct(fr.web_within_target_rate)} of web chats under ${fr.target_ms / 1000}s · p90 ${fmt.ms(fr.p90)}`} />
        <Stat label="Automated resolution" value={fmt.pct(k.automated_resolution_rate)} icon={<Bot className="h-4 w-4" />}
          sub={`${fmt.pct(k.containment_rate)} handled without a human`} tone="good" />
        <Stat label="Escalation rate" value={fmt.pct(k.escalation_rate)} icon={<ShieldAlert className="h-4 w-4" />}
          sub={Object.entries(data.handover_reasons).map(([r, n]) => `${r.replace("_", " ")} ${n}`).join(" · ") || "no handovers"} />
        <Stat label="Leads captured" value={fmt.num(k.leads)} icon={<Target className="h-4 w-4" />}
          sub={`${fmt.pct(k.lead_conversion_rate)} of conversations → lead`} />
        <Stat label="Lead qualification rate" value={fmt.pct(k.lead_qualification_rate)} icon={<Gauge className="h-4 w-4" />}
          sub={`${fmt.num(k.leads_won)} won`} tone="good" />
        <Stat label="Appointments booked" value={fmt.num(k.appointments_booked)} icon={<CalendarCheck className="h-4 w-4" />}
          sub={`${fmt.pct(k.appointment_conversion_rate)} booking-intent conversion`} />
        <Stat label="LLM tokens" value={fmt.num(k.tokens.total)} icon={<Coins className="h-4 w-4" />}
          sub={`≈ $${k.tokens.estimated_cost_usd.toFixed(2)} list price · ${fmt.num(k.tokens.cache_hits)} cache hits`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Conversations over time" className="lg:col-span-2">
          <div className="h-64">
            <ResponsiveContainer>
              <AreaChart data={daily}>
                <defs>
                  <linearGradient id="conv" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={BRAND} stopOpacity={0.35} /><stop offset="100%" stopColor={BRAND} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                <XAxis dataKey="label" fontSize={11} tickLine={false} />
                <YAxis allowDecimals={false} fontSize={11} tickLine={false} width={30} />
                <Tooltip />
                <Area type="monotone" dataKey="conversations" stroke={BRAND} fill="url(#conv)" strokeWidth={2} />
                <Area type="monotone" dataKey="leads" stroke="#10B981" fillOpacity={0} strokeWidth={2} />
                <Area type="monotone" dataKey="handovers" stroke="#F59E0B" fillOpacity={0} strokeWidth={1.5} />
                <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Conversations by channel">
          <div className="h-64">
            {channels.length ? (
              <ResponsiveContainer>
                <PieChart>
                  <Pie data={channels} dataKey="value" nameKey="name" innerRadius={50} outerRadius={85} paddingAngle={2}>
                    {channels.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
                  </Pie>
                  <Tooltip /><Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
            ) : <p className="pt-20 text-center text-sm text-slate-400">No conversations yet</p>}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Lead pipeline">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={pipeline} layout="vertical" margin={{ left: 10 }}>
                <XAxis type="number" allowDecimals={false} fontSize={11} hide />
                <YAxis type="category" dataKey="stage" fontSize={11} width={70} tickLine={false} />
                <Tooltip /><Bar dataKey="count" fill={BRAND} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Top customer intents">
          <div className="space-y-2">
            {data.intents.slice(0, 7).map((i: any) => {
              const max = data.intents[0]?.count || 1;
              return (
                <div key={i.intent}>
                  <div className="flex justify-between text-xs"><span>{i.intent.replaceAll("_", " ")}</span><span className="text-slate-500">{i.count}</span></div>
                  <div className="mt-1 h-1.5 rounded bg-slate-100"><div className="h-1.5 rounded bg-brand" style={{ width: `${(100 * i.count) / max}%` }} /></div>
                </div>
              );
            })}
            {!data.intents.length && <p className="text-sm text-slate-400">No messages yet</p>}
          </div>
        </Card>
        <Card title="Token usage & quota">
          <div className="h-40">
            <ResponsiveContainer>
              <BarChart data={daily}>
                <XAxis dataKey="label" fontSize={10} tickLine={false} />
                <YAxis fontSize={10} width={40} tickLine={false} />
                <Tooltip /><Bar dataKey="tokens" fill="#8B5CF6" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 text-xs text-slate-500">
            <div className="flex justify-between"><span>This month</span>
              <span>{fmt.num(data.quota.used)} / {data.quota.monthly_quota ? fmt.num(data.quota.monthly_quota) : "∞"}</span></div>
            <div className="mt-1 h-2 rounded bg-slate-100">
              <div className={quotaPct > 85 ? "h-2 rounded bg-rose-500" : "h-2 rounded bg-emerald-500"} style={{ width: `${quotaPct}%` }} />
            </div>
            <div className="mt-2 flex items-center gap-1"><Activity className="h-3 w-3" />Avg reply time {fmt.ms(k.avg_response_ms)}</div>
          </div>
        </Card>
      </div>
    </div>
  );
}
