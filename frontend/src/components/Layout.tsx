import clsx from "clsx";
import {
  BarChart3, Bot, Building2, CalendarDays, Inbox, LibraryBig, LogOut, MessageSquareText, Palette, Settings,
  Users, Workflow,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api";
import { atLeast, useAuth, type Role } from "../auth";
import { Select } from "./ui";

type NavItem = { to: string; label: string; icon: ReactNode; min: Role; when?: (kind: string, role: Role) => boolean };
const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: <BarChart3 className="h-4 w-4" />, min: "viewer" },
  { to: "/inbox", label: "Inbox", icon: <Inbox className="h-4 w-4" />, min: "agent" },
  { to: "/leads", label: "Leads", icon: <Workflow className="h-4 w-4" />, min: "viewer" },
  { to: "/appointments", label: "Appointments", icon: <CalendarDays className="h-4 w-4" />, min: "viewer" },
  { to: "/knowledge", label: "Knowledge base", icon: <LibraryBig className="h-4 w-4" />, min: "viewer" },
  { to: "/agents", label: "AI agents", icon: <Bot className="h-4 w-4" />, min: "viewer" },
  { to: "/branding", label: "Branding & widget", icon: <Palette className="h-4 w-4" />, min: "tenant_admin" },
  { to: "/users", label: "Team", icon: <Users className="h-4 w-4" />, min: "tenant_admin" },
  { to: "/settings", label: "Settings", icon: <Settings className="h-4 w-4" />, min: "tenant_admin" },
  {
    to: "/tenants", label: "Clients & tenants", icon: <Building2 className="h-4 w-4" />, min: "tenant_admin",
    when: (kind, role) => role === "super_admin" || kind === "agency",
  },
];

function TenantSwitcher() {
  const { me, switchTenant } = useAuth();
  const [tenants, setTenants] = useState<{ id: string; name: string; kind: string }[]>([]);
  const canSwitch = me && (me.role === "super_admin" || me.role === "tenant_admin");
  useEffect(() => {
    if (canSwitch) api("/tenants", { query: { limit: 200 } }).then((r) => setTenants(r.items)).catch(() => undefined);
  }, [canSwitch, me?.tenant.id]);
  if (!me || !canSwitch || tenants.length < 2) return null;
  return (
    <Select value={me.tenant.id} onChange={(e) => switchTenant(e.target.value)} className="!py-1.5 text-xs" aria-label="Switch tenant">
      {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}{t.kind === "agency" ? " (agency)" : t.kind === "platform" ? " (platform)" : ""}</option>)}
    </Select>
  );
}

export default function Layout({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  if (!me) return null;
  const brandName = me.branding?.brand_name || me.tenant.name;
  const items = NAV.filter((n) => atLeast(me.role, n.min) && (!n.when || n.when(me.tenant.kind, me.role)));
  const impersonating = me.tenant.id !== me.home_tenant_id;
  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="flex items-center gap-2.5 px-5 py-4">
          {me.branding?.logo_url ? (
            <img src={me.branding.logo_url} alt="" className="h-8 w-8 rounded-lg object-cover" />
          ) : (
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-white"><MessageSquareText className="h-4 w-4" /></div>
          )}
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{brandName}</div>
            <div className="text-[11px] text-slate-400">AgentGrid console</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3 py-2">
          {items.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => clsx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition",
                isActive ? "bg-brand/10 font-medium text-brand" : "text-slate-600 hover:bg-slate-100")}>
              {n.icon}{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-100 p-4 text-xs">
          <div className="truncate font-medium text-slate-700">{me.user.full_name || me.user.email}</div>
          <div className="truncate text-slate-400">{me.user.email}</div>
          <div className="mt-1 text-slate-400">{me.role.replace("_", " ")}</div>
          <button onClick={logout} className="mt-3 inline-flex items-center gap-1.5 text-slate-500 hover:text-slate-800">
            <LogOut className="h-3.5 w-3.5" /> Sign out
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-6 py-2.5">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium">{me.tenant.name}</span>
            {impersonating && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-800">acting as client</span>}
          </div>
          <div className="flex items-center gap-2">
            <TenantSwitcher />
            <nav className="flex gap-1 md:hidden">
              {items.slice(0, 5).map((n) => <NavLink key={n.to} to={n.to} className="rounded p-1.5 text-slate-500" aria-label={n.label}>{n.icon}</NavLink>)}
            </nav>
          </div>
        </header>
        <main className="scrollbar-thin flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
