import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Download, FileText, Archive, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api, downloadFile, errorMessage } from "../lib/api";
import { EmptyState, PageHeader, Panel, Spinner } from "../components/StatCard";
import { TransactionTable } from "../components/TransactionTable";
import { useItems } from "../components/FormBits";
import { useAuth } from "../context/AuthContext";
import DateRangePicker from "../components/DateRangePicker";
import { todayYMD } from "../lib/format";

export default function HistoryPage() {
  const [items] = useItems();
  const { isAdmin } = useAuth();
  const [params] = useSearchParams();
  const [range, setRange] = useState({ start: todayYMD(-29), end: todayYMD() });
  const [type, setType] = useState(params.get("type") || "");
  const [itemId, setItemId] = useState(params.get("item_id") || "");
  const [status, setStatus] = useState("active");
  const [batchId, setBatchId] = useState(params.get("batch_id") || "");
  const [rows, setRows] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!range.start || !range.end) return;
    const controller = new AbortController();
    setRows(null); setError("");
    api.get("/transactions", { signal: controller.signal, params: { ...range, status, type: type || undefined, item_id: itemId || undefined, batch_id: batchId || undefined, limit: 501 } })
      .then(({ data }) => setRows(data)).catch((err) => { if (!controller.signal.aborted) { setError(errorMessage(err)); setRows([]); } });
    return () => controller.abort();
  }, [range, type, itemId, batchId, status, revision]);
  const exportFile = async (key, path, name) => {
    setBusy(key);
    try { const filename = await downloadFile(path, name); toast.success(`Berkas ${filename} diunduh`); }
    catch (err) { toast.error(err.message); } finally { setBusy(""); }
  };
  return <div data-testid="history-page">
    <PageHeader eyebrow="Pencatatan Gudang" title="Riwayat Transaksi" description="Admin dapat membatalkan transaksi yang salah. Stok disesuaikan otomatis, sementara catatan asli dan alasan pembatalan tetap tersimpan."
      actions={<>{isAdmin && <Link to="/app/audit" data-testid="history-audit-link" className="btn-ghost"><Archive size={14} /> Riwayat Penghapusan</Link>}<button onClick={() => exportFile("pdf", `/export/transactions/pdf?start=${range.start}&end=${range.end}`, "riwayat.pdf")} disabled={!!busy || status !== "active"} className="btn-ghost" data-testid="history-export-pdf-button"><FileText size={14} /> Cetak PDF aktif</button><button onClick={() => exportFile("xlsx", `/export/transactions?start=${range.start}&end=${range.end}`, "riwayat.xlsx")} disabled={!!busy || status !== "active"} className="btn-primary" data-testid="history-export-button"><Download size={14} /> Ekspor aktif</button></>} />
    <div className="mb-6 flex flex-wrap items-center gap-3">
      {batchId ? <span data-testid="history-batch-filter" className="inline-flex items-center gap-2 rounded-full border border-amber-brand/50 px-3 py-1 text-xs">Batch impor <b className="num">{batchId}</b><button onClick={() => setBatchId("")} className="font-bold" aria-label="Hapus filter batch">×</button></span> : <DateRangePicker value={range} onChange={setRange} />}
      <select aria-label="Jenis transaksi" value={type} onChange={(event) => setType(event.target.value)} className="field !w-auto !py-1.5 text-xs" data-testid="history-type-filter"><option value="">Semua jenis</option><option value="IN">Barang Masuk</option><option value="OUT">Penyaluran</option><option value="ADJUSTMENT">Koreksi</option><option value="REVERSAL">Pembatalan</option></select>
      <select aria-label="Barang" value={itemId} onChange={(event) => setItemId(event.target.value)} className="field !w-auto !py-1.5 text-xs max-w-[240px]" data-testid="history-item-filter"><option value="">Semua barang</option>{items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
      {isAdmin && <select aria-label="Status transaksi" value={status} onChange={(event) => setStatus(event.target.value)} className="field !w-auto !py-1.5 text-xs" data-testid="history-status-filter"><option value="active">Aktif</option><option value="cancelled">Dibatalkan</option><option value="all">Semua status</option></select>}
    </div>
    {error && <div role="alert" data-testid="history-load-error" className="mb-4 text-sm text-red-700">{error}<button onClick={() => setRevision((value) => value + 1)} data-testid="history-retry-button" className="btn-ghost ml-3"><RefreshCw size={14} /> Coba lagi</button></div>}
    <Panel testId="history-table-panel" title={!rows ? "Memuat…" : rows.length > 500 ? "500+ transaksi" : `${rows.length} transaksi`} subtitle={rows && rows.length > 500 ? "Hanya 500 transaksi terbaru yang ditampilkan. Persempit tanggal atau filter untuk melihat sisanya." : undefined}>{!rows ? <Spinner /> : rows.length === 0 ? <EmptyState text="Tidak ada transaksi pada filter ini." /> : <TransactionTable rows={rows.slice(0, 500)} onCancelled={() => setRevision((value) => value + 1)} />}</Panel>
  </div>;
}