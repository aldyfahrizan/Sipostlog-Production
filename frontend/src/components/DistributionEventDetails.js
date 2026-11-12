import { AlertTriangle, Clock3, MapPin, Package, Users } from "lucide-react";
import { fmtDateTime, fmtNum } from "../lib/format";

export const DistributionEventDetails = ({ event, compact = false, prefix = "distribution-selected" }) => {
  if (!event) return null;
  return <div data-testid={`${prefix}-details`} className={compact ? "space-y-3" : "grid gap-4 sm:grid-cols-2 lg:grid-cols-5"}>
    <div><p data-testid={`${prefix}-time-label`} className="flex items-center gap-1.5 text-[11px] font-semibold uppercase text-slate-500"><Clock3 size={13} /> Waktu penyaluran</p><p data-testid={`${prefix}-time`} className="mt-1 text-xs font-semibold leading-relaxed text-slate-800">{fmtDateTime(event.occurred_at)}</p></div>
    <div><p data-testid={`${prefix}-location-label`} className="flex items-center gap-1.5 text-[11px] font-semibold uppercase text-slate-500"><MapPin size={13} /> Lokasi</p><p data-testid={`${prefix}-location`} className="mt-1 break-words text-xs font-semibold leading-relaxed text-slate-800">{event.location}</p></div>
    <div><p data-testid={`${prefix}-incident-label`} className="flex items-center gap-1.5 text-[11px] font-semibold uppercase text-slate-500"><AlertTriangle size={13} /> Jenis bencana</p><p data-testid={`${prefix}-incident`} className="mt-1 text-xs font-semibold leading-relaxed text-slate-800">{event.incident_type || "Lainnya"}</p></div>
    <div className={compact ? "" : "sm:col-span-2 lg:col-span-1"}><p data-testid={`${prefix}-items-label`} className="flex items-center gap-1.5 text-[11px] font-semibold uppercase text-slate-500"><Package size={13} /> Jenis logistik ({event.item_names.length})</p><ul data-testid={`${prefix}-items`} className="mt-1.5 flex flex-wrap gap-1">{event.item_names.map((name) => <li key={name} className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-700">{name}</li>)}</ul></div>
    <div><p data-testid={`${prefix}-recipients-label`} className="flex items-center gap-1.5 text-[11px] font-semibold uppercase text-slate-500"><Users size={13} /> Penerima</p><div className="mt-1 space-y-1 text-xs text-slate-800"><p data-testid={`${prefix}-kk`}>KK: <b>{event.recipient_kk == null ? "Belum dicatat" : fmtNum(event.recipient_kk)}</b></p><p data-testid={`${prefix}-jiwa`}>Jiwa: <b>{event.recipient_jiwa == null ? "Belum dicatat" : fmtNum(event.recipient_jiwa)}</b></p></div></div>
  </div>;
};

export const DistributionCustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const event = payload[0].payload;
  if (!event?.event_id) return null;
  return <div data-testid="distribution-tooltip" className="w-60 max-w-[calc(100vw-3rem)] rounded-lg border border-slate-300 bg-white p-4 shadow-lg"><DistributionEventDetails event={event} compact prefix="distribution-tooltip" /></div>;
};