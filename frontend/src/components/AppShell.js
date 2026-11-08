import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { ArrowDownToLine, ArrowUpFromLine, BarChart3, ClipboardCheck, FileSpreadsheet, History, LogOut, Menu, Package, PackagePlus, Truck, Users, X, Globe, Archive, BookOpen } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { Brand } from "./Logos";
import { ROLE_LABEL } from "../lib/format";
import { toast } from "sonner";
import { errorMessage } from "../lib/api";

const NAV = [
  { to: "/app/stok", label: "Dashboard Stok", icon: Package, id: "stok" },
  { to: "/app/penyaluran", label: "Dashboard Penyaluran", icon: BarChart3, id: "penyaluran" },
  { to: "/app/barang-masuk", label: "Catat Barang Masuk", icon: ArrowDownToLine, id: "barang-masuk" },
  { to: "/app/catat-penyaluran", label: "Catat Penyaluran", icon: Truck, id: "catat-penyaluran" },
  { to: "/app/koreksi", label: "Stock Opname / Koreksi", icon: ClipboardCheck, id: "koreksi" },
  { to: "/app/riwayat", label: "Riwayat Transaksi", icon: History, id: "riwayat" },
  { to: "/app/excel", label: "Ekspor & Impor Excel", icon: FileSpreadsheet, id: "excel" },
  { to: "/app/sop", label: "SOP Penggunaan", icon: BookOpen, id: "sop" },
  { to: "/app/pengguna", label: "Kelola Pengguna", icon: Users, id: "pengguna", admin: true },
  { to: "/app/barang", label: "Kelola Barang", icon: PackagePlus, id: "barang", admin: true },
  { to: "/app/audit", label: "Riwayat Penghapusan", icon: Archive, id: "audit", admin: true },
];

function Sidebar({ onNavigate }) {
  const { user, isAdmin, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="flex h-full flex-col">
      <div className="px-5 py-5 border-b border-slate-200"><Brand testId="sidebar-brand" /></div>
      <nav className="flex-1 overflow-y-auto scrollbar-thin px-3 py-4 space-y-1">
        {NAV.filter((n) => !n.admin || isAdmin).map(({ to, label, icon: Icon, id }) => (
          <NavLink key={to} to={to} onClick={onNavigate} data-testid={`internal-sidebar-nav-${id}`}
            className={({ isActive }) => `group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold transition-colors duration-150 ${isActive ? "bg-orange-50 text-brand-orange border-l-2 border-brand-orange" : "text-slate-600 hover:bg-blue-50 hover:text-brand-blue"}`}>
            <Icon size={17} className="shrink-0" />
            <span className="truncate">{label}</span>
          </NavLink>
        ))}
        <a href="/" className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold text-slate-400 hover:bg-ink-700/60 hover:text-white transition-colors" data-testid="internal-sidebar-nav-public">
          <Globe size={17} /> Halaman Publik
        </a>
      </nav>
      <div className="border-t border-ink-700/70 p-4">
        <div className="flex items-center gap-3">
          {user?.picture ? <img src={user.picture} alt="" className="h-9 w-9 rounded-full object-cover" referrerPolicy="no-referrer" /> : <div className="h-9 w-9 rounded-full bg-ink-700" />}
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold text-white" data-testid="sidebar-user-name">{user?.name || user?.email}</div>
            <div className="text-[11px] font-semibold uppercase tracking-wider text-amber-brand" data-testid="sidebar-user-role">{ROLE_LABEL[user?.role]}</div>
          </div>
          <button data-testid="sidebar-logout-button" title="Keluar" onClick={async () => { try { await logout(); navigate("/", { replace: true }); } catch (err) { toast.error(errorMessage(err, "Gagal keluar")); } }} className="rounded-lg p-2 text-slate-400 hover:text-red-400 hover:bg-ink-700/60 transition-colors"><LogOut size={16} /></button>
        </div>
      </div>
    </div>
  );
}

export default function AppShell() {
  const [open, setOpen] = useState(false);
  return (
    <div className="internal-light min-h-screen relative">
      <aside className="hidden lg:flex fixed inset-y-0 left-0 w-72 flex-col border-r border-slate-200 bg-white z-30"><Sidebar /></aside>
      {open && (
        <div className="lg:hidden fixed inset-0 z-40">
          <button aria-label="Tutup menu" data-testid="sidebar-backdrop" className="absolute inset-0 bg-slate-900/30" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[90vw] bg-white border-r border-slate-200 animate-rise"><Sidebar onNavigate={() => setOpen(false)} />
            <button className="absolute top-4 right-3 text-slate-400" onClick={() => setOpen(false)} data-testid="sidebar-close-button"><X size={20} /></button>
          </aside>
        </div>
      )}
      <div className="lg:pl-72">
        <header className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-slate-200 bg-white/95 backdrop-blur-md px-4 sm:px-8 py-4">
          <div className="lg:hidden"><Brand compact testId="mobile-header-brand" /></div>
          <div className="hidden lg:block text-sm font-semibold text-brand-blue" data-testid="internal-app-name">Sistem Informasi Pemantauan Stock Opname Logistik (SIPOSTLOG)</div>
          <button onClick={() => setOpen(true)} aria-label="Buka menu" className="lg:hidden rounded-lg p-2 text-brand-blue hover:bg-blue-50" data-testid="sidebar-open-button"><Menu size={20} /></button>
        </header>
        <main className="relative px-4 py-6 sm:px-8 sm:py-10 max-w-[1400px]"><Outlet /></main>
      </div>
    </div>
  );
}
