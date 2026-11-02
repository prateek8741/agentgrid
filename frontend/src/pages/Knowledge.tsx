import { FileText, Search, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { api, fmt } from "../api";
import { atLeast, useAuth } from "../auth";
import { Badge, Button, Card, Empty, Input, Modal, PageHeader, Spinner, Table, useApi, useToast } from "../components/ui";

export default function Knowledge() {
  const { me } = useAuth();
  const isAdmin = atLeast(me?.role, "tenant_admin");
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [drag, setDrag] = useState(false);
  const [preview, setPreview] = useState<any>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any>(null);
  const [searching, setSearching] = useState(false);
  const { data, loading, reload } = useApi(() => api("/knowledge/documents"), []);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    for (const file of Array.from(files)) {
      const form = new FormData();
      form.append("file", file);
      try {
        const doc = await api("/knowledge/documents", { form });
        toast(`${doc.title}: ${doc.chunk_count} chunks indexed${doc.truncated ? " (truncated)" : ""}`);
      } catch (e: any) {
        toast(`${file.name}: ${e.message}`, "error");
      }
    }
    setUploading(false);
    reload();
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    upload(e.dataTransfer.files);
  };
  const remove = async (id: string) => {
    if (!confirm("Delete this document from the knowledge base?")) return;
    await api(`/knowledge/documents/${id}`, { method: "DELETE" });
    toast("Document deleted");
    reload();
  };
  const search = async () => {
    if (query.trim().length < 2) return;
    setSearching(true);
    try {
      setResults(await api("/knowledge/search", { body: { query, top_k: 4 } }));
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Knowledge base" subtitle="Documents your AI agents answer from, with source citations"
        actions={data && <Badge color="brand">{data.total_documents} docs · {data.total_chunks} chunks · {data.vector_backend}</Badge>} />
      {isAdmin && (
        <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
          className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 text-center transition ${drag ? "border-brand bg-brand/5" : "border-slate-300 bg-white"}`}>
          <UploadCloud className="h-8 w-8 text-slate-400" />
          <p className="mt-2 text-sm font-medium">Drop PDF, DOCX, TXT or Markdown files here</p>
          <p className="text-xs text-slate-500">Up to 10 MB each. Text is chunked by section and embedded locally (bge-small).</p>
          <input ref={fileRef} type="file" multiple accept=".pdf,.docx,.txt,.md,.markdown" className="hidden" onChange={(e) => upload(e.target.files)} />
          <Button className="mt-3" onClick={() => fileRef.current?.click()} loading={uploading}>Choose files</Button>
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-5">
        <Card title="Documents" className="lg:col-span-3">
          {loading && !data ? <Spinner /> : data.documents.length === 0 ? (
            <Empty icon={<FileText className="h-8 w-8" />} title="No documents yet">Upload your FAQ, price list or policies.</Empty>
          ) : (
            <Table rows={data.documents} onRowClick={async (d: any) => setPreview(await api(`/knowledge/documents/${d.id}`))}
              columns={[
                { header: "Title", cell: (d: any) => <div><div className="font-medium">{d.title}</div><div className="text-xs text-slate-400">{d.filename}</div></div> },
                { header: "Chunks", cell: (d: any) => <>{d.chunk_count}{d.truncated && <Badge color="amber">truncated</Badge>}</> },
                { header: "Size", cell: (d: any) => `${(d.size_bytes / 1024).toFixed(1)} KB` },
                { header: "Added", cell: (d: any) => fmt.ago(d.created_at) },
                {
                  header: "", className: "text-right", cell: (d: any) => isAdmin && (
                    <button onClick={(e) => { e.stopPropagation(); remove(d.id); }} className="text-slate-400 hover:text-rose-600" aria-label="Delete">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  ),
                },
              ]} />
          )}
        </Card>
        <Card title="Test retrieval" className="lg:col-span-2">
          <div className="flex gap-2">
            <Input placeholder="Ask what a customer would ask…" value={query} onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()} />
            <Button onClick={search} loading={searching}><Search className="h-4 w-4" /></Button>
          </div>
          {results && (
            <div className="mt-4 space-y-3">
              <div className="flex items-center gap-2 text-xs">
                <Badge color={results.confident ? "green" : "amber"}>{results.confident ? "Confident" : "Below threshold → \"I don't know\""}</Badge>
                <span className="text-slate-500">best {results.confidence.toFixed(3)} / threshold {results.threshold}</span>
              </div>
              {results.results.map((r: any, i: number) => (
                <div key={i} className="rounded-lg border border-slate-200 p-3 text-xs">
                  <div className="mb-1 flex justify-between font-medium"><span>[{i + 1}] {r.title}</span><span className="text-slate-400">{r.similarity.toFixed(3)}</span></div>
                  <p className="line-clamp-4 whitespace-pre-wrap text-slate-600">{r.content}</p>
                </div>
              ))}
              {!results.results.length && <p className="text-sm text-slate-400">The knowledge base is empty.</p>}
            </div>
          )}
        </Card>
      </div>
      <Modal open={!!preview} onClose={() => setPreview(null)} title={preview?.title || ""} wide>
        <div className="space-y-2">
          {preview?.chunks.map((c: any) => (
            <div key={c.chunk_index} className="rounded-lg bg-slate-50 p-3 text-xs">
              <div className="mb-1 text-slate-400">Chunk {c.chunk_index + 1}{c.meta.section && ` · ${c.meta.section}`}{c.meta.page && ` · page ${c.meta.page}`}</div>
              <p className="whitespace-pre-wrap">{c.content}</p>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
}
