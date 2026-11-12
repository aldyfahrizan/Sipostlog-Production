# SIPOSTLOG v2

Sistem Informasi Pemantauan Stock Opname Logistik — BPBD Kabupaten Banjar.

## Arsitektur
- `backend/` — FastAPI + MongoDB (Motor). Semua endpoint berprefiks `/api`.
- `frontend/` — React 18 (CRA) + Tailwind CSS + Recharts.
- Autentikasi: Google login (Emergent-managed). Admin pertama ditetapkan lewat `ADMIN_EMAIL` di `backend/.env`.

## Prinsip Proyek
1. **37 item logistik awal** dimuat sekali ke database; aplikasi menolak berjalan jika daftar awal di `backend/seed_data.py` tidak berjumlah 37. Admin dapat menambah barang sesudahnya.
2. **Stok hanya berubah lewat transaksi resmi** (Barang Masuk / Penyaluran / Koreksi) dengan jejak audit permanen.
3. **Satuan tidak dicampur** — KG, DOS, BUAH, METER, dll. tidak pernah dijumlahkan menjadi satu total.
4. **Publik hanya melihat status** (Aman / Menipis / Habis) — angka stok dan penyaluran hanya untuk petugas.

## Peran
- `admin` — semua kemampuan petugas + kelola pengguna + ubah ambang minimum.
- `petugas` — dashboard internal, catat transaksi, ekspor/impor Excel.
- `pending` — akun Google baru, menunggu persetujuan admin.

## Excel
- Ekspor: Laporan Stok, Laporan Penyaluran (per item / per tujuan / rincian), Riwayat Transaksi.
- Impor: unduh Template Opname → isi kolom *Stok Fisik (Isi)* → unggah; selisih dicatat sebagai transaksi Koreksi.

## Lisensi
MIT — © BPBD Kabupaten Banjar, 2026.
