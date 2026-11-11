import { useEffect, useState } from "react";
import { BookOpen, Check, Download, FileText, Minus } from "lucide-react";
import { toast } from "sonner";
import { api, downloadFile, errorMessage } from "../lib/api";
import { PageHeader, Panel, Spinner } from "../components/StatCard";
import { ROLE_LABEL } from "../lib/format";
import SopAssistant from "../components/SopAssistant";

function List({ items, ordered }) {
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className={`mt-2 space-y-1.5 pl-5 text-sm leading-relaxed text-slate-300 ${ordered ? "list-decimal" : "list-disc"}`}>
      {items.map((t) => <li key={t}>{t}</li>)}
    </Tag>
  );
}

function AccessTable({ rows }) {
  const cell = (v) => (v ? <Check size={15} className="mx-auto text-emerald-500" aria-label="Ya" /> : <Minus size={15} className="mx-auto text-slate-400" aria-label="Tidak" />);
  return (
    <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200">
      <table className="w-full text-sm" data-testid="sop-access-table">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500">
          <tr><th className="px-3 py-2">Kemampuan</th><th className="px-3 py-2 text-center">Admin</th><th className="px-3 py-2 text-center">Petugas</th><th className="px-3 py-2 text-center">Opname</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {rows.map((r) => <tr key={r.label}><td className="px-3 py-2 text-slate-300">{r.label}</td><td className="px-3 py-2">{cell(r.admin)}</td><td className="px-3 py-2">{cell(r.petugas)}</td><td className="px-3 py-2">{cell(r.opname)}</td></tr>)}
        </tbody>
      </table>
    </div>
  );
}

export default function SopPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    api.get("/sop").then(({ data: d }) => setData(d)).catch((e) => setError(errorMessage(e)));
  }, []);

  const download = async (fmt) => {
    setBusy(fmt);
    try { const n = await downloadFile(`/sop/${fmt}`, `Modul_SOP_SIPOSTLOG.${fmt}`); toast.success(`Berkas ${n} diunduh`); }
    catch (e) { toast.error(e.message); } finally { setBusy(""); }
  };

  return (
    <div data-testid="sop-page">
      <PageHeader eyebrow="Panduan" title="SOP Penggunaan" description="Prosedur baku penggunaan SIPOSTLOG. Bagian yang tampil disesuaikan dengan peran Anda; modul Word dan PDF berisi SOP lengkap untuk semua peran dan siap disahkan."
        actions={<>
          <button onClick={() => download("docx")} disabled={!!busy} className="btn-ghost" data-testid="sop-download-docx"><FileText size={14} /> {busy === "docx" ? "Menyiapkan…" : "Unduh Word"}</button>
          <button onClick={() => download("pdf")} disabled={!!busy} className="btn-primary" data-testid="sop-download-button"><Download size={14} /> {busy === "pdf" ? "Menyiapkan…" : "Unduh PDF"}</button>
        </>} />
      <div className="mb-8"><SopAssistant /></div>
      {error && <div role="alert" className="mb-4 text-sm text-red-700">{error}</div>}
      {!data && !error && <Spinner />}
      {data && (
        <div className="grid gap-8 lg:grid-cols-[220px_1fr]">
          <nav aria-label="Daftar isi SOP" className="lg:sticky lg:top-4 self-start">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500"><BookOpen size={14} /> Daftar isi</div>
            <ol className="space-y-1 text-sm">
              {data.sections.map((s, i) => <li key={s.id}><a href={`#sop-${s.id}`} className="block rounded px-2 py-1 text-slate-300 hover:bg-slate-100 hover:text-white">{i + 1}. {s.title}</a></li>)}
            </ol>
            <p className="mt-3 px-2 text-xs text-slate-500" data-testid="sop-role-note">Ditampilkan untuk peran <b>{ROLE_LABEL[data.role] || data.role}</b> ({data.sections.length} dari {data.total_sections} bagian).</p>
          </nav>
          <div className="min-w-0 space-y-2">
            <div className="rounded-lg border border-slate-200 p-3 text-xs text-slate-500" data-testid="sop-meta">Versi {data.meta.version} · Berlaku {data.meta.effective} · Disahkan oleh {data.meta.approver_title}</div>
            {data.sections.map((s, i) => (
              <Panel key={s.id} title={`${i + 1}. ${s.title}`} testId={`sop-section-${s.id}`}>
                <div id={`sop-${s.id}`} className="scroll-mt-4">
                  {(s.paragraphs || []).map((p) => <p key={p} className="mb-2 text-sm leading-relaxed text-slate-300">{p}</p>)}
                  {s.access_table && <AccessTable rows={data.access} />}
                  {s.steps && <><h4 className="mt-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Langkah-langkah</h4><List items={s.steps} ordered /></>}
                  {s.admin_steps && data.role === "admin" && <><h4 className="mt-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Khusus Admin</h4><List items={s.admin_steps} ordered /></>}
                  {s.rules && <><h4 className="mt-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Ketentuan</h4><List items={s.rules} /></>}
                </div>
              </Panel>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
