"""Single source of truth for the SOP page and the downloadable user module (PDF).

Every rule here mirrors behaviour enforced in code; update both together.
roles: which signed-in roles the section applies to (admin, petugas, opname).
"""

ALL = ["admin", "petugas", "opname"]
WAREHOUSE = ["admin", "petugas"]
ADMIN = ["admin"]

SOP_META = {
    "title": "Standar Operasional Prosedur (SOP) Penggunaan SIPOSTLOG",
    "subtitle": "Sistem Informasi Pemantauan Stock Opname Logistik — BPBD Kabupaten Banjar",
    "version": "1.0",
    "effective": "November 2026",
    # Diisi dan disahkan oleh BPBD; dibiarkan kosong agar diisi tangan pada versi cetak.
    "doc_number": "",
    "approver_title": "Kepala Pelaksana BPBD Kabupaten Banjar",
}

ROLE_ACCESS = [
    # (kemampuan, admin, petugas, opname)
    ("Melihat dashboard stok & penyaluran", True, True, True),
    ("Mencatat Barang Masuk & Penyaluran (termasuk impor Excel)", True, True, False),
    ("Stock opname / koreksi stok (form & Excel)", True, True, True),
    ("Melihat riwayat transaksi & mengunduh laporan", True, True, True),
    ("Membatalkan transaksi yang salah", True, False, False),
    ("Kelola pengguna, kelola barang, ubah ambang minimum", True, False, False),
]

