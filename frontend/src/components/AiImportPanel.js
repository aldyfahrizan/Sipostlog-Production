import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, AlertTriangle, CheckCircle2, FileUp, MessageSquareText, Plus, RefreshCw, ScanText, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { API, api, errorMessage } from "../lib/api";
import { Panel } from "./StatCard";
import { useItems } from "./FormBits";
import { fmtNum, witaDateTime } from "../lib/format";

export const AI_KINDS = {
  opname: { label: "Stock Opname", qty: "Stok fisik", hint: "Berita acara / lembar hitung stock opname. Stok sistem disamakan dengan stok fisik di dokumen; setiap selisih dicatat sebagai transaksi Koreksi." },
  in: { label: "Barang Masuk", qty: "Masuk", hint: "BAST penerimaan, surat jalan, pesan WhatsApp, atau laporan ketikan. Satu laporan = satu penerimaan dari satu sumber." },
  out: { label: "Penyaluran", qty: "Disalurkan", hint: "BAST, surat jalan, pesan WhatsApp, atau laporan ketikan. Satu laporan = satu penyaluran ke satu lokasi." },
};
const ACCEPT = ".docx,.xlsx,.pdf,.txt,.jpg,.jpeg,.png,.webp";
const SOURCES = ["Pengadaan APBD", "Bantuan BNPB", "Bantuan BPBD Provinsi", "Hibah / Donasi", "Pengembalian Posko", "Lainnya"];
const PLACEHOLDER = {
  in: "Contoh: Lapor, hari ini masuk gudang bantuan BNPB: beras 200 kg, selimut 50 lembar, mi instan 30 dos. No. BAST 012/2026.",
  out: "Contoh: Lapor, tadi jam 14.30 disalurkan ke Desa Sungai Tabuk (banjir): beras 300 kg, selimut 40 lembar, untuk 45 KK / 160 jiwa.",
  opname: "Contoh: Hasil hitung gudang hari ini: beras 950 kg, selimut 38 lembar, mi instan 80 dos.",
};
const emptyHeader = { source: "", destination: "", incident_type: "", date: "", time: "", recipient_kk: "", recipient_jiwa: "", ref: "" };
let rowKey = 0;
const newRow = (r = {}) => ({ key: ++rowKey, item_id: r.item_id || "", quantity: r.quantity ?? "", note: r.note || "", source_name: r.source_name || "", source_unit: r.source_unit || "", match: r.match || "" });

function HeaderForm({ kind, header, set, incidents }) {
  const input = (key, label, props = {}) => (
    <label className="block text-xs text-slate-500">{label}
      <input className="field mt-1" value={header[key] ?? ""} onChange={(e) => set(key, e.target.value)} data-testid={`ai-header-${key}`} {...props} />
    </label>
  );
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="ai-header">
      {kind === "in" && <div className="sm:col-span-2">{input("source", "Sumber barang *", { list: "ai-source-list", placeholder: "mis. Bantuan BNPB" })}<datalist id="ai-source-list">{SOURCES.map((x) => <option key={x} value={x} />)}</datalist></div>}
      {kind === "out" && <div className="sm:col-span-2">{input("destination", "Lokasi tujuan *")}</div>}
      {kind === "out" && (
        <label className="block text-xs text-slate-500">Jenis kejadian *
          <select className="field mt-1" value={header.incident_type || ""} onChange={(e) => set("incident_type", e.target.value)} data-testid="ai-header-incident_type">
            <option value="">Pilih…</option>{incidents.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>
      )}
      {input("date", kind === "opname" ? "Tanggal opname" : "Tanggal *", { type: "date" })}
      {kind === "out" && input("time", "Jam (WITA) *", { type: "time" })}
      {kind === "out" && input("recipient_kk", "Jumlah KK", { type: "number", min: 0 })}
      {kind === "out" && input("recipient_jiwa", "Jumlah jiwa", { type: "number", min: 0 })}
      {input("ref", "No. BAST / dokumen")}
    </div>
  );
}

