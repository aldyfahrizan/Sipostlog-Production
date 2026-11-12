import { Link } from "react-router-dom";
import { useState } from "react";
import { ArrowDownToLine, FileSpreadsheet, ScanText } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";
import { PageHeader, Panel } from "../components/StatCard";
import AiImportPanel from "../components/AiImportPanel";
import { Field, ItemPreview, ItemSelect, ResultCard, useItems } from "../components/FormBits";
import { todayYMD } from "../lib/format";

const SOURCES = ["Pengadaan APBD", "Bantuan BNPB", "Bantuan BPBD Provinsi", "Hibah / Donasi", "Pengembalian Posko", "Lainnya"];

export default function StockInPage() {
  const [items, reload] = useItems();
  const [form, setForm] = useState({ item_id: "", quantity: "", source: SOURCES[0], date: todayYMD(), notes: "" });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const item = items.find((i) => i.id === form.item_id);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target ? e.target.value : e }));

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.post("/transactions/in", { ...form, quantity: Number(form.quantity) });
      setResult(data); toast.success("Barang masuk tercatat"); reload();
      setForm((f) => ({ ...f, quantity: "", notes: "" }));
    } catch (err) { toast.error(errorMessage(err)); } finally { setBusy(false); }
  };

  return (
    <div data-testid="stock-in-page">
      <PageHeader eyebrow="Transaksi" title="Catat Barang Masuk" description="Penerimaan logistik baru dari pengadaan, bantuan, hibah, atau pengembalian. Stok bertambah dan tercatat dalam riwayat." actions={<><button type="button" onClick={() => { document.getElementById("impor-ai")?.scrollIntoView({ behavior: "smooth" }); document.getElementById("ai-text-in")?.focus({ preventScroll: true }); }} className="btn-ghost" data-testid="ai-in-shortcut"><ScanText size={14} /> Dari WhatsApp / dokumen</button><Link to="/app/excel#impor-in" className="btn-ghost" data-testid="bulk-in-shortcut"><FileSpreadsheet size={14} /> Impor dari Excel</Link></>} />
      <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <Panel testId="stock-in-form-panel">
          <form onSubmit={submit} className="space-y-5">
            <ItemSelect items={items} value={form.item_id} onChange={set("item_id")} testId="stock-in-item-select" />
            <div className="grid sm:grid-cols-2 gap-5">
              <Field label={`Jumlah${item ? ` (${item.unit})` : ""}`}><input type="number" min="1" required className="field num" value={form.quantity} onChange={set("quantity")} placeholder="0" data-testid="stock-in-quantity-input" /></Field>
              <Field label="Tanggal"><input type="date" required className="field" value={form.date} max={todayYMD()} onChange={set("date")} data-testid="stock-in-date-input" /></Field>
            </div>
            <Field label="Sumber"><select className="field" value={form.source} onChange={set("source")} data-testid="stock-in-source-select">{SOURCES.map((s) => <option key={s}>{s}</option>)}</select></Field>
            <Field label="Catatan (opsional)"><textarea rows={3} className="field" value={form.notes} onChange={set("notes")} placeholder="No. BAST, kondisi barang, dsb." data-testid="stock-in-notes-input" /></Field>
            <button type="submit" disabled={busy || !form.item_id} className="btn-primary w-full sm:w-auto" data-testid="stock-in-submit-button"><ArrowDownToLine size={15} /> Simpan Barang Masuk</button>
          </form>
        </Panel>
        <div className="space-y-4"><ItemPreview item={item} /><ResultCard tx={result} onReset={() => setResult(null)} /></div>
      </div>
      <div className="mt-8"><AiImportPanel kind="in" anchor="impor-ai" title="Impor dari WhatsApp, dokumen, atau ketikan (AI)" /></div>
    </div>
  );
}
