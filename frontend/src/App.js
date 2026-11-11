import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider } from "./context/AuthContext";
import AuthCallback from "./pages/AuthCallback";
import PublicPage from "./pages/PublicPage";
import LoginPage from "./pages/LoginPage";
import PendingPage from "./pages/PendingPage";
import ProtectedRoute from "./components/ProtectedRoute";
import AppShell from "./components/AppShell";
import StockDashboard from "./pages/StockDashboard";
import DistributionDashboard from "./pages/DistributionDashboard";
import StockInPage from "./pages/StockInPage";
import DistributionPage from "./pages/DistributionPage";
import AdjustPage from "./pages/AdjustPage";
import HistoryPage from "./pages/HistoryPage";
import ExcelPage from "./pages/ExcelPage";
import SopPage from "./pages/SopPage";
import AiImportPage from "./pages/AiImportPage";
import UsersPage from "./pages/UsersPage";
import ItemsPage from "./pages/ItemsPage";
import AuditPage from "./pages/AuditPage";

function AppRouter() {
  const location = useLocation();
  if (location.hash?.includes("session_id=")) return <AuthCallback />;
  return (
    <Routes>
      <Route path="/" element={<PublicPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/menunggu" element={<ProtectedRoute allowPending><PendingPage /></ProtectedRoute>} />
      <Route path="/app" element={<ProtectedRoute><AppShell /></ProtectedRoute>}>
        <Route index element={<Navigate to="/app/stok" replace />} />
        <Route path="stok" element={<StockDashboard />} />
        <Route path="penyaluran" element={<DistributionDashboard />} />
        <Route path="barang-masuk" element={<StockInPage />} />
        <Route path="catat-penyaluran" element={<DistributionPage />} />
        <Route path="koreksi" element={<AdjustPage />} />
        <Route path="riwayat" element={<HistoryPage />} />
        <Route path="excel" element={<ExcelPage />} />
        <Route path="sop" element={<SopPage />} />
        <Route path="input-dokumen" element={<AiImportPage />} />
        <Route path="pengguna" element={<ProtectedRoute adminOnly><UsersPage /></ProtectedRoute>} />
        <Route path="barang" element={<ProtectedRoute adminOnly><ItemsPage /></ProtectedRoute>} />
        <Route path="audit" element={<ProtectedRoute adminOnly><AuditPage /></ProtectedRoute>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRouter />
        <Toaster position="top-right" richColors closeButton toastOptions={{ style: { fontFamily: "Manrope, sans-serif" } }} />
      </AuthProvider>
    </BrowserRouter>
  );
}
