import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, AlertTriangle, CheckCircle2, FileUp, Plus, RefreshCw, ScanText, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { API, api, errorMessage } from "../lib/api";
import { PageHeader, Panel } from "../components/StatCard";
import { useItems } from "../components/FormBits";
import { fmtNum } from "../lib/format";

const KINDS = {
  opname: { label: "Stock Opname", qty: "Stok fisik", hint: "Berita acara / lembar hitung stock opname. Stok sistem disamakan dengan stok fisik di dokumen; setiap selisih dicatat sebagai transaksi Koreksi." },
  out: { label: "Penyaluran", qty: "Disalurkan", hint: "BAST, surat jalan, atau rekap penyaluran. Satu dokumen = satu penyaluran ke satu lokasi." },
};
const ACCEPT = ".docx,.xlsx,.pdf,.jpg,.jpeg,.png,.webp";
const emptyHeader = { destination: "", incident_type: "", date: "", time: "", recipient_kk: "", recipient_jiwa: "", ref: "" };
let rowKey = 0;
const newRow = (r = {}) => ({ key: ++rowKey, item_id: r.item_id || "", quantity: r.quantity ?? "", note: r.note || "", source_name: r.source_name || "", source_unit: r.source_unit || "" });

function HeaderForm({ kind, header, set, incidents }) {
  const input = (key, label, props = {}) => (
    <label className="block text-xs text-slate-500">{label}
      <input className="field mt-1" value={header[key] ?? ""} onChange={(e) => set(key, e.target.value)} data-testid={`ai-header-${key}`} {...props} />
    </label>
  );
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="ai-header">
      {kind === "out" && <div className="sm:col-span-2">{input("destination", "Lokasi tujuan *")}</div>}
      {kind === "out" && (
        <label className="block text-xs text-slate-500">Jenis kejadian *
          <select className="field mt-1" value={header.incident_type || ""} onChange={(e) => set("incident_type", e.target.value)} data-testid="ai-header-incident_type">
            <option value="">Pilih…</option>{incidents.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>
      )}
      {input("date", kind === "out" ? "Tanggal *" : "Tanggal opname", { type: "date" })}
      {kind === "out" && input("time", "Jam (WITA) *", { type: "time" })}
      {kind === "out" && input("recipient_kk", "Jumlah KK", { type: "number", min: 0 })}
      {kind === "out" && input("recipient_jiwa", "Jumlah jiwa", { type: "number", min: 0 })}
      {input("ref", "No. BAST / dokumen")}
    </div>
  );
}

