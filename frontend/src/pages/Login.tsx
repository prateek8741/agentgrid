import { MessageSquareText } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";
import { Button, Field, Input } from "../components/ui";

const DEMO = [
  { label: "Sunrise Dental (admin)", email: "admin@sunrisedental.in" },
  { label: "UrbanNest Realty (admin)", email: "admin@urbannest.in" },
  { label: "Super admin", email: "admin@agentgrid.dev" },
];

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex min-h-full items-center justify-center bg-gradient-to-br from-indigo-50 via-white to-emerald-50 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand text-white shadow-lg"><MessageSquareText /></div>
          <h1 className="text-2xl font-semibold">AgentGrid</h1>
          <p className="text-sm text-slate-500">AI customer engagement & automation</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <Field label="Email"><Input type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Password"><Input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
          <Button type="submit" loading={busy} className="w-full">Sign in</Button>
          <div className="border-t pt-3 text-xs text-slate-500">
            Demo accounts (password from the seed output):
            <div className="mt-2 flex flex-wrap gap-1.5">
              {DEMO.map((d) => (
                <button type="button" key={d.email} onClick={() => setEmail(d.email)}
                  className="rounded-md border border-slate-200 px-2 py-1 hover:bg-slate-50">{d.label}</button>
              ))}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
