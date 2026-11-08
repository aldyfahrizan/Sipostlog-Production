# Impor Massal Barang Masuk & Penyaluran via Excel — Rancangan

Tanggal: 2026-11-08 · Status: disetujui dan diimplementasikan

## Tujuan
Petugas gudang BPBD Kabupaten Banjar bisa mencatat satu BAST yang berisi banyak item sekaligus, tanpa harus menginput satu per satu.

## Keputusan
| Topik | Keputusan |
|---|---|
| Sumber data | Template Excel yang diunduh dari aplikasi (tanpa pembaca Word) |
| Kegagalan sebagian | Pratinjau dulu; **simpan semua atau tidak sama sekali** |
| Bentuk template | Dua template terpisah: Barang Masuk dan Penyaluran |
| Info BAST | Diisi sekali di kepala berkas; tabel hanya Item + Jumlah + Catatan |
| Item ganda | Baris kedua ditandai salah (tidak dijumlahkan otomatis) |
| KK/jiwa | Dihitung sekali per batch; halaman publik mengelompokkan per batch |
| Hak akses | Admin & petugas (sama seperti input manual); peran opname tidak bisa |

## Template
- **Barang Masuk** — kepala: Sumber*, Tanggal* (DD/MM/YYYY), No. BAST. Tabel: ID Item, Nama, Kategori, Satuan, Stok Sistem, **Jumlah Masuk (Isi)**, Catatan.
- **Penyaluran** — kepala: Lokasi Tujuan*, Jenis Kejadian* (dropdown), Tanggal*, Jam* (HH:MM WITA), Jumlah KK, Jumlah Jiwa, No. BAST. Tabel sama dengan kolom **Jumlah Disalurkan (Isi)**.
- Template memuat semua item; baris dengan jumlah kosong dilewati.

## Validasi
- Baris salah: ID tidak dikenal, jumlah bukan bilangan bulat > 0, item ganda, stok tidak cukup (Penyaluran).
- Berkas salah: kepala wajib kosong/tidak valid, waktu penyaluran di masa depan, tidak ada baris terisi, bukan `.xlsx`, > 2 MB, atau template jenis lain.

## Arsitektur (pendekatan A — pratinjau + kunci stok)
- `GET /api/excel/template/{in|out}` — unduh template.
- `POST /api/excel/import/{in|out}/preview` — baca & periksa, tanpa menulis. Mengembalikan kepala, baris + status, dan `snapshot` stok per item.
- `POST /api/excel/import/{in|out}/commit` — menerima berkas yang sama + `snapshot`. Server memeriksa ulang, menolak (409) bila stok berubah sejak pratinjau, mengunci semua item dengan `reserve_stock_with_token`, memeriksa ulang di bawah kunci, lalu menulis semua transaksi.
- Snapshot sekaligus mencegah simpan ganda (klik dua kali → 409).
- Tiap transaksi impor menyimpan `batch_id`, `batch_ref` (No. BAST), `batch_file`. Tidak ada koleksi baru.
- `GET /api/transactions?batch_id=` — filter Riwayat per batch.
- `GET /api/public/distribution` — transaksi dengan `batch_id` yang sama digabung menjadi satu kejadian; KK/jiwa diambil sekali.

Catatan: MongoDB berjalan standalone (tanpa transaksi multi-dokumen). Penulisan terjadi saat semua item terkunci dan sudah diverifikasi, sehingga kegagalan di tengah hanya mungkin bila database sendiri bermasalah.

## Antarmuka
- Halaman **Ekspor & Impor Excel**: panel Impor Barang Masuk dan Impor Penyaluran (hanya admin/petugas).
- Tombol **Impor dari Excel** di halaman Barang Masuk dan Catat Penyaluran.
- Pratinjau: ringkasan kepala BAST, tabel baris dengan stok sebelum/sesudah, baris salah berwarna merah dengan alasannya; tombol Simpan aktif hanya bila semua benar.
- Setelah tersimpan: nomor batch + tautan ke Riwayat yang difilter batch tersebut.

## Pengujian
- `backend/tests/test_bulk_import.py` (in-process, `mongomock-motor`): alur sukses, satu baris salah → tidak ada yang tersimpan, stok berubah → 409, simpan ganda → 409, pesan validasi baris & kepala, berkas kosong & template salah, peran opname ditolak, pengelompokan publik + KK sekali.
- Uji browser lokal (Playwright): unduh template → isi → unggah → pratinjau → simpan → Riwayat batch → halaman publik.
