import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { PageHeader } from "../components/StatCard";
import AiImportPanel, { AI_KINDS } from "../components/AiImportPanel";

export default function AiImportPage() {
  const [kinds, setKinds] = useState([]);
  const [kind, setKind] = useState("opname");
  const [panel, setPanel] = useState({ busy: false, hasDraft: false });
  const switchKind = (k) => {
    if (k === kind || panel.busy) return;
    if (panel.hasDraft && !window.confirm("Buang hasil baca ini? Semua perbaikan Anda akan hilang.")) return;
    setKind(k);
  };

  useEffect(() => {
    api.get("/ai-import/status").then(({ data }) => { setKinds(data.kinds); if (!data.kinds.includes("opname") && data.kinds[0]) setKind(data.kinds[0]); }).catch(() => {});
  }, []);

  return (
    <div data-testid="ai-import-page">
      <PageHeader eyebrow="Transaksi" title="Input dari Dokumen (AI)" description="Tempel pesan WhatsApp, ketik laporan, atau unggah tangkapan layar, foto, PDF, Word, dan Excel. AI membaca isinya dan mengisi daftar barang; Anda memeriksa, memperbaiki bila perlu, lalu menyimpan." />
      <div className="mb-4 flex flex-wrap gap-2" role="tablist">
        {Object.entries(AI_KINDS).filter(([k]) => kinds.includes(k)).map(([k, v]) => (
          <button key={k} role="tab" aria-selected={kind === k} onClick={() => switchKind(k)} disabled={panel.busy} className={kind === k ? "btn-primary" : "btn-ghost"} data-testid={`ai-tab-${k}`}>{v.label}</button>
        ))}
      </div>
      <AiImportPanel key={kind} kind={kind} onState={setPanel} />
    </div>
  );
}
