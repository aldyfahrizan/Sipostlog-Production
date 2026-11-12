import { useEffect, useState } from "react";
import { CalendarDays, CloudRain, Flame, Mountain, RefreshCw, Sun, Tag, Wind } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { PublicDistributionChart } from "./PublicDistributionChart";

const INCIDENT_ICON = { Banjir: CloudRain, Kebakaran: Flame, "Tanah Longsor": Mountain, "Angin Kencang": Wind, Kekeringan: Sun, Lainnya: Tag };

const IncidentTiles = ({ summary, total, value, onChange }) => {
  const tile = (key, label, count, sub, Icon) => {
    const active = value === key;
    return <button key={key || "all"} type="button" aria-pressed={active} onClick={() => onChange(key)} data-testid={`distribution-incident-${key ? key.toLowerCase().replace(/\s+/g, "-") : "all"}`}
      className={`flex min-w-0 flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors ${active ? "border-brand-blue bg-blue-50" : "border-slate-200 bg-white hover:border-blue-300"}`}>
      <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-600">{Icon && <Icon size={14} className={count ? "text-brand-orange" : "text-slate-400"} aria-hidden="true" />}<span className="truncate">{label}</span></span>
      <span className="font-mono text-2xl font-bold leading-none text-brand-blue">{count}</span>
      <span className="text-[11px] text-slate-600">{sub}</span>
    </button>;
  };
  return <div className="mb-6" data-testid="distribution-incident-summary">
    <p className="mb-2 text-xs font-semibold text-slate-600">Penyaluran per jenis bencana <span className="font-normal text-slate-500">· klik untuk menyaring</span></p>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
      {tile("", "Semua", total, "penyaluran", null)}
      {summary.map((s) => tile(s.incident_type, s.incident_type, s.events, s.events ? `${s.locations} lokasi` : "belum ada", INCIDENT_ICON[s.incident_type] || Tag))}
    </div>
  </div>;
};

const calendarLabel = (date) => new Date(`${date}T00:00:00+08:00`).toLocaleDateString("id-ID", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Makassar" });

export const PublicDistributionDashboard = () => {
  const [days, setDays] = useState(7);
  const [incident, setIncident] = useState("");
  const [retry, setRetry] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setData(null);
    api.get("/public/distribution", { params: { days }, signal: controller.signal })
      .then(({ data: result }) => { if (!controller.signal.aborted) setData(result); })
      .catch((err) => { if (!controller.signal.aborted) setError(errorMessage(err, "Gagal memuat data penyaluran")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [days, retry]);

  return <section id="penyaluran" className="scroll-mt-24 rounded-xl border border-slate-200 bg-white p-5 sm:p-6 shadow-sm" data-testid="public-distribution-dashboard" aria-labelledby="public-distribution-heading">
    <div className="mb-6 flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
      <div><h2 id="public-distribution-heading" data-testid="public-distribution-title" className="text-base md:text-lg font-bold text-brand-blue">Dashboard Penyaluran Logistik</h2><p data-testid="public-distribution-description" className="mt-1.5 max-w-xl text-xs leading-relaxed text-slate-500">Jenis bencana, waktu, lokasi, jenis logistik, dan penerima bantuan. Jumlah barang tidak ditampilkan.</p>{data && <p data-testid="distribution-date-range" className="mt-2 inline-flex items-center gap-1.5 text-xs text-slate-500"><CalendarDays size={13} /> {calendarLabel(data.start_date)} – {calendarLabel(data.end_date)} · WITA</p>}</div>
      <div role="group" aria-label="Rentang penyaluran" className="flex w-fit flex-wrap gap-1 rounded-full bg-slate-100 p-1" data-testid="distribution-period-selector">{[7, 14, 30].map((value) => <button key={value} type="button" data-testid={`distribution-days-filter-${value}`} aria-pressed={days === value} onClick={() => setDays(value)} className={`min-h-[44px] rounded-full px-4 py-2 text-xs font-bold transition-colors ${days === value ? "bg-brand-blue text-white" : "text-slate-600 hover:bg-slate-200 hover:text-brand-blue"}`}>{value} Hari</button>)}</div>
    </div>
    {loading && <div data-testid="distribution-loading" className="flex h-72 items-center justify-center rounded-lg bg-slate-50 text-sm text-slate-500 animate-pulse">Memuat data penyaluran logistik…</div>}
    {!loading && error && <div role="alert" data-testid="distribution-load-error" className="flex min-h-48 flex-col items-center justify-center gap-4 rounded-lg border border-red-100 bg-red-50 p-5 text-center text-sm text-red-700"><p data-testid="distribution-load-error-message">Gagal memuat data penyaluran. {error}</p><button onClick={() => setRetry((value) => value + 1)} data-testid="distribution-retry-button" className="btn-ghost"><RefreshCw size={15} /> Coba lagi</button></div>}
    {!loading && !error && data && (() => {
      const shown = incident ? data.events.filter((event) => event.incident_type === incident) : data.events;
      return <>
        {data.truncated && <p data-testid="distribution-truncated-note" className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">Data sangat banyak; hanya penyaluran terbaru yang ditampilkan.</p>}
        <IncidentTiles summary={data.incident_types || []} total={data.events.length} value={incident} onChange={setIncident} />
        {shown.length ? <PublicDistributionChart key={`${days}-${retry}-${incident}`} events={shown} /> : <div data-testid="distribution-empty-state" className="flex h-56 flex-col items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 px-5 text-center"><CalendarDays size={30} className="text-slate-400" aria-hidden="true" /><p data-testid="distribution-empty-title" className="mt-4 text-sm font-semibold text-slate-700">Tidak ada catatan penyaluran {incident ? `untuk ${incident} ` : ""}dalam {days} hari terakhir.</p><p data-testid="distribution-empty-description" className="mt-2 max-w-md text-xs leading-relaxed text-slate-500">Pilih rentang atau jenis bencana lain, atau kunjungi kembali setelah petugas mencatat penyaluran.</p></div>}
      </>;
    })()}
  </section>;
};