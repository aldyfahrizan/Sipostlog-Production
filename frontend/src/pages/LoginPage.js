import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, LockKeyhole, UserRound, LoaderCircle } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { Brand, Logos } from "../components/Logos";
import { errorMessage } from "../lib/api";

export default function LoginPage() {
  const { user, loading, loginAdmin } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!loading && user) navigate(user.role === "pending" ? "/menunggu" : "/app/stok", { replace: true });
  }, [user, loading, navigate]);

  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setError(""); setBusy(true);
    try { await loginAdmin(username, password); setPassword(""); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen bg-paper text-slate-700" data-testid="admin-login-page">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
          <Brand dark={false} />
          <Link to="/" data-testid="login-back-link" className="inline-flex items-center gap-2 text-sm font-semibold text-brand-blue hover:text-brand-orange transition-colors"><ArrowLeft size={16} /> Halaman Publik</Link>
        </div>
      </header>
      <main className="mx-auto max-w-md px-5 py-12 sm:py-16 animate-rise">
        <Logos size={56} className="mb-8" testId="login-form-logos" />
        <div className="text-xs font-bold text-brand-orange">BPBD KABUPATEN BANJAR</div>
        <h1 className="mt-3 text-4xl font-extrabold text-brand-blue" data-testid="admin-login-title">Masuk Admin</h1>
        <p className="mt-4 text-sm leading-relaxed text-slate-600" data-testid="admin-login-app-name">Sistem Informasi Pemantauan Stock Opname Logistik (SIPOSTLOG)</p>
        <form onSubmit={submit} className="mt-8 space-y-5" data-testid="admin-login-form">
          <div>
            <label htmlFor="admin-username" className="mb-2 block text-sm font-semibold">Nama pengguna</label>
            <div className="relative"><UserRound size={17} className="absolute left-3 top-3.5 text-slate-400" />
              <input id="admin-username" data-testid="admin-username-input" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={64} value={username} onChange={(e) => setUsername(e.target.value)} className="field !pl-10" disabled={busy} />
            </div>
          </div>
          <div>
            <label htmlFor="admin-password" className="mb-2 block text-sm font-semibold">Kata sandi</label>
            <div className="relative"><LockKeyhole size={17} className="absolute left-3 top-3.5 text-slate-400" />
              <input id="admin-password" data-testid="admin-password-input" type="password" autoComplete="current-password" required maxLength={72} value={password} onChange={(e) => setPassword(e.target.value)} className="field !pl-10" disabled={busy} aria-describedby={error ? "admin-login-error" : undefined} />
            </div>
          </div>
          {error && <p id="admin-login-error" data-testid="admin-login-error" role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
          <button type="submit" disabled={busy || loading} data-testid="admin-login-submit" className="btn-primary w-full !py-3">
            {busy ? <><LoaderCircle size={18} className="animate-spin" /> Memeriksa…</> : <>Masuk <ArrowRight size={18} /></>}
          </button>
        </form>
        <p className="mt-10 border-t border-slate-200 pt-5 text-xs text-slate-500">© {new Date().getFullYear()} BPBD Kabupaten Banjar</p>
      </main>
    </div>
  );
}