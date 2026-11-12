"""Probe how a MongoDB port answers NEW connections. No credentials are used or needed."""
import socket
import ssl
import sys
import time

host, port = sys.argv[1], int(sys.argv[2])


def raw_tcp():
    try:
        s = socket.create_connection((host, port), timeout=8)
    except OSError as e:
        return f"TIDAK BISA TERSAMBUNG: {type(e).__name__}"
    s.settimeout(4)
    try:
        data = s.recv(64)
        return "server MENUTUP koneksi segera" if data == b"" else f"server mengirim {len(data)} byte lebih dulu"
    except socket.timeout:
        return "tersambung, server menunggu (normal)"
    except OSError as e:
        return f"koneksi diputus: {type(e).__name__}"
    finally:
        s.close()


def raw_tls():
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=8) as s, ctx.wrap_socket(s) as t:
            return f"server BERBICARA TLS ({t.version()})"
    except ssl.SSLError as e:
        return f"bukan TLS / handshake gagal: {type(e).__name__}"
    except OSError as e:
        return f"gagal: {type(e).__name__}"


def hello(tls):
    from pymongo import MongoClient
    try:
        kw = dict(tls=True, tlsAllowInvalidCertificates=True) if tls else {}
        c = MongoClient(host, port, directConnection=True, serverSelectionTimeoutMS=8000, connectTimeoutMS=6000, **kw)
        h = c.admin.command("hello")
        return f"BERHASIL (maxWireVersion={h.get('maxWireVersion')}, readOnly={h.get('readOnly')})"
    except Exception as e:  # noqa: BLE001 - diagnostic
        return f"GAGAL: {type(e).__name__}: {str(e).replace(str(port), '<port>').replace(host, '<host>')[:160]}"


print("1. TCP mentah        :", raw_tcp())
print("2. Handshake TLS     :", raw_tls())
print("3. hello tanpa TLS   :", hello(False))
print("4. hello dengan TLS  :", hello(True))
results = []
for i in range(5):
    results.append("ok" if hello(False).startswith("BERHASIL") else "gagal")
    time.sleep(1)
print("5. 5x hello tanpa TLS:", results)