export default function AiImportPanel({ kind, title, anchor, onState }) {
  const [items] = useItems();
  const fileRef = useRef();
  const pick = useRef(0);
  const edits = useRef(0);
  const [status, setStatus] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [draft, setDraft] = useState(null); // { filename, model, read_as, header, rows }
  const [check, setCheck] = useState(null); // last server validation
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(null);
  const [checked, setChecked] = useState(false); // user confirmed the rows that carry a warning

  useEffect(() => {
    api.get("/ai-import/status").then(({ data }) => setStatus(data)).catch((e) => setError(errorMessage(e)));
    api.get("/meta/incident-types").then(({ data }) => setIncidents(data)).catch(() => {});
  }, []);

  const reset = () => { setDraft(null); setCheck(null); setDirty(false); setChecked(false); setError(""); if (fileRef.current) fileRef.current.value = ""; };

  const payload = (d = draft) => ({
    header: Object.fromEntries(Object.entries(d.header).filter(([, v]) => v !== "" && v != null)),
    rows: d.rows.map(({ item_id, quantity, note, source_name, source_unit, match }) => ({ item_id, quantity: quantity === "" ? null : quantity, note, source_name, source_unit, match })),
  });

  // Read one file (document, photo, screenshot) or the typed/pasted text.
  const read = async (file) => {
    if (busy || draft || !status?.ai_enabled) return;
    if (!file && text.trim().length < 10) { setError("Tulis atau tempel laporannya dulu, atau pilih berkas."); return; }
    const id = ++pick.current;
    setBusy("extract"); setError(""); setSaved(null); setDraft(null); setCheck(null); setChecked(false);
    const fd = new FormData();
    if (file) fd.append("file", file, file.name || "tangkapan-layar.png"); else fd.append("text", text.trim());
    try {
      const res = await fetch(`${API}/ai-import/${kind}/extract`, { method: "POST", body: fd, credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Dokumen tidak terbaca. Unggah ulang; bila tetap gagal, hubungi admin.");
      if (id !== pick.current) return;
      // A chat message often has no date or time: fill in "now" and make the user press Periksa.
      const filled = [];
      const header = { ...emptyHeader, ...data.header };
      if (kind !== "opname" && !header.date) { header.date = witaDateTime().date; filled.push("tanggal"); }
      if (kind === "out" && !header.time) { header.time = witaDateTime().time; filled.push("jam"); }
      setDraft({ filename: data.filename, model: data.model, read_as: data.read_as, truncated: data.truncated && (data.kept === "akhir" ? "akhir" : "awal"), filled, header, rows: data.rows.map(newRow) });
      setCheck(data); setDirty(filled.length > 0); if (!file) setText("");
    } catch (e) { if (id === pick.current) setError(e.message); } finally { if (id === pick.current) { setBusy(""); if (fileRef.current) fileRef.current.value = ""; } }
  };

  const edit = (fn) => { edits.current += 1; setDraft((d) => fn(d)); setDirty(true); setChecked(false); };
  const setHeader = (key, value) => edit((d) => ({ ...d, header: { ...d.header, [key]: value } }));
  const setRow = (key, patch) => edit((d) => ({ ...d, rows: d.rows.map((r) => (r.key === key ? { ...r, ...patch } : r)) }));

  const validate = async () => {
    setBusy("check"); setError("");
    const at = edits.current; // an edit made while the check is running keeps the draft unchecked
    try { const { data } = await api.post(`/ai-import/${kind}/validate`, payload()); if (at === edits.current) { setCheck(data); setDirty(false); } }
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

  // Ctrl+V of a screenshot anywhere in the panel reads it straight away.
  // Text on the clipboard (also from Excel or Word, which add a picture of it) is pasted as usual.
  const onPaste = (e) => {
    if (draft || busy || off || [...(e.clipboardData?.types || [])].includes("text/plain")) return;
    const image = [...(e.clipboardData?.files || [])].find((f) => f.type.startsWith("image/"));
    if (image) { e.preventDefault(); read(image); }
  };

  const cfg = AI_KINDS[kind];
  const off = !status?.ai_enabled;
  const hasDraft = !!draft;
  useEffect(() => { onState?.({ busy: busy !== "", hasDraft }); }, [busy, hasDraft]);

  if (status && !status.kinds.includes(kind)) {
    return <div id={anchor} className="scroll-mt-24 rounded-lg border border-slate-200 p-3 text-sm text-slate-500" data-testid="ai-no-access">Akun Anda tidak punya izin mencatat {cfg.label}, jadi impor dengan AI tidak tersedia di sini. Hubungi admin bila perlu.</div>;
  }
  const flagged = !dirty && (check?.rows || []).some((r) => r.warning);
  const canSave = draft && check?.valid && !dirty && busy === "" && (!flagged || checked);

  return (
    <div id={anchor} onPaste={onPaste} className="scroll-mt-24" data-testid={`ai-panel-${kind}`}>
      {status && off && <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800" data-testid="ai-disabled"><AlertTriangle size={15} className="mt-0.5 shrink-0" />Baca laporan dengan AI belum aktif. Hubungi admin. Sementara itu isi formulir secara manual.</div>}

      <Panel title={title || `Baca laporan ${cfg.label} dengan AI`} subtitle={cfg.hint} testId="ai-upload-panel">
        {!draft && (
          <div className="grid gap-4 lg:grid-cols-2" aria-busy={busy === "extract"}>
            <div>
              <label className="block text-xs font-semibold text-slate-400" htmlFor={`ai-text-${kind}`}><MessageSquareText size={13} className="mr-1 inline" />Tempel pesan WhatsApp atau ketik laporan</label>
              <textarea id={`ai-text-${kind}`} rows={7} maxLength={40000} className="field mt-1 text-sm" value={text} onChange={(e) => setText(e.target.value)} disabled={off || busy === "extract"} placeholder={PLACEHOLDER[kind]} data-testid="ai-text-input" />
              <div className="mt-2 flex flex-wrap items-center gap-3">
                <button type="button" onClick={() => read(null)} disabled={off || busy !== "" || text.trim().length < 10} className="btn-primary" data-testid="ai-text-submit"><ScanText size={14} /> {busy === "extract" ? "AI sedang membaca…" : "Baca dengan AI"}</button>
                <span className="text-[11px] text-slate-500">Tangkapan layar bisa langsung ditempel (Ctrl+V) di sini.</span>
              </div>
            </div>
            <button type="button" disabled={off} onClick={() => !busy && fileRef.current?.click()} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (e.dataTransfer.files[0]) read(e.dataTransfer.files[0]); }}
              className={`flex flex-col justify-center rounded-xl border-2 border-dashed border-slate-300 p-6 text-center ${off ? "opacity-60" : busy === "extract" ? "cursor-wait" : "cursor-pointer hover:border-amber-brand/60"}`} data-testid="ai-dropzone">
              {busy === "extract" ? <ScanText size={28} className="mx-auto animate-pulse text-amber-brand" /> : <FileUp size={28} className="mx-auto text-amber-brand" />}
              <span role="status" aria-live="polite" className="mt-3 block text-sm font-semibold text-white">{busy === "extract" ? "AI sedang membaca… (bisa sampai 1 menit)" : "Atau tarik berkas ke sini / klik untuk memilih"}</span>
              <span className="mt-1 block text-xs text-slate-500">Tangkapan layar atau foto (.jpg, .png), PDF, Word (.docx), Excel (.xlsx), atau ekspor chat WhatsApp (.txt) · maks. 8 MB · PDF scan dibaca sampai 5 halaman</span>
            </button>
            <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" tabIndex={-1} disabled={off || busy === "extract"} onChange={(e) => e.target.files[0] && read(e.target.files[0])} data-testid="ai-file-input" />
          </div>
        )}
        <p className="mt-2 text-[11px] text-slate-500">Isi laporan dikirim ke layanan AI pihak ketiga untuk dibaca. Jangan kirim data pribadi penerima bantuan (NIK, nama per orang).</p>

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
              <button onClick={() => { if (window.confirm("Buang hasil baca ini? Semua perbaikan Anda akan hilang.")) reset(); }} className="btn-ghost !py-1 text-xs" data-testid="ai-cancel"><X size={13} /> Buang hasil baca</button>
            </div>
            <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800"><AlertTriangle size={14} className="mt-0.5 shrink-0" />AI bisa salah membaca, terutama angka pada scan atau foto. Cocokkan setiap baris dengan laporan asli sebelum menyimpan.{draft.truncated && ` Laporan sangat panjang: hanya bagian ${draft.truncated} yang dibaca${draft.truncated === "akhir" ? " (pesan terbaru)" : ""}.`}{draft.filled?.length > 0 && ` Laporan tidak menyebut ${draft.filled.join(" dan ")}: diisi dengan saat ini. Ubah bila perlu, lalu tekan Periksa.`}</div>

            <HeaderForm kind={kind} header={draft.header} set={setHeader} incidents={incidents} />
            {check?.header_errors?.length > 0 && !dirty && <ul className="space-y-1" data-testid="ai-header-errors">{check.header_errors.map((e) => <li key={e} className="flex items-start gap-2 text-xs text-red-700"><AlertCircle size={13} className="mt-0.5 shrink-0" />{e}</li>)}</ul>}

            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full min-w-[720px] text-sm">
                <thead className="bg-slate-50 text-left text-xs uppercase tracking-wider text-slate-500"><tr><th className="px-2 py-2">No</th><th className="px-2 py-2">Tertulis di dokumen</th><th className="px-2 py-2">Barang di sistem</th><th className="px-2 py-2 text-right">Stok</th><th className="px-2 py-2 text-right">{cfg.qty}</th><th className="px-2 py-2 text-right">Sesudah</th><th className="px-2 py-2">Catatan</th><th /></tr></thead>
                <tbody className="divide-y divide-slate-200">
                  {draft.rows.map((r, i) => {
                    const c = !dirty ? check?.rows?.[i] : null;
                    return (
                      <tr key={r.key} className={c?.error ? "bg-red-50" : c?.warning ? "bg-amber-50" : ""} data-testid={`ai-row-${i + 1}`}>
                        <td className="px-2 py-1.5 align-top num text-slate-500">{i + 1}</td>
                        <td className="px-2 py-1.5 align-top text-slate-600">{r.source_name || "—"}{r.source_unit && <span className="block text-[11px] text-slate-500">{r.source_unit}</span>}</td>
                        <td className="px-2 py-1.5 align-top">
                          <select className="field !py-1 text-sm" value={r.item_id} onChange={(e) => setRow(r.key, { item_id: e.target.value, match: "" })} aria-label={`Barang baris ${i + 1}`} data-testid={`ai-row-${i + 1}-item`}>
                            <option value="">— pilih barang —</option>{items.map((it) => <option key={it.id} value={it.id}>{it.name} ({it.unit})</option>)}
                          </select>
                          {c?.error && <div className="mt-1 text-xs text-red-700" data-testid={`ai-row-${i + 1}-error`}>✗ {c.error}</div>}
                          {c?.warning && <div className="mt-1 text-xs text-amber-700" data-testid={`ai-row-${i + 1}-warning`}>⚠ {c.warning}</div>}
                        </td>
                        <td className="px-2 py-1.5 align-top num text-right text-slate-500">{c?.current_stock == null ? "—" : fmtNum(c.current_stock)}</td>
                        <td className="px-2 py-1.5 align-top"><input type="number" min="0" step="1" className="field !py-1 num w-24 text-right" value={r.quantity} onChange={(e) => setRow(r.key, { quantity: e.target.value })} aria-label={`${cfg.qty} baris ${i + 1}`} data-testid={`ai-row-${i + 1}-qty`} /></td>
                        <td className="px-2 py-1.5 align-top num text-right text-slate-500">{c?.after == null ? "—" : fmtNum(c.after)}</td>
                        <td className="px-2 py-1.5 align-top"><input className="field !py-1 text-sm" value={r.note} onChange={(e) => setRow(r.key, { note: e.target.value })} aria-label={`Catatan baris ${i + 1}`} /></td>
                        <td className="px-2 py-1.5 align-top"><button onClick={() => edit((d) => ({ ...d, rows: d.rows.filter((x) => x.key !== r.key) }))} aria-label={`Hapus baris ${i + 1}`} className="rounded p-1 text-slate-500 hover:bg-red-50 hover:text-red-700" data-testid={`ai-row-${i + 1}-remove`}><Trash2 size={14} /></button></td>
                      </tr>
                    );
                  })}
                  {draft.rows.length === 0 && <tr><td colSpan={8} className="px-3 py-6 text-center text-sm text-slate-500">AI tidak menemukan baris barang. Tambahkan baris secara manual, atau buang hasil baca lalu kirim laporan yang lebih jelas.</td></tr>}
                </tbody>
              </table>
            </div>

            {flagged && (
              <label className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
                <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => setChecked(e.target.checked)} data-testid="ai-confirm-warnings" />
                Saya sudah memeriksa baris yang ditandai
              </label>
            )}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <button onClick={() => edit((d) => ({ ...d, rows: [...d.rows, newRow()] }))} className="btn-ghost !py-1.5 text-xs" data-testid="ai-add-row"><Plus size={13} /> Tambah baris</button>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-xs text-slate-500" data-testid="ai-summary">{dirty ? "Ada perubahan yang belum diperiksa." : check ? `${check.rows.length - check.row_errors} baris benar · ${check.row_errors} baris salah` : ""}</span>
                <button onClick={validate} disabled={busy !== "" || !dirty} className="btn-ghost" data-testid="ai-validate"><RefreshCw size={14} /> {busy === "check" ? "Memeriksa…" : "Periksa"}</button>
                <button onClick={save} disabled={!canSave} className="btn-primary" data-testid="ai-save">{busy === "save" ? "Menyimpan…" : `Simpan ${draft.rows.length} baris`}</button>
              </div>
            </div>
            <p className="text-xs text-slate-500">Simpan hanya aktif bila semua baris benar dan sudah diperiksa. Baris yang ditandai ⚠ harus Anda periksa dan centang dulu. Semua baris tersimpan sekaligus, atau tidak sama sekali.</p>
          </div>
        )}
      </Panel>
    </div>
  );
}
