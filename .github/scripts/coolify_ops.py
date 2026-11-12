#!/usr/bin/env python3
"""Operasi Coolify lewat API, dijalankan dari runner GitHub Actions.

Log workflow di repo publik bisa dibaca siapa saja. Aturan berkas ini:
  * dari jawaban API hanya kolom di daftar putih yang dicetak;
  * teks bebas (log container / deploy) selalu lewat scrub();
  * setiap nilai rahasia yang dikenal didaftarkan ke ::add-mask:: sebelum ada teks bebas dicetak;
  * token dibaca dari secret COOLIFY_TOKEN atau dari berkas event, tidak pernah lewat ekspresi
    workflow (ekspresi yang sudah diisi tercetak di kepala langkah sebelum masker aktif).

Hanya pustaka standar Python.
"""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://103.127.96.32:8000"
DEFAULT_APP_UUID = "x43jcxgopn0ec7h5acs71bbw"
DEFAULT_APP_URL = "http://x43jcxgopn0ec7h5acs71bbw.103.127.96.32.sslip.io"

READ_ONLY = {"status", "db-logs", "app-logs", "deploy-logs", "ai-probe"}
MUTATING = {"db-restart", "db-private", "db-public", "deploy", "env-set", "app-set"}

# Env vars whose values are not secret; everything else is masked by default.
NON_SECRET_ENV = {
    "DB_NAME", "ADMIN_USERNAME", "CORS_ORIGINS", "LOCAL_ADMIN_EMAIL", "PORT", "AI_BASE_URL", "AI_MODEL",
    "AI_VISION_MODEL", "OPENROUTER_MODEL", "OPENROUTER_VISION_MODEL", "REACT_APP_BACKEND_URL",
    "STARTUP_DB_ATTEMPTS", "NODE_OPTIONS", "GENERATE_SOURCEMAP",
}
# Application settings that app-set may change (integers only).
APP_SETTABLE = {"health_check_interval", "health_check_timeout", "health_check_retries", "health_check_start_period"}

SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|private_key|api_key|apikey|credential|_url$|^url$|_conf$|init_scripts"
    r"|^value$|^real_value$|webhook|dockerfile|docker_compose|custom_labels|docker_run_options|_command$"
    r"|validation_logs|configuration)"
)
PLAIN_WORDS = {"true", "false", "null", "none", "yes", "no", "ya", "tidak"}
MONGO_URL_OPTIONS = {
    "directConnection", "authSource", "replicaSet", "tls", "ssl", "retryWrites", "w", "appName", "authMechanism",
    "serverSelectionTimeoutMS", "connectTimeoutMS", "socketTimeoutMS", "maxPoolSize", "readPreference",
    "tlsAllowInvalidCertificates", "tlsCAFile", "tlsInsecure",
}

# Tests shrink every wait; in production the scale is 1.
_TIME_SCALE = float(os.environ.get("COOLIFY_OPS_TIME_SCALE") or 1)


def now() -> float:
    return time.time() / _TIME_SCALE


def pause(seconds: float) -> None:
    time.sleep(seconds * _TIME_SCALE)


# Repository secrets the workflow hands over as SECRET_ENV_<NAME>, for env-set and ai-probe.
SECRET_ENV_PREFIX = "SECRET_ENV_"

UUID_LIKE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{5,62}")

CFG: dict = {}
_SECRETS: set[str] = set()
_STATE = {"env_secrets_loaded": False, "db_secrets_loaded": False}


# ---------- output discipline ----------
def _emit_mask(value: str) -> None:
    escaped = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    sys.stdout.write(f"::add-mask::{escaped}\n")
    sys.stdout.flush()


def mask(value, force: bool = False) -> None:
    """Register a secret: GitHub hides it in the log and scrub() removes it from our own output."""
    if value is None or isinstance(value, (bool, dict, list)):
        return
    text = str(value)
    parts = {text.strip()} | {line.strip() for line in text.splitlines()}
    for part in parts:
        if len(part) < 4 or part in _SECRETS:
            continue
        if not force and (part.lower() in PLAIN_WORDS or (part.isdigit() and len(part) <= 5)):
            continue
        _SECRETS.add(part)
        _emit_mask(part)


def mask_url_credentials(value) -> None:
    for match in re.finditer(r"[a-z][a-z0-9+.\-]*://[^\s\"'<>]+", str(value), re.I):
        try:
            password = urllib.parse.urlsplit(match.group(0)).password
        except ValueError:
            continue
        if password:
            mask(password, force=True)
            mask(urllib.parse.unquote(password), force=True)


_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "***kunci-privat***"),
    (re.compile(r"((?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis|amqp)://)[^@\s/\"']+@", re.I), r"\1***@"),
    (re.compile(r"(?:\\?\$){1,2}2[aby](?:\\?\$){1,2}\d{2}(?:\\?\$){1,2}[./A-Za-z0-9]{20,}"), "***bcrypt***"),
    (re.compile(r"\b\d{1,6}\|[A-Za-z0-9]{30,}"), "***token***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"), "***kunci***"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "***token***"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "***token***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._|\-]{12,}"), r"\1***"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b(\"?\s*[=:]\s*\"?)[^\s\"',;&]{4,}"), r"\1\2***"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@(?:gmail|yahoo|outlook|hotmail)\.[a-z.]+", re.I), "***email***"),
]
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def scrub(text, hide_ips: bool = False) -> str:
    text = str(text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "***")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if hide_ips:
        keep = CFG.get("server_host", "")
        text = _IPV4.sub(lambda m: m.group(0) if m.group(0) == keep or is_private_ip(m.group(0)) else "<ip>", text)
    return text


def is_private_ip(address: str) -> bool:
    """Loopback, RFC 1918, link-local and CGNAT ranges: container and host-internal addresses."""
    try:
        octets = [int(part) for part in address.split(".")]
    except ValueError:
        return False
    if len(octets) != 4 or any(octet > 255 for octet in octets):
        return False
    first, second = octets[0], octets[1]
    return (first in (0, 10, 127) or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168)
            or (first == 169 and second == 254) or (first == 100 and 64 <= second <= 127))


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x1b]")


