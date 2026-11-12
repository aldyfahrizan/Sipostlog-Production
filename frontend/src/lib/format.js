export const STATUS_LABEL = { aman: "Aman", menipis: "Menipis", habis: "Habis", rencana: "Rencana", "tidak-dianggarkan": "Tidak Dianggarkan" };
export const TYPE_LABEL = { IN: "Barang Masuk", OUT: "Penyaluran", ADJUSTMENT: "Koreksi", REVERSAL: "Pembatalan" };
export const ROLE_LABEL = { admin: "Admin", petugas: "Petugas Gudang", opname: "Petugas Stock Opname", pending: "Menunggu" };

export function fmtDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Makassar" }) + " WITA";
}

export function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("id-ID", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Makassar" });
}

export function fmtShortDay(ymd) {
  const [, m, d] = ymd.split("-");
  return `${d}/${m}`;
}

export function todayYMD(offsetDays = 0) {
  // "Hari ini" mengikuti kalender WITA (UTC+8), bukan tanggal UTC.
  const d = new Date(Date.now() + 8 * 60 * 60 * 1000);
  d.setUTCDate(d.getUTCDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

export function fmtNum(n) {
  return new Intl.NumberFormat("id-ID").format(n ?? 0);
}

export function witaDateTime() {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Makassar", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return { date: `${values.year}-${values.month}-${values.day}`, time: `${values.hour}:${values.minute}` };
}
