import { Copy, ExternalLink, MessageSquareText, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth, type Branding } from "../auth";
import { Button, Card, Field, Input, PageHeader, Select, Spinner, useApi, useToast } from "../components/ui";

export default function BrandingPage() {
  const { me, reload } = useAuth();
  const toast = useToast();
  const { data } = useApi(() => api<Branding>("/tenant/branding"), []);
  const keys = useApi(() => api<any[]>("/api-keys"), []);
  const [form, setForm] = useState<Branding | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (data) setForm(data); }, [data]);
  if (!form) return <Spinner />;
  const set = (k: keyof Branding, v: any) => setForm({ ...form, [k]: v });
  const publicKey = keys.data?.find((k) => k.kind === "public" && !k.revoked_at)?.public_key;
  const snippet = `<script src="${location.origin}/widget.js" data-key="${publicKey || "pk_..."}" async></script>`;

  const save = async () => {
    setBusy(true);
    try {
      await api("/tenant/branding", { method: "PUT", body: { ...form, logo_url: form.logo_url || null, brand_name: form.brand_name || null } });
      toast("Branding saved: console and widget updated");
      await reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Branding & widget" subtitle="White-label the console and the chat widget your customers see"
        actions={<Button onClick={save} loading={busy}><Save className="h-4 w-4" />Save</Button>} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Brand">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Brand name (console)"><Input value={form.brand_name || ""} onChange={(e) => set("brand_name", e.target.value)} placeholder={me?.tenant.name} /></Field>
            <Field label="Logo URL" hint="https://… image, square works best"><Input value={form.logo_url || ""} onChange={(e) => set("logo_url", e.target.value)} /></Field>
            <Field label="Primary colour">
              <div className="flex gap-2"><input type="color" value={form.primary_color} onChange={(e) => set("primary_color", e.target.value)} className="h-9 w-12 rounded border" />
                <Input value={form.primary_color} onChange={(e) => set("primary_color", e.target.value)} /></div>
            </Field>
            <Field label="Accent colour">
              <div className="flex gap-2"><input type="color" value={form.accent_color} onChange={(e) => set("accent_color", e.target.value)} className="h-9 w-12 rounded border" />
                <Input value={form.accent_color} onChange={(e) => set("accent_color", e.target.value)} /></div>
            </Field>
            <Field label="Widget title"><Input value={form.widget_title} onChange={(e) => set("widget_title", e.target.value)} /></Field>
            <Field label="Widget subtitle"><Input value={form.widget_subtitle} onChange={(e) => set("widget_subtitle", e.target.value)} /></Field>
            <Field label="Launcher position">
              <Select value={form.launcher_position} onChange={(e) => set("launcher_position", e.target.value)}>
                <option value="right">Bottom right</option><option value="left">Bottom left</option>
              </Select>
            </Field>
            <label className="mt-6 flex items-center gap-2 text-sm"><input type="checkbox" checked={form.hide_powered_by} onChange={(e) => set("hide_powered_by", e.target.checked)} />Hide "Powered by AgentGrid"</label>
          </div>
        </Card>
        <Card title="Live preview">
          <div className="relative h-[420px] overflow-hidden rounded-lg bg-gradient-to-br from-slate-100 to-slate-200">
            <div className={`absolute bottom-20 w-72 overflow-hidden rounded-2xl bg-white shadow-xl ${form.launcher_position === "left" ? "left-4" : "right-4"}`}>
              <div className="flex items-center gap-2 px-4 py-3 text-white" style={{ background: form.primary_color }}>
                {form.logo_url ? <img src={form.logo_url} className="h-7 w-7 rounded bg-white object-cover" alt="" /> : <MessageSquareText className="h-5 w-5" />}
                <div><div className="text-sm font-semibold">{form.widget_title}</div><div className="text-[11px] opacity-80">{form.widget_subtitle}</div></div>
              </div>
              <div className="space-y-2 bg-slate-50 p-3 text-xs">
                <div className="max-w-[80%] rounded-xl rounded-bl-sm border bg-white px-3 py-2">Hi! How can I help you today?</div>
                <div className="ml-auto max-w-[80%] rounded-xl rounded-br-sm px-3 py-2 text-white" style={{ background: form.primary_color }}>Do you have slots tomorrow?</div>
                <div className="flex gap-1">
                  {["Book an appointment", "Talk to a human"].map((q) => (
                    <span key={q} className="rounded-full border px-2 py-0.5" style={{ borderColor: form.primary_color, color: form.primary_color }}>{q}</span>
                  ))}
                </div>
              </div>
              {!form.hide_powered_by && <div className="py-1.5 text-center text-[10px] text-slate-400">Powered by AgentGrid</div>}
            </div>
            <div className={`absolute bottom-4 flex h-12 w-12 items-center justify-center rounded-full text-white shadow-lg ${form.launcher_position === "left" ? "left-4" : "right-4"}`}
              style={{ background: form.primary_color }}><MessageSquareText className="h-5 w-5" /></div>
          </div>
        </Card>
      </div>
      <Card title="Embed on your website">
        <p className="mb-2 text-sm text-slate-600">Paste this before <code>&lt;/body&gt;</code>. The key is public by design; restrict the allowed websites under Settings.</p>
        <div className="flex gap-2">
          <code className="flex-1 overflow-x-auto rounded-lg bg-slate-900 px-3 py-2.5 text-xs text-slate-100">{snippet}</code>
          <Button variant="secondary" onClick={() => { navigator.clipboard.writeText(snippet); toast("Copied"); }}><Copy className="h-4 w-4" /></Button>
          <a href={`/demo/${me?.tenant.slug}`} target="_blank" rel="noreferrer"><Button variant="secondary"><ExternalLink className="h-4 w-4" />Demo site</Button></a>
        </div>
      </Card>
    </div>
  );
}
