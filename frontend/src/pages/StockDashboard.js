import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowDownToLine, ArrowUpFromLine, ClipboardCheck, Package, Search, TrendingUp, FileText } from "lucide-react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, downloadFile, errorMessage } from "../lib/api";
import { toast } from "sonner";
import StatCard, { EmptyState, PageHeader, Panel, Spinner } from "../components/StatCard";
import { StatusBadge, TypeBadge } from "../components/StatusBadge";
import Plan2027View from "../components/Plan2027View";
import ItemTrendModal from "../components/ItemTrendModal";
import { fmtDateTime, fmtNum } from "../lib/format";
import { CancelTransactionButton, DeleteEntityButton } from "../components/AdminDeleteControls";
import { useAuth } from "../context/AuthContext";

const PALETTE = ["#F59E0B", "#38BDF8", "#34D399", "#F472B6", "#A78BFA", "#FB923C", "#22D3EE", "#E879F9", "#84CC16", "#F87171", "#94A3B8"];

function YearToggle({ year, onChange }) {
  return (
    <div className="inline-flex rounded-full border border-ink-700 bg-ink-900/60 p-1" data-testid="year-toggle">
      {["2026", "2027"].map((y) => (
        <button key={y} onClick={() => onChange(y)} data-testid={`year-toggle-${y}`}
          className={`rounded-full px-3.5 py-1.5 text-xs font-bold transition-colors ${year === y ? "bg-amber-brand text-ink-900" : "text-slate-300 hover:text-white"}`}>
          Tahun {y}
        </button>
      ))}
    </div>
  );
}