def _safe_line(line: str) -> str:
    """A printed line must never be read by the runner as a workflow command (`::cmd::`, `##[cmd]`).

    Log text comes from containers and so from anyone who can send them a request; a line such as
    `::stop-commands::x` would switch off the masking of everything printed after it.
    """
    line = _CONTROL.sub(" ", line)
    stripped = line.lstrip()
    if stripped.startswith("::") or stripped.startswith("##"):
        line = "| " + line
    return line


def out(message="", hide_ips: bool = False) -> None:
    for line in scrub(message, hide_ips=hide_ips).split("\n"):
        print(_safe_line(line), flush=True)


def fail(message: str) -> None:
    single = _CONTROL.sub(" ", scrub(message).replace("\n", " "))
    sys.stdout.write(f"::error::{single}\n")
    sys.stdout.flush()
    out(f"GAGAL: {single}")
    sys.exit(1)


def section(title: str) -> None:
    out(f"\n== {title} ==")


# ---------- inputs ----------
def load_inputs() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle).get("inputs") or {}
    except (OSError, ValueError, AttributeError):
        raw = {}
    return {key: ("" if value is None else str(value)) for key, value in raw.items()}


# ---------- HTTP ----------
def http(method: str, url: str, body=None, token: str | None = None, timeout: int = 30):
    """Returns (status, parsed JSON or text). Status 0 means the request never got an answer."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except Exception as error:  # noqa: BLE001 - network failures of any kind are reported, not raised
        return 0, f"{type(error).__name__}: {getattr(error, 'reason', error)}"
    text = raw.decode("utf-8", "replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def api(method: str, path: str, body=None, timeout: int = 30):
    return http(method, CFG["api"] + path, body=body, token=CFG["token"], timeout=timeout)


def api_post_or_get(path: str):
    """Coolify moved action endpoints from GET to POST; older panels only answer GET."""
    status, payload = api("POST", path)
    if status == 405:
        older_status, older_payload = api("GET", path)
        if older_status not in (404, 405):
            return older_status, older_payload
    return status, payload


def reason(payload) -> str:
    if isinstance(payload, dict):
        text = payload.get("message") or payload.get("error") or ""
        if payload.get("errors"):
            text = f"{text} {json.dumps(payload['errors'], ensure_ascii=False)}"
        return str(text)[:400] or "(tanpa pesan)"
    return re.sub(r"\s+", " ", str(payload))[:200]


def explain(status: int, payload) -> str:
    if status == 0:
        return f"tidak tersambung ({payload})"
    if status == 401:
        return "HTTP 401: token ditolak (salah atau sudah dicabut)"
    if status == 403:
        return f"HTTP 403: token tidak punya izin untuk ini, atau API Coolify dimatikan - {reason(payload)}"
    return f"HTTP {status}: {reason(payload)}"


# ---------- secret registration ----------
def register_secrets(node, key: str = "") -> None:
    """Mask every string that sits under a sensitive-looking key anywhere in an API answer."""
    if isinstance(node, dict):
        if "key" in node and str(node.get("key")) in NON_SECRET_ENV and ("value" in node or "real_value" in node):
            return
        for child_key, child in node.items():
            register_secrets(child, str(child_key))
    elif isinstance(node, list):
        for child in node:
            register_secrets(child, key)
    elif isinstance(node, (str, int, float)) and not isinstance(node, bool) and key and SENSITIVE_KEY.search(key):
        mask(node)
        mask_url_credentials(node)


def env_list(payload) -> list:
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("envs") or []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def load_envs() -> list:
    status, payload = api("GET", f"/applications/{CFG['app']}/envs")
    if status != 200:
        out(f"Variabel lingkungan tidak terbaca: {explain(status, payload)}")
        return []
    envs = env_list(payload)
    for env in envs:
        if str(env.get("key", "")) in NON_SECRET_ENV:
            continue
        for field in ("value", "real_value"):
            mask(env.get(field))
            mask_url_credentials(env.get(field))
    # Coolify strips the values for tokens without read:sensitive (or whose owner is not a team admin).
    # Then nothing was registered, so free text must not be printed on the strength of this call.
    _STATE["env_secrets_loaded"] = not envs or any(env.get("value") or env.get("real_value") for env in envs)
    return envs


def load_databases() -> list:
    status, payload = api("GET", "/databases")
    if status != 200 or not isinstance(payload, list):
        out(f"Daftar database tidak terbaca: {explain(status, payload)}")
        return []
    register_secrets(payload)
    databases = [item for item in payload if isinstance(item, dict)]
    for database in databases:
        if database.get("public_port"):
            mask(database["public_port"], force=True)
    _STATE["db_secrets_loaded"] = not databases or any(
        database.get("internal_db_url") or database.get("mongo_initdb_root_password") for database in databases)
    return databases


def require_secrets_loaded() -> None:
    """Free text may only be printed once every secret we could find has been registered."""
    if not _STATE["env_secrets_loaded"]:
        load_envs()
    if not _STATE["db_secrets_loaded"]:
        load_databases()
    if not (_STATE["env_secrets_loaded"] and _STATE["db_secrets_loaded"]):
        fail("Token ini tidak bisa melihat nilai rahasia (butuh izin read:sensitive dan pemilik token admin tim), "
             "jadi log tidak dicetak: tanpa daftar rahasia, penyamaran di log publik tidak bisa dijamin.")


# ---------- lookups ----------
def is_mongo(database: dict) -> bool:
    blob = " ".join(str(database.get(field) or "") for field in ("database_type", "type", "image")).lower()
    return "mongo" in blob


def find_database(inputs: dict) -> dict:
    databases = load_databases()
    wanted = inputs.get("db_uuid", "").strip()
    chosen = [d for d in databases if d.get("uuid") == wanted] if wanted else [d for d in databases if is_mongo(d)]
    if len(chosen) != 1:
        fail(f"Butuh tepat satu database MongoDB, ditemukan {len(chosen)} dari {len(databases)}. Isi input db_uuid.")
    return chosen[0]


def int_input(inputs: dict, name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(inputs.get(name) or default).strip())
    except ValueError:
        value = default
    return max(low, min(value, high))


def fields(node: dict, names: list) -> str:
    if not isinstance(node, dict):
        return "(tidak ada data)"
    return ", ".join(f"{name}={node.get(name)}" for name in names if node.get(name) not in (None, ""))


DB_FIELDS = ["name", "uuid", "database_type", "image", "status", "server_status", "is_public", "public_port_timeout",
             "enable_ssl", "ssl_mode", "restart_count", "last_restart_at", "last_restart_type", "last_online_at",
             "started_at", "limits_memory", "limits_cpus", "created_at", "updated_at"]
APP_FIELDS = ["name", "uuid", "status", "fqdn", "git_branch", "git_commit_sha", "build_pack", "ports_exposes",
              "health_check_enabled", "health_check_path", "health_check_interval", "health_check_timeout",
              "health_check_retries", "health_check_start_period", "custom_healthcheck_found", "restart_count",
              "last_restart_at", "last_online_at", "limits_memory", "updated_at"]
SERVER_FIELDS = ["name", "uuid", "ip", "proxy_type", "unreachable_count", "high_disk_usage_notification_sent",
                 "unreachable_notification_sent"]
SERVER_SETTINGS = ["is_reachable", "is_usable", "is_build_server", "concurrent_builds", "is_sentinel_enabled",
                   "is_metrics_enabled", "docker_cleanup_threshold", "force_docker_cleanup"]
DEPLOY_FIELDS = ["deployment_uuid", "status", "commit", "is_webhook", "is_api", "force_rebuild", "restart_only",
                 "rollback", "created_at", "updated_at"]


# ---------- probes that need no token ----------
def app_health() -> tuple[int, str]:
    status, payload = http("GET", CFG["app_url"] + "/api/health", timeout=15)
    if status == 200 and isinstance(payload, dict):
        shown = {k: payload.get(k) for k in ("status", "items", "ai", "admin_password_config") if k in payload}
        return status, " ".join(f"{k}={v}" for k, v in shown.items())
    return status, reason(payload) if status else str(payload)


def app_health_retry(attempts: int = 4, gap: int = 10) -> tuple[int, str]:
    code, text = app_health()
    for _ in range(attempts - 1):
        if code == 200:
            break
        pause(gap)
        code, text = app_health()
    return code, text


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    buffer = b""
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            break
        buffer += chunk
    return buffer


def mongo_hello(host: str, port: int, timeout: float = 6.0) -> tuple[bool, str]:
    """Open a brand-new connection and send an unauthenticated `hello` (no password involved)."""
    body = b"\x10hello\x00" + struct.pack("<i", 1) + b"\x02$db\x00" + struct.pack("<i", 6) + b"admin\x00" + b"\x00"
    document = struct.pack("<i", len(body) + 4) + body
    payload = struct.pack("<I", 0) + b"\x00" + document
    message = struct.pack("<iiii", 16 + len(payload), 1, 0, 2013) + payload
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(message)
            header = _recv_exact(sock, 16)
            if len(header) < 16:
                return False, "DITUTUP server tanpa jawaban"
            length, _, _, opcode = struct.unpack("<iiii", header)
            rest = _recv_exact(sock, max(0, min(length - 16, 65536)))
            if opcode == 2013 and b"maxWireVersion" in rest:
                return True, "mongod menjawab hello"
            return False, f"jawaban tak dikenal (opcode {opcode})"
    except ConnectionRefusedError:
        return False, "port tertutup (ditolak)"
    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
        return False, "DITUTUP server seketika (reset)"
    except (socket.timeout, TimeoutError):
        return False, "tidak menjawab (timeout)"
    except OSError as error:
        return False, f"gagal ({type(error).__name__})"


def probe_panel() -> None:
    section("Panel Coolify")
    for path in ("/api/health", "/api/v1/health", "/login"):
        status, payload = http("GET", CFG["base"] + path, timeout=15)
        note = "" if status else f" ({payload})"
        out(f"  GET {path} -> HTTP {status}{note}")


# ---------- actions ----------
def show_databases(databases: list) -> None:
    section("Database")
    if not databases:
        out("  (tidak ada / tidak terbaca)")
    for database in databases:
        public_port = "ada" if database.get("public_port") else "tidak ada"
        backups = database.get("backup_configs")
        backup_note = f", jadwal_backup={len(backups)}" if isinstance(backups, list) else ""
        out(f"  - {fields(database, DB_FIELDS)}, public_port={public_port}{backup_note}")


def describe_envs(envs: list, databases: list) -> None:
    section("Variabel lingkungan aplikasi (hanya nama)")
    if not envs:
        out("  (tidak terbaca)")
        return
    production = [e for e in envs if not e.get("is_preview")]
    for env in sorted(production, key=lambda e: str(e.get("key"))):
        flags = [name for name, field in (("literal", "is_literal"), ("build", "is_buildtime"),
                                          ("runtime", "is_runtime"), ("terkunci", "is_shown_once")) if env.get(field)]
        out(f"  - {env.get('key')} [{', '.join(flags)}]")
    values = {str(e.get("key")): (e.get("real_value") or e.get("value")) for e in production}
    section("Pemeriksaan nilai (tanpa menampilkan nilainya)")
    mongo_url = values.get("MONGO_URL")
    if not mongo_url:
        out("  MONGO_URL: nilai tidak terlihat oleh token ini / belum diisi")
    else:
        try:
            parts = urllib.parse.urlsplit(str(mongo_url).strip().strip("'\""))
            hosts = [h.rsplit("@", 1)[-1] for h in parts.netloc.split(",")]
            names = [h.rsplit(":", 1)[0] for h in hosts]
            ports = [h.rsplit(":", 1)[1] if ":" in h else "27017" for h in hosts]
            db_uuids = {str(d.get("uuid")) for d in databases}
            kind = ["internal (nama container database)" if n in db_uuids else
                    "PUBLIK (alamat IP server)" if n == CFG["server_host"] else "lain" for n in names]
            port_note = ["27017" if p == "27017" else "bukan 27017" for p in ports]
            seen = set(urllib.parse.parse_qs(parts.query).keys())
            options = sorted(seen & MONGO_URL_OPTIONS) + ([f"+{len(seen - MONGO_URL_OPTIONS)} lain"]
                                                         if seen - MONGO_URL_OPTIONS else [])
            scheme = parts.scheme if parts.scheme in ("mongodb", "mongodb+srv") else "bukan mongodb"
            out(f"  MONGO_URL: skema={scheme}, host={kind}, port={port_note}, opsi={options}, "
                f"ada_user={'ya' if parts.username else 'tidak'}")
        except ValueError:
            out("  MONGO_URL: tidak bisa diurai sebagai URL")
    admin_hash = str(values.get("ADMIN_PASSWORD_HASH") or "")
    if admin_hash:
        valid = bool(re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", admin_hash.strip().strip("'\"")))
        out(f"  ADMIN_PASSWORD_HASH: format bcrypt {'valid' if valid else 'TIDAK valid'}, panjang={len(admin_hash)}")
    for key in ("AI_BASE_URL", "AI_MODEL", "AI_VISION_MODEL", "OPENROUTER_MODEL"):
        if values.get(key):
            out(f"  {key}: {values[key]}")
    for key in ("AI_API_KEY", "OPENROUTER_API_KEY"):
        out(f"  {key}: {'terisi' if values.get(key) else 'kosong / tidak terlihat'}")


def list_deployments(take: int = 8) -> list:
    status, payload = api("GET", f"/deployments/applications/{CFG['app']}?skip=0&take={take}")
    if status != 200:
        out(f"  Riwayat deploy tidak terbaca: {explain(status, payload)}")
        return []
    if isinstance(payload, dict):
        payload = payload.get("deployments") or payload.get("data") or []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def act_status(inputs: dict) -> None:
    status, payload = api("GET", "/version")
    section("Autentikasi")
    if status != 200:
        fail(f"Token tidak bisa dipakai: {explain(status, payload)}")
    out(f"  Token diterima. Versi Coolify: {str(payload)[:40]}")

    section("Server")
    status, servers = api("GET", "/servers")
    if status == 200 and isinstance(servers, list):
        register_secrets(servers)
        for server in servers:
            settings = server.get("settings") if isinstance(server.get("settings"), dict) else {}
            out(f"  - {fields(server, SERVER_FIELDS)} | {fields(settings, SERVER_SETTINGS)}")
    else:
        out(f"  tidak terbaca: {explain(status, servers)}")

    databases = load_databases()
    show_databases(databases)

    section("Aplikasi")
    status, application = api("GET", f"/applications/{CFG['app']}")
    if status == 200 and isinstance(application, dict):
        register_secrets(application)
        out(f"  - {fields(application, APP_FIELDS)}")
    else:
        out(f"  tidak terbaca: {explain(status, application)}")

    section("Semua resource di panel")
    status, resources = api("GET", "/resources")
    if status == 200 and isinstance(resources, list):
        register_secrets(resources)
        for resource in resources:
            if isinstance(resource, dict):
                out(f"  - {fields(resource, ['name', 'type', 'status', 'fqdn'])}")
    else:
        out(f"  tidak terbaca: {explain(status, resources)}")

    section("Deploy terakhir")
    for deployment in list_deployments():
        message = str(deployment.get("commit_message") or "").splitlines()[0][:70] if deployment.get("commit_message") else ""
        out(f"  - {fields(deployment, DEPLOY_FIELDS)} {message}")
    status, running = api("GET", "/deployments")
    if status == 200 and isinstance(running, list):
        out(f"  Deploy yang sedang berjalan/antre di server: {len(running)}")

    describe_envs(load_envs(), databases)

    section("Aplikasi live (endpoint publik)")
    code, text = app_health()
    out(f"  /api/health -> HTTP {code} {text}")

    for database in databases:
        if is_mongo(database) and database.get("is_public") and database.get("public_port"):
            ok, note = mongo_hello(CFG["server_host"], int(database["public_port"]))
            section("Koneksi BARU ke MongoDB lewat port publik")
            out(f"  {'DITERIMA' if ok else 'GAGAL'}: {note}")


MONGO_ATTRS = ["connectionCount", "connectionId", "remote", "currentValue", "recommendedMinimum", "port", "pid",
               "dbPath", "architecture", "host", "version", "gitVersion", "durationMillis", "ns", "type",
               "planSummary", "maxConns", "numYields", "principalName", "authenticationDatabase", "mechanism",
               "code", "codeName", "signal", "exitCode"]
MONGO_TEXT_ATTRS = ["error", "errmsg", "reason", "message", "result", "namespace"]


PLAIN_LOG_LINE = re.compile(r"[A-Za-z0-9 ,.;:'()\-_/\[\]!?=+*#@]{1,160}")
_BRACES = re.compile(r"\{[^{}]*\}")


def render_mongo_line(line: str) -> tuple[str, dict | None]:
    """One mongod log line -> safe one-line summary. Commands and documents are never printed."""
    text = line.strip()
    start = text.find("{")
    record = None
    if start >= 0:
        try:
            record = json.loads(text[start:])
        except ValueError:
            record = None
    if not isinstance(record, dict) or "msg" not in record:
        # Not a structured mongod line (entrypoint text, or a fragment of a long line that Docker split).
        # A fragment can carry query documents, so only plainly harmless text is shown.
        if PLAIN_LOG_LINE.fullmatch(text):
            return text, None
        return f"(baris non-JSON, {len(text)} karakter, disembunyikan)", None
    attr = record.get("attr") if isinstance(record.get("attr"), dict) else {}
    stamp = record.get("t", {}).get("$date", "") if isinstance(record.get("t"), dict) else ""
    shown = [f"{key}={attr[key]}" for key in MONGO_ATTRS if key in attr and not isinstance(attr[key], (dict, list))]
    for key in MONGO_TEXT_ATTRS:
        value = attr.get(key)
        if isinstance(value, dict):
            value = value.get("errmsg") or value.get("codeName") or value.get("message")
        if isinstance(value, str) and value:
            shown.append(f"{key}={_BRACES.sub('{…}', value.split('dup key')[0])[:160]}")
    summary = f"{stamp} {record.get('s')} {record.get('c')} {record.get('id')} {record.get('msg')}"
    return (summary + (" | " + ", ".join(shown) if shown else ""))[:420], record


def act_db_logs(inputs: dict) -> None:
    database = find_database(inputs)
    require_secrets_loaded()
    tail = int_input(inputs, "lines", 200, 10, 400)
    fetch = max(1000, min(tail * 25, 10000))
    status, payload = api("GET", f"/databases/{database['uuid']}/logs?lines={fetch}", timeout=90)
    if status != 200:
        fail(f"Log database tidak terbaca: {explain(status, payload)}")
    raw = payload.get("logs") if isinstance(payload, dict) else payload
    lines = [line for line in str(raw or "").splitlines() if line.strip()]
    rendered, counts, last_count, refused, first_stamp, last_stamp = [], {}, None, 0, "", ""
    highlights, accepted, ended, refused_from, clients = [], {}, {}, {}, {}
    for line in lines:
        text, record = render_mongo_line(line)
        rendered.append(text)
        if not record:
            continue
        stamp = record.get("t", {}).get("$date", "") if isinstance(record.get("t"), dict) else ""
        first_stamp = first_stamp or stamp
        last_stamp = stamp or last_stamp
        key = (str(record.get("s")), str(record.get("c")), str(record.get("id")), str(record.get("msg"))[:80])
        counts[key] = counts.get(key, 0) + 1
        attr = record.get("attr") if isinstance(record.get("attr"), dict) else {}
        if isinstance(attr.get("connectionCount"), int):
            last_count = attr["connectionCount"]
        message = str(record.get("msg", "")).lower()
        host = str(attr.get("remote") or "").rsplit(":", 1)[0]
        if "too many open connections" in message:
            refused += 1
            refused_from[host] = refused_from.get(host, 0) + 1
        elif message == "connection accepted":
            accepted[host] = accepted.get(host, 0) + 1
        elif message == "connection ended":
            ended[host] = ended.get(host, 0) + 1
        elif message == "client metadata":
            doc = attr.get("doc") if isinstance(attr.get("doc"), dict) else {}
            driver = doc.get("driver") if isinstance(doc.get("driver"), dict) else {}
            application = doc.get("application") if isinstance(doc.get("application"), dict) else {}
            who = (host, str(driver.get("name") or "?")[:40], str(application.get("name") or "-")[:40])
            clients[who] = clients.get(who, 0) + 1
        if record.get("s") in ("E", "F", "W") or any(word in message for word in (
                "starting", "rlimit", "shutting down", "shutdown", "too many", "build info", "out of", "no space",
                "disk", "waiting for connections", "interrupted", "wiredtiger message")):
            highlights.append(text)
    section(f"Log MongoDB: ringkasan dari {len(lines)} baris terakhir")
    out(f"  Rentang waktu: {first_stamp} s.d. {last_stamp}")
    out(f"  connectionCount terakhir yang tercatat: {last_count}")
    out(f"  Baris 'too many open connections': {refused}")
    section("Siapa yang membuka koneksi (alamat asal: diterima / selesai / ditolak karena penuh)")
    hosts = sorted(set(accepted) | set(ended) | set(refused_from),
                   key=lambda h: -(accepted.get(h, 0) + refused_from.get(h, 0)))
    for host in hosts[:12]:
        out(f"  {host or '(tidak tercatat)'}: {accepted.get(host, 0)} / {ended.get(host, 0)} / {refused_from.get(host, 0)}",
            hide_ips=True)
    section("Jenis klien (alamat asal, driver, nama aplikasi)")
    for who, count in sorted(clients.items(), key=lambda item: -item[1])[:12]:
        out(f"  {count:>6}x  {who[0]} | {who[1]} | {who[2]}", hide_ips=True)
    section("Jenis pesan terbanyak")
    for key, count in sorted(counts.items(), key=lambda item: -item[1])[:18]:
        out(f"  {count:>6}x  {' '.join(key)}")
    section("Peringatan, error, dan pesan start/stop (maks. 40 terakhir)")
    for text in highlights[-40:]:
        out("  " + text, hide_ips=True)
    section(f"{tail} baris terakhir")
    for text in rendered[-tail:]:
        out("  " + text, hide_ips=True)


def act_app_logs(inputs: dict) -> None:
    require_secrets_loaded()
    count = int_input(inputs, "lines", 200, 10, 2000)
    status, payload = api("GET", f"/applications/{CFG['app']}/logs?lines={count}", timeout=90)
    if status != 200:
        fail(f"Log aplikasi tidak terbaca: {explain(status, payload)}")
    raw = payload.get("logs") if isinstance(payload, dict) else payload
    section(f"Log container aplikasi ({count} baris terakhir)")
    for line in str(raw or "").splitlines()[-count:]:
        out("  " + line[:500], hide_ips=True)


def deployment_log_lines(deployment: dict) -> list:
    raw = deployment.get("logs")
    if not raw:
        return []
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return str(raw).splitlines()
    lines = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or entry.get("hidden"):
            continue
        for line in str(entry.get("output") or "").splitlines():
            if line.strip():
                lines.append(f"{str(entry.get('timestamp') or '')[11:19]} {line[:400]}")
    return lines


def act_deploy_logs(inputs: dict) -> None:
    require_secrets_loaded()
    wanted = inputs.get("deployment_uuid", "").strip()
    if not wanted:
        history = list_deployments(take=3)
        if not history:
            fail("Tidak ada riwayat deploy yang terbaca.")
        wanted = str(history[0].get("deployment_uuid"))
    status, deployment = api("GET", f"/deployments/{wanted}", timeout=60)
    if status != 200 or not isinstance(deployment, dict):
        fail(f"Deploy {wanted} tidak terbaca: {explain(status, deployment)}")
    section("Deploy")
    out("  " + fields(deployment, DEPLOY_FIELDS))
    count = int_input(inputs, "lines", 200, 10, 1500)
    lines = deployment_log_lines(deployment)
    if not deployment.get("logs"):
        out("  Coolify tidak menyertakan log: token butuh izin read:sensitive dan pemiliknya harus admin tim.")
    section(f"Log deploy ({min(count, len(lines))} dari {len(lines)} baris)")
    for line in lines[-count:]:
        out("  " + line, hide_ips=True)


def act_db_restart(inputs: dict) -> None:
    database = find_database(inputs)
    uuid = database["uuid"]
    show_databases([database])
    forced = inputs.get("force", "").strip().lower() == "ya"
    if database.get("enable_ssl") and not forced:
        fail("enable_ssl aktif pada database. Restart bisa mengubah cara aplikasi harus tersambung; "
             "jalankan lagi dengan force=ya bila memang dikehendaki.")
    # Coolify's restart removes the container, pulls the image again and recreates it. A floating tag
    # could therefore bring a different MongoDB major version, which may refuse the existing data files.
    image = str(database.get("image") or "")
    tag = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else "latest"
    if not re.match(r"\d", tag) and not forced:
        fail(f"Image database memakai tag mengambang ({tag}). Restart menarik ulang image dan bisa mengganti versi "
             "MongoDB; jalankan lagi dengan force=ya bila memang dikehendaki.")
    port = int(database["public_port"]) if database.get("is_public") and database.get("public_port") else None
    section("Sebelum restart")
    if port:
        ok, note = mongo_hello(CFG["server_host"], port)
        out(f"  Koneksi baru lewat port publik: {'DITERIMA' if ok else 'GAGAL'} ({note})")
    code, text = app_health()
    out(f"  Aplikasi /api/health: HTTP {code} {text}")

    status, payload = api_post_or_get(f"/databases/{uuid}/restart")
    if status != 200:
        fail(f"Restart ditolak: {explain(status, payload)}")
    section("Restart diminta")
    out(f"  Jawaban Coolify: {reason(payload)}")

    started, streak, success, saw_dip, nudged = now(), 0, False, False, False
    while now() - started < 480:
        pause(10)
        elapsed = int(now() - started)
        status, current = api("GET", f"/databases/{uuid}")
        db_status = current.get("status") if status == 200 and isinstance(current, dict) else f"HTTP {status}"
        if isinstance(current, dict):
            register_secrets(current)
        code, text = app_health()
        if port:
            ok, note = mongo_hello(CFG["server_host"], port)
            good = ok
            out(f"  +{elapsed:>3}s database={db_status} | koneksi baru: {note} | aplikasi: HTTP {code}")
        else:
            running = str(db_status).startswith("running") and code == 200
            saw_dip = saw_dip or not running
            good = running and (saw_dip or elapsed >= 90)
            out(f"  +{elapsed:>3}s database={db_status} | aplikasi: HTTP {code}")
        streak = streak + 1 if good else 0
        if streak >= 2 and elapsed >= 20:
            success = True
            break
        if not nudged and elapsed >= 180 and not str(db_status).startswith("running"):
            # Coolify removes the container before a queued job starts it again; if that job failed
            # (for example the image pull), the database stays down until it is started explicitly.
            nudged = True
            status, payload = api_post_or_get(f"/databases/{uuid}/start")
            out(f"  Database belum hidup setelah 3 menit; perintah start dikirim: {explain(status, payload)}")
    section("Sesudah restart")
    status, current = api("GET", f"/databases/{uuid}")
    if status == 200 and isinstance(current, dict):
        register_secrets(current)
        out("  " + fields(current, ["status", "restart_count", "last_restart_at", "last_restart_type", "started_at"]))
    code, text = app_health()
    out(f"  Aplikasi /api/health: HTTP {code} {text}")
    if not success:
        fail("Database belum terbukti menerima koneksi baru dalam 8 menit. Periksa db-logs dan status.")
    out("  BERHASIL: database menerima koneksi baru.")


def act_db_visibility(inputs: dict, public: bool) -> None:
    database = find_database(inputs)
    uuid = database["uuid"]
    show_databases([database])
    if bool(database.get("is_public")) == public:
        out(f"  Sudah {'publik' if public else 'tertutup'}; tidak ada yang diubah.")
        return
    port = int(database["public_port"]) if database.get("public_port") else None
    if public and not port:
        fail("Database belum punya public_port; atur dulu di panel Coolify.")
    # Coolify starts the proxy immediately only when public_port is part of the request.
    reopen = {"is_public": True, "public_port": port}
    status, payload = api("PATCH", f"/databases/{uuid}", body=reopen if public else {"is_public": False})
    if status not in (200, 201):
        fail(f"Perubahan is_public ditolak: {explain(status, payload)}")
    pause(8)
    status, current = api("GET", f"/databases/{uuid}")
    if status == 200 and isinstance(current, dict):
        register_secrets(current)
        out(f"  Sekarang: is_public={current.get('is_public')}, status={current.get('status')}")
        if bool(current.get("is_public")) != public:
            fail("Coolify tidak menyimpan perubahan is_public.")
    if port:
        ok, note = mongo_hello(CFG["server_host"], port)
        out(f"  Port publik dari internet: {note}")
        if not public and ok:
            fail("Port publik masih menjawab dari internet.")
    code, text = app_health_retry()
    out(f"  Aplikasi /api/health: HTTP {code} {text}")
    if code != 200 and not public and inputs.get("force", "").strip().lower() == "ya":
        out("  Aplikasi kehilangan database setelah port ditutup (container lama memakai alamat publik).")
        out("  force=ya: port TETAP ditutup. Jalankan db-restart lalu deploy agar versi baru tersambung lewat jaringan internal.")
        return
    if code != 200 and not public:
        out("  Aplikasi kehilangan database setelah port ditutup: container yang berjalan masih memakai alamat publik.")
        status, payload = api("PATCH", f"/databases/{uuid}", body=reopen)
        pause(10)
        code, text = app_health_retry()
        out(f"  Port publik dibuka lagi (HTTP {status}); aplikasi: HTTP {code} {text}")
        fail("Penutupan port dibatalkan. Deploy dulu versi yang memakai MONGO_URL internal, baru tutup port "
             "(atau jalankan lagi dengan force=ya untuk menutupnya tanpa pembatalan).")
    if code != 200:
        fail("Aplikasi tidak sehat setelah perubahan.")


def running_deployment() -> str | None:
    for deployment in list_deployments(take=5):
        if deployment.get("status") in ("queued", "in_progress") and deployment.get("deployment_uuid"):
            return str(deployment["deployment_uuid"])
    return None


def act_deploy(inputs: dict) -> None:
    require_secrets_loaded()
    section("Deploy")
    code, text = app_health()
    out(f"  Sebelum: /api/health HTTP {code} {text}")
    uuid = running_deployment()
    if uuid:
        out(f"  Ada deploy yang sedang berjalan ({uuid}); menunggu yang itu, tidak membuat yang baru.")
    else:
        force = "true" if inputs.get("force", "").strip().lower() == "ya" else "false"
        status, payload = api_post_or_get(f"/deploy?uuid={CFG['app']}&force={force}")
        if status != 200 or not isinstance(payload, dict):
            fail(f"Deploy ditolak: {explain(status, payload)}")
        queued = [d for d in (payload.get("deployments") or []) if isinstance(d, dict) and d.get("deployment_uuid")]
        if not queued:
            fail(f"Coolify tidak mengembalikan nomor deploy: {reason(payload)}")
        uuid = str(queued[0]["deployment_uuid"])
        out(f"  Deploy dibuat: {uuid}")

    started, last_status, last_line, state, lines = now(), None, "", "unknown", []
    while now() - started < 2100:
        pause(15)
        elapsed = int(now() - started)
        status, deployment = api("GET", f"/deployments/{uuid}", timeout=60)
        if status != 200 or not isinstance(deployment, dict):
            out(f"  +{elapsed:>4}s status tidak terbaca: {explain(status, deployment)}")
            continue
        state = str(deployment.get("status"))
        lines = deployment_log_lines(deployment)
        newest = lines[-1] if lines else ""
        if state != last_status or newest != last_line:
            out(f"  +{elapsed:>4}s {state} | {newest[:200]}", hide_ips=True)
            last_status, last_line = state, newest
        if state not in ("queued", "in_progress"):
            break
    section(f"Hasil deploy: {state}")
    for line in lines[-70:]:
        out("  " + line, hide_ips=True)
    code, text = app_health_retry()
    section("Aplikasi live sesudah deploy")
    out(f"  /api/health -> HTTP {code} {text}")
    if state != "finished":
        fail(f"Deploy berakhir dengan status {state}.")
    if code != 200:
        fail("Deploy selesai tetapi aplikasi tidak menjawab /api/health.")
    out("  BERHASIL: versi baru berjalan.")


def act_env_set(inputs: dict) -> None:
    pairs = {}
    if inputs.get("envs_json", "").strip():
        try:
            loaded = json.loads(inputs["envs_json"])
        except ValueError:
            fail("envs_json bukan JSON yang sah.")
        if not isinstance(loaded, dict):
            fail("envs_json harus berupa objek {NAMA: nilai}.")
        pairs.update({str(k): str(v) for k, v in loaded.items()})
    if inputs.get("key", "").strip():
        pairs[inputs["key"].strip()] = inputs.get("value", "")
    if not pairs:
        fail("Isi key dan value, atau envs_json.")
    for key in list(pairs):
        if not pairs[key]:
            # An empty value means: take it from the repository secret of the same name.
            pairs[key] = os.environ.get(SECRET_ENV_PREFIX + key, "")
            if not pairs[key]:
                fail(f"Nilai {key[:40]} kosong dan secret GitHub bernama {key[:40]} belum diisi.")
    for key, value in pairs.items():
        if key not in NON_SECRET_ENV:
            mask(value, force=True)
    literal = inputs.get("literal", "").strip().lower() != "tidak"
    section("Mengisi variabel lingkungan")
    for key, value in pairs.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", key):
            fail(f"Nama variabel tidak sah: {key[:40]}")
        body = {"key": key, "value": value, "is_preview": False, "is_literal": literal}
        status, payload = api("PATCH", f"/applications/{CFG['app']}/envs", body=body)
        verb = "diperbarui"
        if status == 404:
            status, payload = api("POST", f"/applications/{CFG['app']}/envs", body=body)
            verb = "dibuat"
        if status not in (200, 201):
            fail(f"{key} gagal disimpan: {explain(status, payload)}")
        out(f"  {key}: {verb} (literal={'ya' if literal else 'tidak'})")
    out("  Nilai baru berlaku setelah deploy berikutnya.")


def act_app_set(inputs: dict) -> None:
    key = inputs.get("key", "").strip()
    if key not in APP_SETTABLE:
        fail(f"app-set hanya untuk: {', '.join(sorted(APP_SETTABLE))}")
    try:
        value = int(inputs.get("value", "").strip())
    except ValueError:
        fail("value harus bilangan bulat.")
    if not 1 <= value <= 600:
        fail("value harus antara 1 dan 600.")
    status, payload = api("PATCH", f"/applications/{CFG['app']}", body={key: value})
    if status not in (200, 201):
        fail(f"Pengaturan ditolak: {explain(status, payload)}")
    status, application = api("GET", f"/applications/{CFG['app']}")
    if status == 200 and isinstance(application, dict):
        register_secrets(application)
        out("  " + fields(application, ["health_check_enabled", "health_check_interval", "health_check_timeout",
                                        "health_check_retries", "health_check_start_period"]))


def act_ai_probe(inputs: dict) -> None:
    """List models of an OpenAI-compatible provider and send one tiny chat request. Needs no Coolify token."""
    key = os.environ.get(SECRET_ENV_PREFIX + "AI_API_KEY", "")
    if not key:
        fail("Secret GitHub AI_API_KEY belum diisi.")
    base = inputs.get("value", "").strip().rstrip("/")
    parts = urllib.parse.urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        fail("Isi value dengan alamat dasar penyedia, contoh https://nama-host/v1")
    section("Daftar model")
    status, payload = http("GET", base + "/models", token=key, timeout=40)
    models = []
    if status == 200 and isinstance(payload, dict) and isinstance(payload.get("data"), list):
        models = [str(m.get("id")) for m in payload["data"] if isinstance(m, dict) and m.get("id")]
        out(f"  {len(models)} model tersedia:")
        for name in models[:80]:
            out(f"    {name[:80]}")
    else:
        out(f"  GET /models gagal: {explain(status, payload)}")
    model = inputs.get("key", "").strip() or (models[0] if models else "")
    if not model:
        fail("Tidak ada model untuk diuji. Isi key dengan nama model.")
    section(f"Uji chat dengan model {model[:80]}")
    body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "Balas satu kata: siap"}]}
    status, payload = http("POST", base + "/chat/completions", body=body, token=key, timeout=90)
    answer = ""
    if status == 200 and isinstance(payload, dict) and isinstance(payload.get("choices"), list) and payload["choices"]:
        first = payload["choices"][0]
        message = first.get("message") if isinstance(first, dict) else None
        answer = str((message or {}).get("content") or "")
    if not answer:
        fail(f"Chat gagal: {explain(status, payload)}")
    out(f"  BERHASIL. Jawaban: {answer[:80]}")
    out(f"  Pakai: AI_BASE_URL={base} dan AI_MODEL={model[:80]}")


ACTIONS = {
    "ai-probe": act_ai_probe,
    "status": act_status,
    "db-logs": act_db_logs,
    "app-logs": act_app_logs,
    "deploy-logs": act_deploy_logs,
    "db-restart": act_db_restart,
    "db-private": lambda inputs: act_db_visibility(inputs, public=False),
    "db-public": lambda inputs: act_db_visibility(inputs, public=True),
    "deploy": act_deploy,
    "env-set": act_env_set,
    "app-set": act_app_set,
}


def main() -> None:
    inputs = load_inputs()
    token = (os.environ.get("COOLIFY_TOKEN_SECRET") or "").strip() or inputs.get("token", "").strip()
    if token:
        mask(token, force=True)
    for name, value in os.environ.items():
        if name.startswith(SECRET_ENV_PREFIX) and value:
            mask(value, force=True)
    action = inputs.get("action", "").strip() or "status"
    if action == "env-set":
        # Register the new values before anything else can be printed.
        if inputs.get("value") and inputs.get("key", "").strip() not in NON_SECRET_ENV:
            mask(inputs["value"], force=True)
        if inputs.get("envs_json", "").strip():
            mask(inputs["envs_json"], force=True)
            try:
                for key, value in json.loads(inputs["envs_json"]).items():
                    if str(key) not in NON_SECRET_ENV:
                        mask(str(value), force=True)
            except (ValueError, AttributeError):
                pass
    elif action.startswith("db-") and inputs.get("value", "").strip():
        inputs["db_uuid"] = inputs["value"].strip()
    elif action == "deploy-logs" and inputs.get("value", "").strip():
        inputs["deployment_uuid"] = inputs["value"].strip()
    for name in ("db_uuid", "deployment_uuid"):
        if inputs.get(name) and not UUID_LIKE.fullmatch(inputs[name]):
            fail(f"{name} tidak sah.")

    base = (os.environ.get("COOLIFY_URL_SECRET") or "").strip() or DEFAULT_URL
    base = base.rstrip("/")
    CFG.update({
        "base": base,
        "api": base + "/api/v1",
        "token": token,
        "app": (os.environ.get("COOLIFY_APP_UUID") or "").strip() or DEFAULT_APP_UUID,
        "app_url": ((os.environ.get("APP_URL") or "").strip() or DEFAULT_APP_URL).rstrip("/"),
        "server_host": urllib.parse.urlsplit(base).hostname or "",
    })
    if action not in ACTIONS:
        fail(f"Perintah tidak dikenal: {action[:40]}")
    if not UUID_LIKE.fullmatch(CFG["app"]):
        fail("UUID aplikasi tidak sah.")
    out(f"Perintah: {action}")
    probe_panel()
    if not token and action != "ai-probe":
        fail("Token API Coolify belum diberikan (secret COOLIFY_TOKEN atau input token).")
    if action in MUTATING and inputs.get("confirm", "").strip().lower() != "ya":
        fail(f"Perintah {action} mengubah server. Isi input confirm dengan: ya")
    ACTIONS[action](inputs)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - a raw traceback must not reach the public log unscrubbed
        out(traceback.format_exc())
        sys.stdout.write("::error::Skrip berhenti karena kesalahan tak terduga (lihat log di atas)\n")
        sys.exit(1)