export default function AiImportPage() {
  const [items] = useItems();
  const fileRef = useRef();
  const [status, setStatus] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [kind, setKind] = useState("opname");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [draft, setDraft] = useState(null); // { filename, model, read_as, header, rows }
  const [check, setCheck] = useState(null); // last server validation
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(null);

  useEffect(() => {
    api.get("/ai-import/status").then(({ data }) => { setStatus(data); if (!data.kinds.includes("opname") && data.kinds[0]) setKind(data.kinds[0]); }).catch((e) => setError(errorMessage(e)));
    api.get("/meta/incident-types").then(({ data }) => setIncidents(data)).catch(() => {});
  }, []);

  const reset = () => { setDraft(null); setCheck(null); setDirty(false); setError(""); if (fileRef.current) fileRef.current.value = ""; };
  const switchKind = (k) => { if (k !== kind) { reset(); setSaved(null); setKind(k); } };

  const payload = (d = draft) => ({
    header: Object.fromEntries(Object.entries(d.header).filter(([, v]) => v !== "" && v != null)),
    rows: d.rows.map(({ item_id, quantity, note, source_name, source_unit }) => ({ item_id, quantity: quantity === "" ? null : quantity, note, source_name, source_unit })),
  });

  const upload = async (file) => {
    if (!file) return;
    setBusy("extract"); setError(""); setSaved(null); setDraft(null); setCheck(null);
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await fetch(`${API}/ai-import/${kind}/extract`, { method: "POST", body: fd, credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Gagal membaca dokumen");
      setDraft({ filename: data.filename, model: data.model, read_as: data.read_as, truncated: data.truncated,
        header: { ...emptyHeader, ...data.header }, rows: data.rows.map(newRow) });
      setCheck(data); setDirty(false);
    } catch (e) { setError(e.message); } finally { setBusy(""); if (fileRef.current) fileRef.current.value = ""; }
  };

  const edit = (fn) => { setDraft((d) => fn(d)); setDirty(true); };
  const setHeader = (key, value) => edit((d) => ({ ...d, header: { ...d.header, [key]: value } }));
  const setRow = (key, patch) => edit((d) => ({ ...d, rows: d.rows.map((r) => (r.key === key ? { ...r, ...patch } : r)) }));

  const validate = async () => {
    setBusy("check"); setError("");
    try { const { data } = await api.post(`/ai-import/${kind}/validate`, payload()); setCheck(data); setDirty(false); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(""); }
  };

  const save = async () => {
    setBusy("save"); setError("");
    try {
      const { data } = await api.post(`/ai-import/${kind}/commit`, { ...payload(), snapshot: check.snapshot, filename: draft.filename });
      setSaved(data); reset(); window.dispatchEvent(new Event("sipostlog:stock-changed"));
      toast.success(`${data.count} transaksi tersimpan`);
    } catch (e) { setError(errorMessage(e)); if (e?.response?.status === 409) setDirty(true); } finally { setBusy(""); }
  };

  const cfg = KINDS[kind];
  const kinds = status?.kinds || [];
  const canSave = draft && check?.valid && !dirty && busy === "";

  return (
    <div data-testid="ai-import-page">
      <PageHeader eyebrow="Transaksi" title="Input dari Dokumen (AI)" description="Unggah dokumen Word, Excel, PDF, hasil scan, atau foto. AI membaca isinya dan mengisi daftar barang; Anda memeriksa, memperbaiki bila perlu, lalu menyimpan." />

      {status && !status.ai_enabled && <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800" data-testid="ai-disabled"><AlertTriangle size={15} className="mt-0.5 shrink-0" />Pembaca AI belum aktif. Admin perlu mengisi kunci AI di pengaturan server (lihat tombol Tes koneksi AI di halaman SOP Penggunaan), lalu Redeploy.</div>}

      <div className="mb-4 flex flex-wrap gap-2" role="tablist">
        {Object.entries(KINDS).filter(([k]) => kinds.includes(k)).map(([k, v]) => (
          <button key={k} role="tab" aria-selected={kind === k} onClick={() => switchKind(k)} className={kind === k ? "btn-primary" : "btn-ghost"} data-testid={`ai-tab-${k}`}>{v.label}</button>
        ))}
      </div>

      <Panel title={`Unggah dokumen ${cfg.label}`} subtitle={cfg.hint} testId="ai-upload-panel">
        {!draft && (
          <div onClick={() => status?.ai_enabled && fileRef.current?.click()} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (status?.ai_enabled) upload(e.dataTransfer.files[0]); }}
            className={`rounded-xl border-2 border-dashed border-slate-300 p-8 text-center ${status?.ai_enabled ? "cursor-pointer hover:border-amber-brand/60" : "opacity-60"}`} data-testid="ai-dropzone">
            {busy === "extract" ? <ScanText size={28} className="mx-auto animate-pulse text-amber-brand" /> : <FileUp size={28} className="mx-auto text-amber-brand" />}
            <div className="mt-3 text-sm font-semibold text-white">{busy === "extract" ? "AI sedang membaca dokumen… (bisa sampai 1 menit)" : "Tarik berkas ke sini atau klik untuk memilih"}</div>
            <div className="mt-1 text-xs text-slate-500">Word (.docx), Excel (.xlsx), PDF, atau foto/scan (.jpg, .png) · maks. 8 MB · PDF scan dibaca sampai 5 halaman</div>
            <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" onChange={(e) => upload(e.target.files[0])} data-testid="ai-file-input" />
          </div>
        )}
        <p className="mt-2 text-[11px] text-slate-500">Isi dokumen dikirim ke layanan AI pihak ketiga untuk dibaca. Jangan unggah dokumen yang memuat data pribadi penerima bantuan (NIK, nama per orang).</p>

        {error && <div role="alert" className="mt-4 flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700" data-testid="ai-error"><AlertCircle size={15} className="mt-0.5 shrink-0" />{error}</div>}

        {saved && (
          <div className="mt-4 rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm" data-testid="ai-saved">
            <div className="flex items-center gap-2 font-semibold text-emerald-700"><CheckCircle2 size={15} /> {saved.count} transaksi tersimpan{saved.unchanged > 0 && ` · ${saved.unchanged} barang tidak berubah`}</div>
            <div className="mt-1 text-xs text-slate-600">Nomor batch: <b className="num">{saved.batch_id}</b></div>
            {saved.count > 0 && <Link to={`/app/riwayat?batch_id=${saved.batch_id}`} className="mt-2 inline-block text-xs font-semibold text-amber-brand hover:underline" data-testid="ai-history-link">Lihat di Riwayat Transaksi →</Link>}
          </div>
        )}

        {draft && (
          <div className="mt-2 space-y-4" data-testid="ai-draft">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-semibold text-white">Hasil baca · <span className="text-slate-400">{draft.filename}</span> <span className="text-xs font-normal text-slate-500">(dibaca sebagai {draft.read_as})</span></div>
              <button onClick={reset} className="btn-ghost !py-1 text-xs" data-testid="ai-cancel"><X size={13} /> Batal</button>
            </div>
            <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800"><AlertTriangle size={14} className="mt-0.5 shrink-0" />AI bisa salah membaca, terutama angka pada scan atau foto. Cocokkan setiap baris dengan dokumen asli sebelum menyimpan.{draft.truncated && " Dokumen sangat panjang: hanya bagian awal yang dibaca."}</div>

            <HeaderForm kind={kind} header={draft.header} set={setHeader} incidents={incidents} />
            {check?.header_errors?.length > 0 && !dirty && <ul className="space-y-1" data-testid="ai-header-errors">{check.header_errors.map((e) => <li key={e} className="flex items-start gap-2 text-xs text-red-700"><AlertCircle size={13} className="mt-0.5 shrink-0" />{e}</li>)}</ul>}

            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full min-w-[720px] text-sm">
                <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500"><tr><th className="px-2 py-2">No</th><th className="px-2 py-2">Tertulis di dokumen</th><th className="px-2 py-2">Barang di sistem</th><th className="px-2 py-2 text-right">Stok</th><th className="px-2 py-2 text-right">{cfg.qty}</th><th className="px-2 py-2 text-right">Sesudah</th><th className="px-2 py-2">Catatan</th><th /></tr></thead>
                <tbody className="divide-y divide-slate-200">
                  {draft.rows.map((r, i) => {
                    const c = !dirty ? check?.rows?.[i] : null;
                    return (
                      <tr key={r.key} className={c?.error ? "bg-red-50" : ""} data-testid={`ai-row-${i + 1}`}>
                        <td className="px-2 py-1.5 align-top num text-slate-500">{i + 1}</td>
                        <td className="px-2 py-1.5 align-top text-slate-600">{r.source_name || "—"}{r.source_unit && <span className="block text-[11px] text-slate-500">{r.source_unit}</span>}</td>
                        <td className="px-2 py-1.5 align-top">
                          <select className="field !py-1 text-sm" value={r.item_id} onChange={(e) => setRow(r.key, { item_id: e.target.value })} aria-label={`Barang baris ${i + 1}`} data-testid={`ai-row-${i + 1}-item`}>
                            <option value="">— pilih barang —</option>{items.map((it) => <option key={it.id} value={it.id}>{it.name} ({it.unit})</option>)}
                          </select>
                          {c?.error && <div className="mt-1 text-xs text-red-700" data-testid={`ai-row-${i + 1}-error`}>✗ {c.error}</div>}
                          {c?.warning && <div className="mt-1 text-xs text-amber-700">⚠ {c.warning}</div>}
                        </td>
                        <td className="px-2 py-1.5 align-top num text-right text-slate-500">{c?.current_stock == null ? "—" : fmtNum(c.current_stock)}</td>
                        <td className="px-2 py-1.5 align-top"><input type="number" min="0" step="1" className="field !py-1 num w-24 text-right" value={r.quantity} onChange={(e) => setRow(r.key, { quantity: e.target.value })} aria-label={`${cfg.qty} baris ${i + 1}`} data-testid={`ai-row-${i + 1}-qty`} /></td>
                        <td className="px-2 py-1.5 align-top num text-right text-slate-500">{c?.after == null ? "—" : fmtNum(c.after)}</td>
                        <td className="px-2 py-1.5 align-top"><input className="field !py-1 text-sm" value={r.note} onChange={(e) => setRow(r.key, { note: e.target.value })} aria-label={`Catatan baris ${i + 1}`} /></td>
                        <td className="px-2 py-1.5 align-top"><button onClick={() => edit((d) => ({ ...d, rows: d.rows.filter((x) => x.key !== r.key) }))} aria-label={`Hapus baris ${i + 1}`} className="rounded p-1 text-slate-500 hover:bg-red-50 hover:text-red-700" data-testid={`ai-row-${i + 1}-remove`}><Trash2 size={14} /></button></td>
                      </tr>
                    );
                  })}
                  {draft.rows.length === 0 && <tr><td colSpan={8} className="px-3 py-6 text-center text-sm text-slate-500">AI tidak menemukan baris barang. Tambahkan baris secara manual atau unggah berkas yang lebih jelas.</td></tr>}
                </tbody>
              </table>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <button onClick={() => edit((d) => ({ ...d, rows: [...d.rows, newRow()] }))} className="btn-ghost !py-1.5 text-xs" data-testid="ai-add-row"><Plus size={13} /> Tambah baris</button>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-xs text-slate-500" data-testid="ai-summary">{dirty ? "Ada perubahan yang belum diperiksa." : check ? `${check.rows.length - check.row_errors} baris benar · ${check.row_errors} baris salah` : ""}</span>
                <button onClick={validate} disabled={busy !== "" || !dirty} className="btn-ghost" data-testid="ai-validate"><RefreshCw size={14} /> {busy === "check" ? "Memeriksa…" : "Periksa"}</button>
                <button onClick={save} disabled={!canSave} className="btn-primary" data-testid="ai-save">{busy === "save" ? "Menyimpan…" : `Simpan ${draft.rows.length} baris`}</button>
              </div>
            </div>
            <p className="text-xs text-slate-500">Simpan hanya aktif bila semua baris benar dan sudah diperiksa. Semua baris tersimpan sekaligus, atau tidak sama sekali.</p>
          </div>
        )}
      </Panel>
    </div>
  );
}
