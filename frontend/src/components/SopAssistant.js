import { useEffect, useRef, useState } from "react";
import { Bot, FileText, FileUp, Send, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { API, api, errorMessage } from "../lib/api";
import { Panel } from "./StatCard";
import { useAuth } from "../context/AuthContext";
import { fmtDateTime } from "../lib/format";

function Documents({ docs, isAdmin, onChanged }) {
  const fileRef = useRef();
  const [busy, setBusy] = useState(false);

  const upload = async (file) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".docx")) { toast.error("Hanya berkas Word (.docx)"); return; }
    setBusy(true);
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await fetch(`${API}/sop/documents`, { method: "POST", body: fd, credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Gagal mengunggah");
      toast.success(`${data.filename} ditambahkan`);
      onChanged();
    } catch (e) { toast.error(e.message); } finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };

  const remove = async (doc) => {
    if (!window.confirm(`Hapus dokumen ${doc.filename}?`)) return;
    try { await api.delete(`/sop/documents/${doc.doc_id}`); toast.success("Dokumen dihapus"); onChanged(); }
    catch (e) { toast.error(errorMessage(e)); }
  };

  return (
    <div data-testid="sop-documents">
      <p className="text-xs text-slate-500">AI menjawab dari SOP Penggunaan bawaan{docs.length ? " dan dokumen Word berikut:" : "."}</p>
      {docs.length > 0 && (
        <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200">
          {docs.map((d) => (
            <li key={d.doc_id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm" data-testid={`sop-doc-${d.doc_id}`}>
              <span className="flex min-w-0 items-center gap-2 text-slate-300"><FileText size={14} className="shrink-0 text-amber-brand" /><span className="truncate">{d.filename}</span></span>
              <span className="flex shrink-0 items-center gap-2 text-xs text-slate-500">{fmtDateTime(d.uploaded_at)}{isAdmin && <button onClick={() => remove(d)} aria-label={`Hapus ${d.filename}`} className="rounded p-1 hover:bg-red-50 hover:text-red-700" data-testid={`sop-doc-delete-${d.doc_id}`}><Trash2 size={14} /></button>}</span>
            </li>
          ))}
        </ul>
      )}
      {isAdmin && (
        <div className="mt-3">
          <button onClick={() => fileRef.current?.click()} disabled={busy} className="btn-ghost !py-1.5 text-xs" data-testid="sop-doc-upload-button"><FileUp size={13} /> {busy ? "Mengunggah…" : "Unggah dokumen Word (.docx)"}</button>
          <input ref={fileRef} type="file" accept=".docx" className="hidden" onChange={(e) => upload(e.target.files[0])} data-testid="sop-doc-upload-input" />
          <p className="mt-1 text-[11px] text-slate-500">Maks. 5 MB per berkas, 20 dokumen. Jangan unggah dokumen berisi data pribadi: pertanyaan dan kutipan dikirim ke layanan AI pihak ketiga.</p>
        </div>
      )}
    </div>
  );
}

