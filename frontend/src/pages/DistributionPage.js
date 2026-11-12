import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { Truck, FileSpreadsheet, ScanText } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";
import { PageHeader, Panel } from "../components/StatCard";
import AiImportPanel from "../components/AiImportPanel";
import { Field, ItemPreview, ItemSelect, ResultCard, useItems } from "../components/FormBits";
import { witaDateTime } from "../lib/format";
import { RecipientFields } from "../components/RecipientFields";

export default function DistributionPage() {
  const [items, reload] = useItems();
  const [incidents, setIncidents] = useState([]);
  const [destinations, setDestinations] = useState([]);
  const [form, setForm] = useState(() => ({ item_id: "", quantity: "", destination: "", incident_type: "", ...witaDateTime(), recipient_kk: "", recipient_jiwa: "", notes: "" }));
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const item = items.find((i) => i.id === form.item_id);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target ? e.target.value : e }));
  const exceeds = item && Number(form.quantity) > item.currentStock;

  useEffect(() => {
    api.get("/meta/incident-types").then((r) => { setIncidents(r.data); setForm((f) => ({ ...f, incident_type: f.incident_type || r.data[0] })); });
    api.get("/meta/destinations").then((r) => setDestinations(r.data)).catch(() => {});
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const { data } = await api.post("/transactions/out", { ...form, quantity: Number(form.quantity), recipient_kk: form.recipient_kk === "" ? null : Number(form.recipient_kk), recipient_jiwa: form.recipient_jiwa === "" ? null : Number(form.recipient_jiwa) });
      setResult(data); toast.success("Penyaluran tercatat"); reload();
      setForm((f) => ({ ...f, quantity: "", notes: "", recipient_kk: "", recipient_jiwa: "", ...witaDateTime() }));
    } catch (err) { toast.error(errorMessage(err)); } finally { setBusy(false); }
  };

  return (
    <div data-testid="distribution-page">
      <PageHeader eyebrow="Transaksi" title="Catat Penyaluran" description="Distribusi logistik ke posko atau wilayah terdampak. Server menolak otomatis jika jumlah melebihi stok tersedia." actions={<><a href="#impor-ai" className="btn-ghost" data-testid="ai-out-shortcut"><ScanText size={14} /> Dari WhatsApp / dokumen</a><Link to="/app/excel#impor-out" className="btn-ghost" data-testid="bulk-out-shortcut"><FileSpreadsheet size={14} /> Impor dari Excel</Link></>} />
      <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <Panel testId="distribution-form-panel">
          <form onSubmit={submit} className="space-y-5">
            <ItemSelect items={items} value={form.item_id} onChange={set("item_id")} testId="distribution-item-select" />
            <div className="grid sm:grid-cols-3 gap-5">
              <Field label={`Jumlah${item ? ` (${item.unit})` : ""}`} hint={exceeds ? `Melebihi stok tersedia (${item.currentStock} ${item.unit})` : undefined}>
                <input type="number" min="1" required className={`field num ${exceeds ? "!border-red-500" : ""}`} value={form.quantity} onChange={set("quantity")} placeholder="0" data-testid="distribution-quantity-input" />
              </Field>
              <Field label="Tanggal (WITA)"><input type="date" required className="field" value={form.date} max={witaDateTime().date} onChange={set("date")} data-testid="distribution-date-input" /></Field>
              <Field label="Waktu (WITA)"><input type="time" required className="field" value={form.time} onChange={set("time")} data-testid="distribution-time-input" /></Field>
            </div>
            <div className="grid sm:grid-cols-2 gap-5">
              <Field label="Lokasi penyaluran" hint="Isi desa, kecamatan, atau posko. Jangan isi nama pribadi.">
                <input list="dest-list" required minLength={2} maxLength={120} className="field" value={form.destination} onChange={set("destination")} placeholder="mis. Posko Martapura Timur" data-testid="distribution-destination-input" />
                <datalist id="dest-list">{destinations.map((d) => <option key={d} value={d} />)}</datalist>
              </Field>
              <Field label="Jenis Kejadian"><select className="field" value={form.incident_type} onChange={set("incident_type")} data-testid="distribution-incident-select">{incidents.map((s) => <option key={s}>{s}</option>)}</select></Field>
            </div>
            <RecipientFields form={form} onChange={set} disabled={busy} />
            <Field label="Catatan internal (opsional)"><textarea rows={3} maxLength={500} className="field" value={form.notes} onChange={set("notes")} placeholder="Nomor surat tugas atau catatan petugas. Tidak ditampilkan ke publik." data-testid="distribution-notes-input" /></Field>
            <button type="submit" disabled={busy || !form.item_id || exceeds} className="btn-primary w-full sm:w-auto" data-testid="distribution-submit-button"><Truck size={15} /> {busy ? "Menyimpan…" : "Simpan Penyaluran"}</button>
          </form>
        </Panel>
        <div className="space-y-4"><ItemPreview item={item} /><ResultCard tx={result} onReset={() => setResult(null)} /></div>
      </div>
      <div className="mt-8"><AiImportPanel kind="out" anchor="impor-ai" title="Impor dari WhatsApp, dokumen, atau ketikan (AI)" /></div>
    </div>
  );
}
