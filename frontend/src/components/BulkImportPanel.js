import { Fragment, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, CheckCircle2, FileSpreadsheet, FileUp, X } from "lucide-react";
import { toast } from "sonner";
import { API, downloadFile } from "../lib/api";
import { Panel } from "./StatCard";
import { fmtNum } from "../lib/format";

const dmy = (iso) => (iso ? iso.split("-").reverse().join("/") : "—");

const KINDS = {
  in: {
    title: "Impor Barang Masuk",
    subtitle: "Satu berkas = satu BAST penerimaan",
    template: "Unduh Template Barang Masuk",
    qtyLabel: "Masuk",
    header: (h) => [["Sumber", h.source], ["Tanggal", dmy(h.date)], ["No. BAST", h.ref || "—"]],
  },
  out: {
    title: "Impor Penyaluran",
    subtitle: "Satu berkas = satu BAST penyaluran",
    template: "Unduh Template Penyaluran",
    qtyLabel: "Disalurkan",
    header: (h) => [["Tujuan", h.destination], ["Kejadian", h.incident_type], ["Tanggal", `${dmy(h.date)} ${h.time || ""}`],
      ["KK", h.recipient_kk ?? "—"], ["Jiwa", h.recipient_jiwa ?? "—"], ["No. BAST", h.ref || "—"]],
  },
};

async function postFile(path, file, extra = {}) {
  const fd = new FormData();
  fd.append("file", file);
  Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  const res = await fetch(`${API}${path}`, { method: "POST", body: fd, credentials: "include" });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail : "Gagal memproses berkas");
    err.status = res.status;
    throw err;
  }
  return data;
}