export default function StockDashboard() {
  const { isAdmin } = useAuth();
  const [year, setYear] = useState("2026");
  const [data, setData] = useState(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("semua");
  const [trendItem, setTrendItem] = useState(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [printing, setPrinting] = useState(false);
  useEffect(() => { const refresh = () => setRetry((value) => value + 1); window.addEventListener("sipostlog:stock-changed", refresh); return () => window.removeEventListener("sipostlog:stock-changed", refresh); }, []);

  useEffect(() => {
    let active = true;
    setData(null); setError("");
    api.get("/dashboard/stock", { params: { year } }).then((r) => { if (active) setData(r.data); }).catch((err) => { if (active) setError(errorMessage(err)); });
    return () => { active = false; };
  }, [year, retry]);

  const printStock = async () => {
    setPrinting(true);
    try { await downloadFile("/export/stock/pdf", "stok.pdf"); }
    catch (err) { toast.error(errorMessage(err)); }
    finally { setPrinting(false); }
  };

  const rows = useMemo(() => (data?.items || []).filter((i) => (filter === "semua" || i.status === filter) && i.name.toLowerCase().includes(q.toLowerCase())), [data, q, filter]);

  // A stale payload from the other year can still be in state during the async toggle.
  const is2026 = data?.year === "2026" && data?.realized === true;

  if (error) return <div role="alert" data-testid="stock-load-error" className="text-red-700">{error}<button className="btn-ghost ml-3" data-testid="stock-retry-button" onClick={() => setRetry((v) => v + 1)}>Coba lagi</button></div>;
  if (!data) return <Spinner />;

  if (year === "2027" || !is2026) {
    return (
      <div data-testid="stock-dashboard">
        <PageHeader eyebrow="Dashboard Internal" title="Dashboard Stok" description={`Rencana logistik tahun ${year} · ${data.total_items} jenis item.`}
          actions={<YearToggle year={year} onChange={setYear} />} />
        {year === "2027" ? <Plan2027View data={data} /> : <Spinner />}
      </div>
    );
  }

  const low = data.status_counts.menipis + data.status_counts.habis;

  return (
    <div data-testid="stock-dashboard">
      <PageHeader eyebrow="Dashboard Internal" title="Dashboard Stok" description={`Posisi stok tahun ${year} · ${data.total_items} jenis item logistik.`}
        actions={<><YearToggle year={year} onChange={setYear} /><button onClick={printStock} disabled={printing} className="btn-ghost" data-testid="stock-export-pdf-button"><FileText size={14} /> {printing ? "Mencetak…" : "Cetak PDF"}</button><Link to="/app/barang-masuk" className="btn-ghost" data-testid="quick-stock-in"><ArrowDownToLine size={14} /> Barang Masuk</Link><Link to="/app/catat-penyaluran" className="btn-primary" data-testid="quick-stock-out"><ArrowUpFromLine size={14} /> Catat Penyaluran</Link></>} />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger">
        <StatCard testId="stat-total-items" label="Jenis Item" value={data.total_items} sub="dalam katalog logistik" accent="slate" icon={Package} />
        <StatCard testId="stat-safe-items" label="Aman" value={data.status_counts.aman} sub="di atas ambang minimum" accent="green" />
        <StatCard testId="stat-low-items" label="Menipis / Habis" value={low} sub="perlu tindak lanjut" accent={low ? "red" : "green"} icon={AlertTriangle} />
        <StatCard testId="stat-weekly-activity" label="Transaksi 7 Hari" value={data.weekly_activity.IN + data.weekly_activity.OUT + data.weekly_activity.ADJUSTMENT + (data.weekly_activity.REVERSAL || 0)} sub={`${data.weekly_activity.IN} masuk · ${data.weekly_activity.OUT} salur · ${data.weekly_activity.ADJUSTMENT} koreksi · ${data.weekly_activity.REVERSAL || 0} batal`} accent="blue" icon={ClipboardCheck} />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.2fr_1fr]">
        <Panel title="Komposisi per Kategori" subtitle="Jumlah jenis item per kategori (bukan volume stok)" testId="panel-categories">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={data.categories} layout="vertical" margin={{ left: 8, right: 24 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="category" width={150} tick={{ fill: "#94A3B8", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => v.replace("Logistik ", "")} />
                <Tooltip cursor={{ fill: "rgba(245,158,11,0.06)" }} contentStyle={{ background: "#0F172A", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }} formatter={(v, n) => [`${v} item`, n === "count" ? "Jumlah" : "Menipis"]} />
                <Bar dataKey="count" radius={[0, 6, 6, 0]} barSize={14}>
                  {data.categories.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Perlu Perhatian" subtitle="Item pada atau di bawah ambang minimum" testId="panel-low-items">
          {data.low_items.length === 0 ? <EmptyState text="Semua item berada di atas ambang minimum." /> : (
            <ul className="divide-y divide-ink-700/60">
              {data.low_items.map((i) => (
                <li key={i.id} className="flex items-center justify-between py-2.5 gap-3" data-testid={`low-item-${i.id}`}>
                  <div className="min-w-0"><div className="truncate text-sm font-semibold text-white">{i.name}</div><div className="text-xs text-slate-500">{i.category}</div></div>
                  <div className="text-right"><div className="num text-sm font-bold text-red-400">{fmtNum(i.currentStock)} <span className="text-slate-500 text-xs">{i.unit}</span></div><div className="text-[11px] text-slate-500">min {fmtNum(i.minThreshold)}</div></div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.6fr_1fr]">
        <Panel title="Daftar Stok" subtitle={`${rows.length} dari ${data.total_items} item · klik baris untuk lihat tren`} testId="panel-stock-table"
          actions={<div className="flex gap-2"><select value={filter} onChange={(e) => setFilter(e.target.value)} className="field !w-auto !py-1.5 text-xs" data-testid="stock-status-filter"><option value="semua">Semua</option><option value="aman">Aman</option><option value="menipis">Menipis</option><option value="habis">Habis</option></select>
            <label className="relative"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari…" className="field !w-40 !py-1.5 !pl-8 text-xs" data-testid="stock-search-input" /></label></div>}>
          <div className="overflow-x-auto scrollbar-thin -mx-2">
            <table className="tbl">
              <thead><tr><th>Item</th><th>Kategori</th><th className="text-right">Stok</th><th className="text-right">Ambang</th><th>Status</th><th>Diperbarui</th><th></th></tr></thead>
              <tbody>
                {rows.map((i) => (
                  <tr key={i.id} data-testid={`stock-row-${i.id}`} onClick={() => setTrendItem(i.id)} className="cursor-pointer">
                    <td><div className="font-semibold text-white">{i.name}</div><div className="text-[11px] text-slate-500 font-mono">{i.id}</div></td>
                    <td className="text-slate-400 text-xs">{i.category}</td>
                    <td className="text-right num font-bold text-white" data-testid={`stock-qty-${i.id}`}>{fmtNum(i.currentStock)} <span className="text-slate-500 text-xs font-normal">{i.unit}</span></td>
                    <td className="text-right num text-slate-400">{fmtNum(i.minThreshold)}</td>
                    <td><StatusBadge status={i.status} testId={`stock-status-${i.id}`} /></td>
                    <td className="text-xs text-slate-500 whitespace-nowrap">{fmtDateTime(i.lastUpdated)}</td>
                    <td className="text-right"><div className="flex justify-end gap-1"><button data-testid={`stock-trend-button-${i.id}`} title={`Tren stok ${i.name}`} onClick={(e) => { e.stopPropagation(); setTrendItem(i.id); }} className="rounded-lg p-2 text-brand-blue hover:bg-blue-50 transition-colors"><TrendingUp size={15} /></button>{isAdmin && <DeleteEntityButton kind="item" entity={i} disabled={i.currentStock !== 0} disabledReason={i.currentStock !== 0 ? "Stok harus nol sebelum barang dihapus" : ""} />}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Aktivitas Terbaru" subtitle="8 transaksi terakhir" testId="panel-recent" actions={<Link to="/app/riwayat" data-testid="stock-view-all-history" className="text-xs font-bold text-amber-brand hover:underline">Semua →</Link>}>
          {data.recent_transactions.length === 0 ? <EmptyState text="Belum ada transaksi." /> : (
            <ul className="space-y-3">
              {data.recent_transactions.map((t) => (
                <li key={t.transaction_id} className="rounded-lg bg-ink-900/60 border border-ink-700/60 p-3">
                  <div className="flex items-center justify-between gap-2"><TypeBadge type={t.type} /><span className="text-[11px] text-slate-500">{fmtDateTime(t.occurred_at)}</span></div>
                  <div className="mt-2 text-sm font-semibold text-white truncate">{t.item_name}</div>
                  <div className="text-xs text-slate-400 num">{fmtNum(t.previous_quantity)} → <span className="text-white font-bold">{fmtNum(t.new_quantity)}</span> {t.unit} <span className={t.change_quantity >= 0 ? "text-emerald-400" : "text-amber-400"}>({t.change_quantity > 0 ? "+" : ""}{fmtNum(t.change_quantity)})</span></div>
                  <div className="text-[11px] text-slate-500 mt-1 truncate">{t.destination || t.source || t.reason} · {t.user_name}</div>
                  {isAdmin && <div className="mt-2"><CancelTransactionButton transaction={t} /></div>}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
      <ItemTrendModal itemId={trendItem} onClose={() => setTrendItem(null)} />
    </div>
  );
}
