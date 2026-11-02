// Thin fetch client: JSON, bearer auth, transparent refresh-token rotation, typed errors.
const STORE = "agentgrid.tokens";

export type Tokens = { access_token: string; refresh_token: string };
export class ApiError extends Error {
  constructor(public status: number, message: string, public details?: unknown) {
    super(message);
  }
}

export function getTokens(): Tokens | null {
  try {
    return JSON.parse(localStorage.getItem(STORE) || "null");
  } catch {
    return null;
  }
}
export function setTokens(t: Tokens | null) {
  if (t) localStorage.setItem(STORE, JSON.stringify({ access_token: t.access_token, refresh_token: t.refresh_token }));
  else localStorage.removeItem(STORE);
}

let refreshing: Promise<boolean> | null = null;
async function refresh(): Promise<boolean> {
  const t = getTokens();
  if (!t) return false;
  refreshing ??= fetch("/api/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: t.refresh_token }),
  })
    .then(async (r) => {
      if (!r.ok) return false;
      setTokens(await r.json());
      return true;
    })
    .catch(() => false)
    .finally(() => setTimeout(() => (refreshing = null), 0));
  return refreshing;
}

type Options = { method?: string; body?: unknown; form?: FormData; query?: Record<string, unknown> };

export async function api<T = any>(path: string, opts: Options = {}, retried = false): Promise<T> {
  const qs = opts.query
    ? "?" +
      new URLSearchParams(
        Object.entries(opts.query)
          .filter(([, v]) => v !== undefined && v !== null && v !== "")
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : "";
  const headers: Record<string, string> = {};
  const t = getTokens();
  if (t) headers.Authorization = `Bearer ${t.access_token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api/v1${path}${qs}`, {
    method: opts.method || (opts.body !== undefined || opts.form ? "POST" : "GET"),
    headers,
    body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
  });
  if (res.status === 401 && !retried && path !== "/auth/login" && (await refresh())) {
    return api<T>(path, opts, true);
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && path !== "/auth/login") {
      setTokens(null);
      window.dispatchEvent(new Event("agentgrid:logout"));
    }
    throw new ApiError(res.status, data?.error?.message || res.statusText, data?.error?.details);
  }
  return data as T;
}

export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/v1${path}`;
}

export const fmt = {
  num: (n?: number | null) => (n == null ? "—" : new Intl.NumberFormat().format(n)),
  pct: (n?: number | null) => (n == null ? "—" : `${(n * 100).toFixed(1)}%`),
  ms: (n?: number | null) => (n == null ? "—" : n >= 1000 ? `${(n / 1000).toFixed(2)} s` : `${n} ms`),
  date: (s?: string | null) => (s ? new Date(s).toLocaleString() : "—"),
  time: (s?: string | null) => (s ? new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""),
  ago: (s?: string | null) => {
    if (!s) return "—";
    const sec = Math.max(1, (Date.now() - new Date(s).getTime()) / 1000);
    if (sec < 60) return `${Math.round(sec)}s ago`;
    if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
    return `${Math.round(sec / 86400)}d ago`;
  },
};
