import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { LogIn, Search, RefreshCw, ArrowDown } from "lucide-react";
import { api } from "../lib/api";
import { Brand, Logos } from "../components/Logos";
import { PublicDistributionDashboard } from "../components/PublicDistributionDashboard";

export default function PublicPage() {
  const [items, setItems] = useState([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("Semua");
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setError(false); setLoading(true);
    api.get("/public/items").then(({ data }) => setItems(data))
      .catch(() => { setItems([]); setError(true); }).finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);
  const categories = useMemo(() => ["Semua", ...Array.from(new Set(items.map((item) => item.category))).sort()], [items]);
  const filtered = useMemo(() => items.filter((item) =>
    (category === "Semua" || item.category === category) && item.name.toLowerCase().includes(query.trim().toLowerCase())
  ), [items, category, query]);
  const groups = useMemo(() => {
    const map = new Map();
    filtered.forEach((item) => { if (!map.has(item.category)) map.set(item.category, []); map.get(item.category).push(item); });
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b, "id"));
  }, [filtered]);

  return (
    <div className="min-h-screen bg-paper text-slate-800" data-testid="public-page">
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl flex-wrap gap-3 items-center justify-between px-4 sm:px-6 py-3">
          <Brand dark={false} />
          <Link to="/login" data-testid="public-login-button" className="btn-primary !px-4 !py-2"><LogIn size={15} /> Masuk Admin</Link>
        </div>
      </header>

      <section className="relative overflow-hidden border-b border-blue-100 bg-gradient-to-br from-white via-white to-blue-50" data-testid="public-overview">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-10 sm:py-14 animate-rise">
          <div className="text-xs font-bold text-brand-orange" data-testid="public-organization">BPBD KABUPATEN BANJAR</div>
          <h1 className="mt-4 font-extrabold text-brand-blue" data-testid="public-app-title"><span className="block text-4xl sm:text-5xl lg:text-6xl">SIPOSTLOG</span><span className="mt-3 block max-w-2xl text-base md:text-lg font-bold leading-relaxed">Sistem Informasi Pemantauan Stock Opname Logistik</span></h1>
          <div className="mt-7 flex flex-wrap gap-x-6 gap-y-2"><a href="#penyaluran" className="inline-flex min-h-[44px] items-center gap-2 text-sm font-semibold text-brand-blue hover:text-brand-orange transition-colors" data-testid="public-distribution-link">Lihat penyaluran logistik <ArrowDown size={15} /></a><a href="#jenis-logistik" className="inline-flex min-h-[44px] items-center gap-2 text-sm font-semibold text-brand-blue hover:text-brand-orange transition-colors" data-testid="public-catalog-link">Jelajahi jenis logistik <ArrowDown size={15} /></a></div>
        </div>
      </section>

      <main className="mx-auto max-w-6xl px-4 sm:px-6 py-8 sm:py-10">
        <PublicDistributionDashboard />
        <section id="jenis-logistik" className="mt-10 scroll-mt-24" aria-labelledby="public-items-heading">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-5">
          <h2 id="public-items-heading" className="text-base md:text-lg font-bold text-brand-blue" data-testid="public-items-heading">Jenis Logistik {!loading && !error && <span className="ml-1 text-xs font-semibold text-slate-500" data-testid="public-items-count">{filtered.length} jenis · {groups.length} kategori</span>}</h2>
          <label className="relative min-w-0 sm:w-64">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input aria-label="Cari jenis logistik" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Cari jenis logistik…" data-testid="public-search-input" className="field !rounded-full !pl-10" />
          </label>
        </div>
        <div className="flex flex-wrap gap-2" data-testid="public-category-filters">
          {categories.map((value, index) => <button key={value} onClick={() => setCategory(value)} aria-pressed={category === value}
            data-testid={value === "Semua" ? "public-category-filter-all" : `public-category-filter-${index}`}
            className={`min-h-[44px] rounded-full px-3.5 py-2 text-xs font-bold transition-colors border ${category === value ? "bg-brand-blue text-white border-brand-blue" : "bg-white text-slate-600 border-slate-200 hover:border-blue-300"}`}>{value}</button>)}
        </div>

        {error && <div role="alert" data-testid="public-load-error" className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">Daftar jenis logistik belum dapat dimuat.<button onClick={load} data-testid="public-retry-button" className="inline-flex min-h-[44px] items-center gap-2 font-bold"><RefreshCw size={15} /> Coba lagi</button></div>}
        <div className="mt-6 grid items-start gap-4 md:grid-cols-2 stagger" data-testid="public-item-grid">
          {!loading && groups.map(([name, list]) => <section key={name} className="min-w-0 rounded-lg border border-slate-200 bg-white" data-testid="public-category-group">
            <header className="flex items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-4 py-2.5 rounded-t-lg"><h3 className="min-w-0 truncate text-sm font-bold text-brand-blue">{name}</h3><span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-600 border border-slate-200">{list.length} jenis</span></header>
            <ul className="divide-y divide-slate-100">
              {list.map((item) => <li key={item.id} data-testid={`public-item-card-${item.id}`} className="flex items-center justify-between gap-3 px-4 py-2">
                <span className="min-w-0 break-words text-sm text-slate-800" data-testid={`public-item-name-${item.id}`}>{item.name}</span>
                <span className="sr-only" data-testid={`public-item-category-${item.id}`}>{item.category}</span>
                <span className="shrink-0 rounded bg-blue-50 px-2 py-0.5 font-mono text-[11px] font-semibold text-brand-blue" data-testid={`public-item-unit-${item.id}`}>{item.unit}</span>
              </li>)}
            </ul>
          </section>)}
          {loading && <div data-testid="public-loading" className="col-span-full py-10 text-center text-sm text-slate-500">Memuat jenis logistik…</div>}
          {!loading && !error && filtered.length === 0 && <div data-testid="public-empty-state" className="col-span-full py-12 text-center text-sm text-slate-500">Tidak ada jenis logistik yang cocok.</div>}
        </div>
        </section>
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-7 flex flex-wrap items-center gap-3 text-xs text-slate-500"><Logos size={32} testId="public-footer-logos" /><span data-testid="public-footer-copyright">© {new Date().getFullYear()} BPBD Kabupaten Banjar — SIPOSTLOG</span></div>
      </footer>
    </div>
  );
}