export default function BulkImportPanel({ kind }) {
  const cfg = KINDS[kind];
  const fileRef = useRef();
  const pick = useRef(0);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const testId = `bulk-${kind}`;

  const reset = () => { setFile(null); setPreview(null); setError(""); if (fileRef.current) fileRef.current.value = ""; };

  const downloadTemplate = async () => {
    setBusy("tpl");
    try { const n = await downloadFile(`/excel/template/${kind}`, "template.xlsx"); toast.success(`Berkas ${n} diunduh`); }
    catch (e) { toast.error(e.message); } finally { setBusy(""); }
  };

  const choose = async (f) => {
    if (!f || busy) return;
    if (!f.name.toLowerCase().endsWith(".xlsx")) { toast.error("Berkas harus .xlsx"); return; }
    const id = ++pick.current;
    setBusy("preview"); setSaved(null); setPreview(null); setError(""); setFile(f);
    try { const data = await postFile(`/excel/import/${kind}/preview`, f); if (id === pick.current) setPreview(data); }
    catch (e) { if (id === pick.current) { setError(e.message); setFile(null); } }
    finally { if (id === pick.current) { setBusy(""); if (fileRef.current) fileRef.current.value = ""; } }
  };

  const save = async () => {
    if (!preview?.valid || !file) return;
    setBusy("save"); setError("");
    try {
      const data = await postFile(`/excel/import/${kind}/commit`, file, { snapshot: JSON.stringify(preview.snapshot) });
      setSaved(data); setPreview(null); setFile(null);
      toast.success(`${data.count} transaksi tersimpan`);
    } catch (e) {
      setError(e.status === 409 ? "Stok berubah sejak pratinjau. Unggah ulang berkas untuk melihat angka terbaru." : e.message);
      if (e.status === 409) setPreview(null);
    } finally { setBusy(""); }
  };

  return (
    <Panel title={cfg.title} subtitle={cfg.subtitle} testId={testId}>
      <div id={`impor-${kind}`} className="flex flex-wrap items-center gap-2">
        <button onClick={downloadTemplate} disabled={busy === "tpl"} className="btn-ghost !py-1.5 text-xs" data-testid={`${testId}-template-button`}><FileSpreadsheet size={13} /> {cfg.template}</button>
        <span className="text-xs text-slate-500">Isi kotak kuning, lalu unggah. Data baru tersimpan setelah Anda menekan Simpan.</span>
      </div>

      {!preview && (
        <div onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files[0]); }}
          onClick={() => fileRef.current?.click()} data-testid={`${testId}-dropzone`}
          className={`mt-4 cursor-pointer rounded-xl border-2 border-dashed p-6 text-center transition-colors ${dragging ? "border-amber-brand bg-amber-brand/10" : "border-ink-700 hover:border-amber-brand/60"}`}>
          <FileUp size={24} className="mx-auto text-amber-brand" />
          <div className="mt-2 text-sm font-semibold text-white">{busy === "preview" ? "Memeriksa berkas…" : "Tarik berkas .xlsx ke sini atau klik untuk memilih"}</div>
          <input ref={fileRef} type="file" accept=".xlsx" className="hidden" onChange={(e) => choose(e.target.files[0])} data-testid={`${testId}-input`} />
        </div>
      )}

      {error && <div role="alert" className="mt-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300" data-testid={`${testId}-error`}><AlertCircle size={15} className="mt-0.5 shrink-0" />{error}</div>}

      {preview && (
        <div className="mt-4 space-y-3" data-testid={`${testId}-preview`}>
          <div className="flex items-center justify-between gap-2"><div className="text-sm font-semibold text-white">Pratinjau · <span className="text-slate-400">{preview.filename}</span></div><button onClick={reset} className="btn-ghost !py-1 text-xs" data-testid={`${testId}-cancel`}><X size={13} /> Ganti berkas</button></div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg border border-ink-700 bg-ink-900/60 p-3 text-xs sm:grid-cols-3">
            {cfg.header(preview.header).map(([k, v]) => <div key={k} className="min-w-0"><dt className="text-slate-500">{k}</dt><dd className="truncate font-semibold text-slate-200">{v || "—"}</dd></div>)}
          </dl>
          {preview.header_errors.length > 0 && <ul className="space-y-1" data-testid={`${testId}-header-errors`}>{preview.header_errors.map((e) => <li key={e} className="flex items-start gap-2 text-xs text-red-300"><AlertCircle size={13} className="mt-0.5 shrink-0" />{e}</li>)}</ul>}
          {preview.rows.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-ink-700">
              <table className="w-full text-xs">
                <thead className="bg-ink-900/80 text-left text-slate-400"><tr><th className="px-2 py-2">No</th><th className="px-2 py-2">Barang</th><th className="px-2 py-2 text-right">Stok</th><th className="px-2 py-2 text-right">{cfg.qtyLabel}</th><th className="px-2 py-2 text-right">Sesudah</th><th className="px-2 py-2">Status</th></tr></thead>
                <tbody className="divide-y divide-ink-700/60">
                  {preview.rows.map((r) => (
                    <Fragment key={r.row}>
                    <tr className={r.error ? "bg-red-500/10" : ""} data-testid={`${testId}-row-${r.row}`}>
                      <td className="px-2 py-1.5 num text-slate-400">{r.row}</td>
                      <td className="px-2 py-1.5 text-slate-200">{r.item_name || <span className="text-slate-500">{r.item_id || "—"}</span>}{r.unit && <span className="block text-[10px] text-slate-500">{r.unit}</span>}</td>
                      <td className="px-2 py-1.5 num text-right text-slate-300">{r.current_stock == null ? "—" : fmtNum(r.current_stock)}</td>
                      <td className="px-2 py-1.5 num text-right text-white">{r.quantity == null ? "—" : fmtNum(r.quantity)}</td>
                      <td className="px-2 py-1.5 num text-right text-slate-300">{r.after == null ? "—" : fmtNum(r.after)}</td>
                      <td className="px-2 py-1.5">{r.error ? <AlertCircle size={14} className="text-red-400" /> : <CheckCircle2 size={14} className="text-emerald-400" />}</td>
                    </tr>
                    {r.error && <tr className="bg-red-500/10" data-testid={`${testId}-row-${r.row}-error`}><td /><td colSpan={5} className="px-2 pb-2 text-red-300">✗ {r.error}</td></tr>}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-xs text-slate-400" data-testid={`${testId}-summary`}>{preview.rows.length - preview.row_errors} baris benar · <span className={preview.row_errors ? "text-red-300" : ""}>{preview.row_errors} baris salah</span>{preview.header_errors.length > 0 && " · data BAST di bagian atas belum lengkap"}</span>
            <button onClick={save} disabled={!preview.valid || busy === "save"} className="btn-primary !py-2 text-xs" data-testid={`${testId}-save`}>{busy === "save" ? "Menyimpan…" : `Simpan ${preview.rows.length} transaksi`}</button>
          </div>
          {!preview.valid && <p className="text-xs text-slate-500">Tombol Simpan aktif setelah semua baris benar. Perbaiki berkas Excel lalu unggah ulang.</p>}
        </div>
      )}

      {saved && (
        <div className="mt-4 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm" data-testid={`${testId}-saved`}>
          <div className="flex items-center gap-2 font-semibold text-emerald-300"><CheckCircle2 size={15} /> {saved.count} transaksi tersimpan</div>
          <div className="mt-1 text-xs text-slate-300">Nomor batch: <b className="num text-white">{saved.batch_id}</b>{saved.batch_ref && <> · No. BAST: <b className="text-white">{saved.batch_ref}</b></>}</div>
          <Link to={`/app/riwayat?batch_id=${saved.batch_id}`} className="mt-2 inline-block text-xs font-semibold text-amber-brand hover:underline" data-testid={`${testId}-history-link`}>Lihat di Riwayat Transaksi →</Link>
        </div>
      )}
    </Panel>
  );
}