function Chat({ enabled }) {
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef();
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [messages, busy]);

  const send = async (e) => {
    e.preventDefault();
    const q = question.trim();
    if (q.length < 3 || busy) return;
    const history = messages.filter((m) => !m.error).slice(-6).map(({ role, content }) => ({ role, content }));
    setMessages((m) => [...m, { role: "user", content: q }]);
    setQuestion(""); setBusy(true);
    try {
      const { data } = await api.post("/sop/ask", { question: q, history });
      setMessages((m) => [...m, { role: "assistant", content: data.answer, sources: data.sources }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: errorMessage(err), error: true }]);
    } finally { setBusy(false); }
  };

  return (
    <div data-testid="sop-chat">
      <div className="max-h-[420px] min-h-[140px] space-y-3 overflow-y-auto rounded-lg border border-slate-200 p-3">
        {messages.length === 0 && <p className="text-sm text-slate-500">Contoh: "Bagaimana cara impor penyaluran dari BAST?" atau "Apa yang dilakukan bila stok tidak cukup?"</p>}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`} data-testid={`sop-chat-${m.role}`}>
            <div className={`max-w-[85%] whitespace-pre-wrap rounded-xl px-3 py-2 text-sm leading-relaxed ${m.role === "user" ? "bg-amber-brand/15 text-white" : m.error ? "border border-red-300 bg-red-50 text-red-700" : "bg-slate-100 text-slate-800"}`}>
              {m.content}
              {m.sources?.length > 0 && <div className="mt-1 text-[11px] text-slate-500">Rujukan: {m.sources.join(", ")}</div>}
            </div>
          </div>
        ))}
        {busy && <div className="text-sm text-slate-500" data-testid="sop-chat-busy">AI sedang membaca dokumen…</div>}
        <div ref={endRef} />
      </div>
      <form onSubmit={send} className="mt-3 flex gap-2">
        <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000} disabled={!enabled}
          placeholder={enabled ? "Tulis pertanyaan tentang SOP atau dokumen…" : "Tanya AI belum diaktifkan oleh admin"}
          className="field flex-1" aria-label="Pertanyaan untuk AI" data-testid="sop-chat-input" />
        <button type="submit" disabled={!enabled || busy || question.trim().length < 3} className="btn-primary" data-testid="sop-chat-send"><Send size={14} /> Tanya</button>
      </form>
      <p className="mt-1 text-[11px] text-slate-500">Jawaban AI bisa keliru. Untuk keputusan penting, periksa dokumen aslinya.</p>
    </div>
  );
}

function AiTest({ enabled, provider }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const run = async () => {
    setBusy(true); setResult(null);
    try { const { data } = await api.post("/sop/ai-test"); setResult(data); }
    catch (e) { setResult({ ok: false, text: { ok: false, detail: errorMessage(e) }, vision: {} }); }
    finally { setBusy(false); }
  };
  const line = (label, r) => r && (r.ok !== undefined) && (
    <li className={r.ok ? "text-emerald-700" : "text-red-700"}>{r.ok ? "✓" : "✗"} {label}: {r.ok ? `berhasil (model ${r.model})` : r.detail}</li>
  );
  return (
    <div className="mt-4 rounded-lg border border-slate-200 p-3 text-xs" data-testid="sop-ai-test">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-slate-500">Mesin AI: <b>{provider === "custom" ? "penyedia sendiri (AI_BASE_URL)" : provider === "openrouter" ? "OpenRouter" : "belum diatur"}</b></span>
        <button onClick={run} disabled={busy} className="btn-ghost !py-1 text-xs" data-testid="sop-ai-test-button">{busy ? "Menguji…" : "Tes koneksi AI"}</button>
      </div>
      {!enabled && <p className="mt-2 text-amber-700" data-testid="sop-ai-disabled">AI belum aktif. Di Coolify → Environment Variables isi <b>AI_BASE_URL</b>, <b>AI_API_KEY</b>, dan <b>AI_MODEL</b> (penyedia kompatibel OpenAI, mis. Kelontong AI), atau <b>OPENROUTER_API_KEY</b>, lalu Redeploy.</p>}
      {result && <ul className="mt-2 space-y-1" data-testid="sop-ai-test-result">{line("Teks (Tanya AI, Word/Excel/PDF)", result.text)}{line("Gambar (scan/foto)", result.vision)}</ul>}
    </div>
  );
}

export default function SopAssistant() {
  const { isAdmin } = useAuth();
  const [state, setState] = useState({ documents: [], ai_enabled: false, loaded: false });
  const load = () => api.get("/sop/documents").then(({ data }) => setState({ ...data, loaded: true })).catch(() => setState((s) => ({ ...s, loaded: true })));
  useEffect(() => { load(); }, []);

  return (
    <Panel title={<span className="inline-flex items-center gap-2"><Bot size={18} /> Tanya AI tentang SOP</span>} subtitle="Ajukan pertanyaan; AI menjawab berdasarkan SOP dan dokumen Word yang diunggah admin." testId="sop-assistant">
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <Chat enabled={state.ai_enabled} />
        <Documents docs={state.documents} isAdmin={isAdmin} onChanged={load} />
      </div>
      {state.loaded && isAdmin && <AiTest enabled={state.ai_enabled} provider={state.ai_provider} />}
    </Panel>
  );
}
