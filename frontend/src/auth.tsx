import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getTokens, setTokens } from "./api";

export type Role = "super_admin" | "tenant_admin" | "agent" | "viewer";
export type Branding = {
  brand_name?: string | null; logo_url?: string | null; primary_color: string; accent_color: string;
  widget_title: string; widget_subtitle: string; launcher_position: "left" | "right"; hide_powered_by: boolean;
};
export type Me = {
  user: { id: string; email: string; full_name: string; role: Role; tenant_id: string };
  tenant: { id: string; name: string; slug: string; kind: "platform" | "agency" | "business" };
  home_tenant_id: string;
  role: Role;
  branding: Branding;
  settings: Record<string, any>;
};

const RANK: Record<Role, number> = { viewer: 10, agent: 20, tenant_admin: 30, super_admin: 100 };
export const atLeast = (role: Role | undefined, min: Role) => !!role && RANK[role] >= RANK[min];

type Ctx = {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchTenant: (tenantId: string) => Promise<void>;
  reload: () => Promise<void>;
};
const AuthContext = createContext<Ctx>(null as unknown as Ctx);
export const useAuth = () => useContext(AuthContext);

function applyBrand(color?: string) {
  const hex = (color || "#4F46E5").replace("#", "");
  const full = hex.length === 3 ? hex.split("").map((c) => c + c).join("") : hex;
  const n = parseInt(full, 16);
  if (!Number.isNaN(n)) {
    document.documentElement.style.setProperty("--brand", `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`);
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!getTokens()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      const data = await api<Me>("/auth/me");
      setMe(data);
      applyBrand(data.branding?.primary_color);
      document.title = `${data.branding?.brand_name || data.tenant.name} · AgentGrid`;
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
    const onLogout = () => setMe(null);
    window.addEventListener("agentgrid:logout", onLogout);
    return () => window.removeEventListener("agentgrid:logout", onLogout);
  }, [reload]);

  const login = async (email: string, password: string) => {
    setTokens(await api("/auth/login", { body: { email, password } }));
    await reload();
  };
  const logout = async () => {
    const t = getTokens();
    if (t) await api("/auth/logout", { body: { refresh_token: t.refresh_token } }).catch(() => undefined);
    setTokens(null);
    setMe(null);
    applyBrand();
  };
  const switchTenant = async (tenantId: string) => {
    setTokens(await api("/auth/switch-tenant", { body: { tenant_id: tenantId } }));
    await reload();
  };
  return (
    <AuthContext.Provider value={{ me, loading, login, logout, switchTenant, reload }}>{children}</AuthContext.Provider>
  );
}
