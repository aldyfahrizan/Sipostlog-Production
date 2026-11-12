import { useEffect, useState } from "react";
import { X, TrendingUp } from "lucide-react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, errorMessage } from "../lib/api";
import { useDialogFocus } from "./useDialogFocus";
import { fmtDateTime, fmtNum } from "../lib/format";
import { Spinner } from "./StatCard";

export default function ItemTrendModal({ itemId, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const ref = useDialogFocus(!!itemId, onClose);

  useEffect(() => {
    if (!itemId) return;
    let active = true;
    setData(null); setError("");
    api.get(`/items/${itemId}/history-chart`).then((r) => { if (active) setData(r.data); }).catch((err) => { if (active) setError(errorMessage(err)); });
    return () => { active = false; };
  }, [itemId]);

  if (!itemId) return null;
  const points = (data?.points || []).map((p) => ({ ...p, ts: new Date(p.date).getTime() })).filter((p) => !Number.isNaN(p.ts));
  const fmtTick = (ts) => new Date(ts).toLocaleDateString("id-ID", { day: "2-digit", month: "short", year: "2-digit", timeZone: "Asia/Makassar" });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" data-testid="item-trend-modal" onClick={onClose}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="item-trend-heading" className="w-full max-w-2xl rounded-lg border border-slate-200 bg-white p-4 sm:p-6 shadow-2xl animate-rise" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-amber-brand"><TrendingUp size={14} /> Tren Stok Per Item</div>
            <h3 id="item-trend-heading" className="mt-1 text-lg font-bold text-brand-blue" data-testid="item-trend-title">{data?.item_name || (error ? "Tren stok" : "Memuat…")}</h3>
          </div>
          <button onClick={onClose} title="Tutup grafik" className="rounded-lg p-1.5 text-slate-600 hover:text-brand-blue hover:bg-blue-50" data-testid="item-trend-close"><X size={18} /></button>
        </div>
        {error ? <p role="alert" data-testid="item-trend-error" className="py-8 text-sm text-red-700">{error}</p> : !data ? <Spinner /> : points.length === 0 ? (
          <div className="py-14 text-center text-sm text-slate-500" data-testid="item-trend-empty">Belum ada riwayat transaksi untuk item ini.</div>
        ) : (
          <div className="mt-5 h-64">
            <ResponsiveContainer>
              <LineChart data={points} margin={{ left: 4, right: 12, top: 8 }}>
                <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={fmtTick} tick={{ fill: "#64748B", fontSize: 10 }} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={40} />
                <YAxis allowDecimals={false} tick={{ fill: "#64748B", fontSize: 10 }} axisLine={false} tickLine={false}
                  label={{ value: `Stok (${data.unit})`, angle: -90, position: "insideLeft", fill: "#64748B", fontSize: 11, style: { textAnchor: "middle" } }} />
                <Tooltip contentStyle={{ background: "#FFFFFF", border: "1px solid #CBD5E1", color: "#334155", borderRadius: 8, fontSize: 12 }}
                  formatter={(v) => [`${fmtNum(v)} ${data.unit}`, "Stok"]} labelFormatter={(ts) => fmtDateTime(ts)} />
                <Line type="stepAfter" dataKey="stock" stroke="#C2410C" strokeWidth={2.5} dot={{ r: 3, fill: "#C2410C" }} isAnimationActive animationDuration={600} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
