# Panduan Deploy Sipostlog (Coolify)

Dokumen ini menjelaskan cara menjalankan Sipostlog di **Coolify self-hosted** menggunakan strategi build **Dockerfile**.

## Arsitektur
- **Satu container** menyajikan API FastAPI (`/api/*`) dan SPA React (semua path lain).
- **Database**: MongoDB yang dikelola Coolify.
- **Reverse proxy**: Traefik bawaan Coolify (TLS otomatis).

## File deployment
| File | Fungsi |
|---|---|
| `Dockerfile` | Multi-stage: `node:20-alpine` (build CRA) → `python:3.12-slim` (runtime) |
| `.dockerignore` | Mengecilkan build context |
| `backend/serve.py` | Entrypoint Uvicorn + mount SPA + fallback client-side routing |
| `docker-compose.coolify.yml` | Uji image secara lokal |

## Langkah 1 — Siapkan MongoDB di Coolify
1. Project → **+ New** → **Database** → **MongoDB**.
2. Catat connection string internal (host `...:27017`) dan kredensial root.
3. Database aplikasi: `sipostlog` (dibuat otomatis saat pertama kali menulis).

## Langkah 2 — Buat Aplikasi
1. Project → **+ New** → **Application** → **Public Repository** → `aldyfahrizan/Sipostlog-Production`, branch `main`.
2. **Build Pack**: `Dockerfile`
3. **Base Directory**: `/`
4. **Dockerfile Location**: `/Dockerfile`
5. **Ports Exposes**: `8000`
6. **Healthcheck Path**: `/api/health`

## Langkah 3 — Environment Variables
| Key | Contoh | Keterangan |
|---|---|---|
| `MONGO_URL` | `mongodb://root:PASSWORD@<service>:27017/?directConnection=true` | Connection string MongoDB Coolify |
| `DB_NAME` | `sipostlog` | Nama database |
| `CORS_ORIGINS` | `https://sipostlog.domain.id` | Domain publik aplikasi (pisahkan koma bila lebih dari satu) |
| `ADMIN_USERNAME` | `admin` | Username admin utama |
| `ADMIN_PASSWORD_HASH` | `$2b$12$...` | Hash bcrypt password admin |
| `LOCAL_ADMIN_EMAIL` | `admin@sipostlog.local` | Email internal akun admin lokal |
| `ADMIN_EMAIL` | `aldyfahrizan@gmail.com` | Email yang otomatis menjadi admin saat login Google |
| `OPENROUTER_API_KEY` | `sk-or-v1-...` | (Opsional) Mengaktifkan **Tanya AI** di halaman SOP dan menu **Input dari Dokumen (AI)**. Buat gratis di openrouter.ai → Keys |
| `OPENROUTER_MODEL` | `meta-llama/llama-3.3-70b-instruct:free` | (Opsional) Paksa satu model; bila kosong server memilih model `:free` yang tersedia |
| `OPENROUTER_VISION_MODEL` | `google/gemma-3-27b-it:free` | (Opsional) Model pembaca gambar untuk menu **Input dari Dokumen (AI)** (scan/foto); bila kosong server memilih model `:free` yang bisa membaca gambar |

**Build Argument** (opsional): `REACT_APP_BACKEND_URL`
- Kosongkan bila frontend dan API berada di domain yang sama (disarankan).
- Isi bila frontend dihosting terpisah, mis. `https://api.domain.id`.

Generate hash password baru:
```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'PASSWORD_BARU', bcrypt.gensalt()).decode())"
```

## Langkah 4 — Deploy
1. Klik **Deploy**. Build pertama memakan waktu ±5–10 menit.
2. Pantau log build di tab **Deployments**.
3. Setelah status `running:healthy`, buka domain aplikasi.

## Langkah 4b — Auto-deploy dari GitHub
Workflow `.github/workflows/deploy-coolify.yml` memanggil API Coolify setiap ada push/merge ke `main`.
1. Coolify → **Keys & Tokens** → **API tokens** → buat token dengan izin `deploy`.
2. GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**, isi `COOLIFY_TOKEN` dengan token dari langkah 1.
   Alamat panel dan UUID aplikasi sudah diatur di blok `env` workflow; bila berubah, edit di sana atau isi secret `COOLIFY_URL` / `COOLIFY_APP_UUID` untuk menimpanya.
3. Uji: GitHub → **Actions → Deploy ke Coolify → Run workflow**, lalu pantau tab **Deployments** di Coolify.

> Tanda `$` pada `ADMIN_PASSWORD_HASH` wajib dilindungi: centang **Is Literal** pada variabel tersebut. Tanpa itu Coolify memotong hash dan admin tidak bisa login (`/api/health` menampilkan `admin_password_config: "invalid"`).

## Langkah 5 — Verifikasi
1. `https://<domain>/api/health` → `{"status":"ok","items":37,...}`
2. Halaman publik tampil (katalog tanpa angka stok).
3. Login `/login` dengan `ADMIN_USERNAME` + password.
4. Reload halaman tetap login (cookie sesi bekerja).
5. Catat transaksi untuk memastikan koneksi MongoDB berfungsi.

## Catatan VPS 2 GB
- Build frontend memakai `NODE_OPTIONS=--max-old-space-size=1024` dan `GENERATE_SOURCEMAP=false`.
- **Swap 2 GB wajib** agar build tidak OOM:
  ```bash
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  sudo sysctl -w vm.swappiness=10
  ```
- Uvicorn dijalankan 1 worker.

## Troubleshooting
- **`Asal permintaan tidak diizinkan`** → `CORS_ORIGINS` tidak memuat domain persis (termasuk `https://`, tanpa trailing slash).
- **Build gagal `Killed` / OOM** → swap belum aktif atau RAM habis; cek `free -m` dan `dmesg | tail`.
- **Container `unhealthy`** → cek log container; pastikan `MONGO_URL` dapat dijangkau dari dalam container.
- **Login tidak bertahan** → cookie memakai `Secure` + `SameSite=None`, jadi **wajib HTTPS**.
