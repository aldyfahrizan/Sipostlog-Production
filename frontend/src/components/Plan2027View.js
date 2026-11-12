import { CalendarClock } from "lucide-react";
import StatCard, { EmptyState, Panel } from "./StatCard";
import { StatusBadge } from "./StatusBadge";
import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtNum } from "../lib/format";

export default function Plan2027View({ data }) {
  return (
    <div data-testid="plan-2027-view">
      <div className="mb-6 flex items-center gap-2 rounded-lg border border-sky-500/30 bg-sky-500/10 p-3 text-sm text-sky-200" data-testid="plan-2027-notice">
        <CalendarClock size={16} className="shrink-0" /> Rencana pengadaan tahun anggaran 2027 — belum direalisasikan, menunggu awal tahun dan proses pengadaan.
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger">
        <StatCard testId="stat-2027-total" label="Item dalam RKA" value={data.total_items} sub="katalog logistik bencana" accent="slate" />
        <StatCard testId="stat-2027-rencana" label="Direncanakan" value={data.status_counts.rencana} sub="dianggarkan tahun 2027" accent="blue" />
        <StatCard testId="stat-2027-tidak" label="Tidak Dianggarkan" value={data.status_counts["tidak-dianggarkan"]} sub="tidak ada dalam RKA 2027" accent="slate" />
        <StatCard testId="stat-2027-categories" label="Kategori Logistik" value={data.total_categories} sub="dalam katalog RKA" accent="amber" />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.2fr_1fr]">
        <Panel title="Komposisi per Kategori" subtitle="Jumlah jenis item per kategori dalam RKA 2027" testId="panel-2027-categories">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={data.categories} layout="vertical" margin={{ left: 8, right: 36 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="category" width={150} tick={{ fill: "#94A3B8", fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip cursor={{ fill: "rgba(56,189,248,0.06)" }} contentStyle={{ background: "#0F172A", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }} formatter={(v) => [`${v} item`, "Jumlah"]} />
                <Bar dataKey="count" fill="#38BDF8" radius={[0, 6, 6, 0]} barSize={14}>
                  <LabelList dataKey="count" position="right" fill="#94A3B8" fontSize={11} fontWeight={700} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
        <Panel title="Ringkasan" subtitle="Status anggaran 2027" testId="panel-2027-summary">
          <p className="text-sm text-slate-400 leading-relaxed">Data ini diambil dari Rencana Kerja dan Anggaran (RKA) tahun 2027. Stok riil masih 0 untuk seluruh item karena pengadaan baru dapat dilakukan setelah tahun anggaran 2027 dimulai.</p>
        </Panel>
      </div>

      <Panel className="mt-6" title="Rencana Pengadaan per Item" subtitle={`${data.items.length} item dalam katalog`} testId="panel-2027-items">
        <div className="overflow-x-auto scrollbar-thin -mx-2">
          <table className="tbl">
            <thead><tr><th>Item</th><th>Kategori</th><th className="text-right">Kuantitas Rencana</th><th className="text-right">Stok Riil 2027</th><th>Status</th></tr></thead>
            <tbody>
              {data.items.map((i) => (
                <tr key={i.id} data-testid={`plan-2027-row-${i.id}`}>
                  <td><div className="font-semibold text-white">{i.name}</div><div className="text-[11px] text-slate-500 font-mono">{i.id}</div></td>
                  <td className="text-slate-400 text-xs">{i.category}</td>
                  <td className="text-right num font-bold text-white">{fmtNum(i.planQuantity)} <span className="text-slate-500 text-xs font-normal">{i.unit}</span></td>
                  <td className="text-right num" data-testid={`plan-2027-stock-${i.id}`}>{fmtNum(i.currentStock)} {i.unit}</td>
                  <td><StatusBadge status={i.status} testId={`plan-2027-status-${i.id}`} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.items.length === 0 && <EmptyState text="Belum ada rencana pengadaan." />}
      </Panel>
    </div>
  );
}