SECTIONS = [
    {
        "id": "tujuan",
        "title": "Tujuan dan Ruang Lingkup",
        "roles": ALL,
        "paragraphs": [
            "SOP ini menjadi pedoman bagi seluruh pengguna SIPOSTLOG dalam mencatat, memantau, dan melaporkan stok logistik kebencanaan BPBD Kabupaten Banjar agar data stok selalu sesuai dengan kondisi fisik gudang dan dapat dipertanggungjawabkan.",
            "Ruang lingkup: penerimaan barang, penyaluran ke lokasi bencana, stock opname, pelaporan, serta pengelolaan pengguna dan katalog barang.",
        ],
        "rules": [
            "Stok hanya boleh berubah melalui transaksi resmi: Barang Masuk, Penyaluran, atau Koreksi. Setiap transaksi tercatat permanen beserta nama pencatat dan waktunya.",
            "Satuan tidak pernah dicampur. KG, DUS, BUAH, METER, dan seterusnya dicatat dan dilaporkan terpisah.",
            "Masyarakat umum hanya melihat status stok (Aman / Menipis / Habis), tanpa angka.",
        ],
    },
    {
        "id": "peran",
        "title": "Peran dan Hak Akses",
        "roles": ALL,
        "paragraphs": [
            "Setiap akun memiliki satu peran. Peran menentukan menu dan tombol yang tampil. Akun baru berstatus Menunggu (pending) sampai admin memberi peran.",
        ],
        "access_table": True,
    },
    {
        "id": "masuk",
        "title": "Masuk dan Keluar Aplikasi",
        "roles": ALL,
        "steps": [
            "Buka alamat SIPOSTLOG, lalu klik tombol Masuk Admin (untuk semua peran).",
            "Isi username dan kata sandi yang diberikan admin, lalu klik Masuk.",
            "Setelah selesai bekerja, klik ikon Keluar (panah keluar) di samping nama Anda di bagian bawah menu samping, terutama pada komputer bersama.",
        ],
        "rules": [
            "Jangan meminjamkan akun. Semua transaksi tercatat atas nama pemilik akun.",
            "Setelah 5 kali salah kata sandi, akun dikunci sementara selama 15 menit.",
            "Lupa kata sandi: hubungi admin untuk mengatur ulang kata sandi.",
        ],
    },
    {
        "id": "dashboard",
        "title": "Membaca Dashboard Stok",
        "roles": ALL,
        "steps": [
            "Buka menu Dashboard Stok untuk melihat seluruh barang beserta stok dan statusnya.",
            "Perhatikan status: Habis berarti stok 0; Menipis berarti stok sudah sama dengan atau di bawah ambang minimum; Aman berarti di atas ambang minimum.",
            "Buka Dashboard Penyaluran untuk melihat rekap penyaluran per barang dan per lokasi tujuan.",
        ],
        "rules": [
            "Barang berstatus Menipis atau Habis dilaporkan ke atasan untuk tindak lanjut pengadaan.",
        ],
    },
    {
        "id": "barang-masuk",
        "title": "Mencatat Barang Masuk",
        "roles": WAREHOUSE,
        "steps": [
            "Buka menu Catat Barang Masuk.",
            "Pilih barang, isi jumlah sesuai dokumen penerimaan (BAST/surat jalan), sumber (mis. BNPB, Dinsos Provinsi, hibah), tanggal, dan catatan bila perlu.",
            "Periksa kembali satuan yang tampil, lalu klik Simpan. Stok bertambah dan transaksi masuk ke Riwayat.",
        ],
        "rules": [
            "Catat pada hari yang sama dengan penerimaan fisik barang.",
            "Simpan dokumen BAST/surat jalan asli sebagai bukti pendukung.",
        ],
    },
    {
        "id": "penyaluran",
        "title": "Mencatat Penyaluran",
        "roles": WAREHOUSE,
        "steps": [
            "Buka menu Catat Penyaluran.",
            "Pilih barang dan isi jumlah yang disalurkan.",
            "Isi lokasi tujuan, jenis kejadian (Banjir, Kebakaran, Tanah Longsor, Angin Kencang, Kekeringan, atau Lainnya), tanggal dan jam penyaluran (WITA).",
            "Isi jumlah penerima (KK dan jiwa) bila tercantum di BAST, lalu klik Simpan.",
        ],
        "rules": [
            "Penyaluran ditolak bila stok tidak cukup. Jangan memaksakan angka; periksa stok fisik dan lakukan koreksi bila memang ada selisih.",
            "Waktu penyaluran tidak boleh di masa depan.",
            "Lokasi tujuan dan jumlah penerima ditampilkan di halaman publik, jadi tulis nama lokasi dengan jelas dan tanpa data pribadi.",
        ],
    },
    {
        "id": "impor-massal",
        "title": "Impor Massal Barang Masuk / Penyaluran via Excel",
        "roles": WAREHOUSE,
        "paragraphs": [
            "Gunakan fitur ini bila satu BAST berisi banyak barang. Satu berkas Excel = satu BAST.",
        ],
        "steps": [
            "Buka menu Ekspor & Impor Excel, pada panel Impor Barang Masuk atau Impor Penyaluran klik Unduh Template.",
            "Isi kotak kuning di bagian atas (sumber / lokasi tujuan, jenis kejadian, tanggal, jam, KK, jiwa, No. BAST).",
            "Isi kolom jumlah hanya untuk barang yang ada di BAST; kosongkan barang lain. Jangan mengubah kolom ID Item.",
            "Unggah berkas. Periksa tabel pratinjau: baris berwarna merah menunjukkan kesalahan beserta alasannya.",
            "Bila ada kesalahan, perbaiki berkas Excel lalu unggah ulang. Tombol Simpan baru aktif setelah semua baris benar.",
            "Klik Simpan. Nomor batch muncul di layar. Tulis nomor itu pada BAST fisik untuk memudahkan pencocokan.",
        ],
        "rules": [
            "Penyimpanan bersifat semua atau tidak sama sekali: tidak ada data yang tersimpan sebagian.",
            "Bila muncul pesan 'Stok berubah sejak pratinjau', unggah ulang berkas untuk melihat angka terbaru.",
            "Barang yang sama tidak boleh muncul dua kali dalam satu berkas.",
        ],
    },
    {
        "id": "input-dokumen",
        "title": "Input dari Dokumen (AI)",
        "roles": ALL,
        "paragraphs": [
            "Menu ini membaca pesan WhatsApp, laporan ketikan, tangkapan layar, foto, PDF, Word, atau Excel dengan bantuan AI, lalu mengisi daftar barang secara otomatis. Tersedia untuk Stock Opname (semua petugas) serta Barang Masuk dan Penyaluran (admin dan petugas gudang). Bagian yang sama juga ada di bawah formulir Catat Barang Masuk dan Catat Penyaluran.",
        ],
        "steps": [
            "Buka menu Input dari Dokumen (AI) lalu pilih tab Stock Opname, Barang Masuk, atau Penyaluran; atau gulir ke bagian 'Impor dari WhatsApp, dokumen, atau ketikan (AI)' di halaman Catat Barang Masuk / Catat Penyaluran.",
            "Tempel pesan WhatsApp atau ketik laporannya lalu klik Baca dengan AI; atau unggah berkas (maks. 8 MB); atau tempel tangkapan layar dengan Ctrl+V. Tunggu sampai AI selesai membaca.",
            "Cocokkan setiap baris dengan dokumen asli: nama barang, jumlah, dan kepala dokumen. Perbaiki bila ada yang salah; baris berwarna merah wajib diperbaiki atau dihapus.",
            "Klik Periksa setelah mengubah data, lalu klik Simpan. Catat nomor batch pada dokumen fisik.",
        ],
        "rules": [
            "AI dapat salah membaca, terutama angka pada scan atau foto. Petugas yang menekan Simpan bertanggung jawab atas kebenaran data.",
            "Perhatikan peringatan satuan (mis. dokumen menulis KARUNG, sistem memakai KG) dan konversikan angkanya lebih dulu.",
            "Jangan mengunggah dokumen yang memuat data pribadi penerima bantuan, karena isi dokumen dikirim ke layanan AI pihak ketiga.",
            "Penyimpanan bersifat semua atau tidak sama sekali.",
        ],
    },
    {
        "id": "opname",
        "title": "Stock Opname dan Koreksi Stok",
        "roles": ALL,
        "steps": [
            "Lakukan penghitungan fisik di gudang sesuai jadwal opname.",
            "Koreksi satu barang: buka menu Stock Opname / Koreksi, pilih barang, isi stok fisik hasil hitung dan alasan (wajib), lalu Simpan.",
            "Koreksi banyak barang: di menu Ekspor & Impor Excel unduh Template Opname, isi kolom Stok Fisik (Isi) hanya untuk barang yang stok fisiknya berbeda dari Stok Sistem, lalu unggah kembali.",
            "Periksa hasil impor; setiap selisih tercatat sebagai transaksi Koreksi.",
        ],
        "rules": [
            "Alasan koreksi wajib diisi dengan jelas (mis. 'Hasil opname triwulan III', 'Barang rusak').",
            "Koreksi bukan pengganti pencatatan Barang Masuk atau Penyaluran.",
        ],
    },
    {
        "id": "riwayat",
        "title": "Riwayat Transaksi dan Pembatalan",
        "roles": ALL,
        "steps": [
            "Buka menu Riwayat Transaksi. Saring berdasarkan rentang tanggal, jenis transaksi, atau barang.",
            "Bila menemukan transaksi yang salah, laporkan ke admin dengan menyebut tanggal, barang, dan jumlah.",
        ],
        "admin_steps": [
            "Admin membatalkan transaksi dengan tombol Batalkan dan wajib menulis alasan. Stok disesuaikan otomatis; catatan asli dan alasan pembatalan tetap tersimpan.",
            "Riwayat pembatalan dan penghapusan dapat dilihat di menu Riwayat Penghapusan.",
        ],
        "rules": [
            "Transaksi tidak pernah dihapus permanen; kesalahan diperbaiki dengan pembatalan beralasan.",
        ],
    },
    {
        "id": "laporan",
        "title": "Laporan dan Ekspor",
        "roles": ALL,
        "steps": [
            "Buka menu Ekspor & Impor Excel dan pilih rentang tanggal laporan.",
            "Unduh Laporan Stok, Laporan Penyaluran, atau Riwayat Transaksi dalam format .xlsx atau PDF berkop surat BPBD.",
            "Cetak PDF dan mintakan tanda tangan pejabat yang berwenang bila diperlukan sebagai laporan resmi.",
        ],
    },
    {
        "id": "pengguna",
        "title": "Kelola Pengguna",
        "roles": ADMIN,
        "steps": [
            "Buka menu Kelola Pengguna untuk membuat akun baru (username, nama, kata sandi awal, peran).",
            "Ubah peran atau nonaktifkan akun pegawai yang pindah tugas. Akun yang dinonaktifkan langsung keluar dari semua sesi.",
            "Atur ulang kata sandi pengguna yang lupa, lalu sampaikan kata sandi baru secara langsung (bukan lewat grup).",
        ],
        "rules": [
            "Jumlah akun dibatasi 10 akun. Hapus atau nonaktifkan akun yang tidak lagi dipakai.",
            "Penghapusan akun wajib disertai alasan dan tercatat di Riwayat Penghapusan.",
        ],
    },
    {
        "id": "barang",
        "title": "Kelola Barang dan Ambang Minimum",
        "roles": ADMIN,
        "steps": [
            "Buka menu Kelola Barang untuk menambah barang baru (nama, kategori, satuan, stok awal, ambang minimum).",
            "Ubah ambang minimum lewat menu Stock Opname / Koreksi (pilih barang, isi Ubah ambang minimum, klik Simpan); ambang ini menentukan status Menipis.",
            "Barang hanya dapat dihapus bila stoknya 0, dengan alasan yang tercatat.",
        ],
    },
    {
        "id": "publik",
        "title": "Halaman Publik",
        "roles": ALL,
        "paragraphs": [
            "Halaman depan SIPOSTLOG dapat dibuka siapa saja tanpa login. Halaman ini menampilkan katalog barang dengan status Aman / Menipis / Habis serta grafik penyaluran 7, 14, atau 30 hari terakhir (lokasi, jenis barang, jumlah KK dan jiwa).",
            "Angka stok dan nama petugas tidak pernah ditampilkan ke publik.",
        ],
    },
    {
        "id": "keamanan",
        "title": "Keamanan dan Tertib Data",
        "roles": ALL,
        "rules": [
            "Gunakan kata sandi yang kuat dan jangan dicatat di tempat terbuka atau dibagikan lewat pesan.",
            "Selalu keluar dari aplikasi setelah selesai, terutama di komputer bersama.",
            "Laporkan segera ke admin bila menemukan angka stok yang tidak sesuai dengan kondisi fisik.",
            "Simpan seluruh dokumen sumber (BAST, surat jalan, berita acara opname) untuk keperluan audit.",
        ],
    },
]


def sections_for(role: str) -> list:
    return [s for s in SECTIONS if role in s["roles"]]
