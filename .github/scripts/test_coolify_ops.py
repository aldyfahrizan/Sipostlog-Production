"""Tests for coolify_ops.py against a fake Coolify panel, a fake app and a fake mongod.

The property that matters most: nothing secret reaches the (public) workflow log.

Run: python -m pytest .github/scripts/test_coolify_ops.py -q
"""
import json
import os
import socket
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("coolify_ops.py")
TOKEN = "7|" + "Tk9" * 16
APP = "app1uuid0000000000000001"
DB = "db1uuid00000000000000001"
DB_PASSWORD = "Sup3rR00tPassw0rdXyZ"
MONGO_URL = f"mongodb://root:{DB_PASSWORD}@{DB}:27017/?directConnection=true"
ADMIN_HASH = "$2b$12$" + "abcdefghijklmnopqrstuv" + "wxyzABCDEFGHIJKLMNOPQRSTUVWXYZ01234"[:31]
ADMIN_EMAIL = "pemilik.rahasia@gmail.com"
AI_KEY = "sk-kelontong-" + "Zz9" * 10
WEBHOOK_SECRET = "whsec_" + "Q1w2" * 6
SENTINEL = "sentinel-" + "Ab12" * 8
SESSION_TOKEN = "sess_" + "Kk77" * 8
NEW_SECRET_VALUE = "nilai-baru-" + "Rr55" * 6
BACKUP = "bak1uuid0000000000000001"
RUN_OPTIONS = "--ulimit nofile=64000:64000 --env RAHASIA=" + "Hh33" * 6
SECRETS = [TOKEN, DB_PASSWORD, MONGO_URL, ADMIN_HASH, ADMIN_EMAIL, AI_KEY, WEBHOOK_SECRET, SENTINEL, SESSION_TOKEN,
           NEW_SECRET_VALUE, RUN_OPTIONS]


def op_msg_reply() -> bytes:
    body = b"\x10maxWireVersion\x00" + struct.pack("<i", 21) + b"\x00"
    document = struct.pack("<i", len(body) + 4) + body
    payload = struct.pack("<I", 0) + b"\x00" + document
    return struct.pack("<iiii", 16 + len(payload), 7, 1, 2013) + payload


class World:
    """Shared, mutable state of the fake server."""

    def __init__(self):
        self.mongo_accepts = False       # does a brand-new mongod connection get an answer?
        self.app_ok = True
        self.app_needs_public_port = False
        self.db_status = "running:healthy"
        self.is_public = True
        self.restart_polls = None        # polls left until mongod is back after a restart
        self.deploy_polls = 0
        self.deploy_result = "finished"
        self.running_deployment = False
        self.strip_sensitive = False     # token without read:sensitive: Coolify omits the values
        self.ai_requests = []
        self.ai_needs_completion_tokens = False
        self.envs = [
            {"key": "MONGO_URL", "value": MONGO_URL, "real_value": MONGO_URL, "is_literal": True, "is_runtime": True},
            {"key": "DB_NAME", "value": "sipostlog", "real_value": "sipostlog"},
            {"key": "ADMIN_USERNAME", "value": "admin", "real_value": "admin"},
            {"key": "ADMIN_PASSWORD_HASH", "value": ADMIN_HASH, "real_value": ADMIN_HASH, "is_literal": True},
            {"key": "ADMIN_EMAIL", "value": ADMIN_EMAIL, "real_value": ADMIN_EMAIL},
            {"key": "AI_BASE_URL", "value": "https://api.penyedia.example/v1", "real_value": "https://api.penyedia.example/v1"},
            {"key": "AI_API_KEY", "value": AI_KEY, "real_value": AI_KEY},
        ]
        self.requests = []
        self.mongo_port = 0
        self.backups = []                # scheduled backup configurations of the database
        self.executions = []
        self.backup_polls = 0            # polls left until the first backup run finishes
        self.backup_result = "success"
        self.run_options = None          # custom_docker_run_options of the database

    def database(self):
        return {
            "uuid": DB, "name": "mongodb-database", "database_type": "standalone-mongodb", "image": "mongo:7",
            "status": self.db_status, "is_public": self.is_public, "public_port": self.mongo_port,
            "public_port_timeout": 3600, "enable_ssl": False, "restart_count": 0,
            "internal_db_url": MONGO_URL,
            "external_db_url": f"mongodb://root:{DB_PASSWORD}@127.0.0.1:{self.mongo_port}/?directConnection=true",
            "mongo_initdb_root_password": DB_PASSWORD, "mongo_initdb_root_username": "root", "backup_configs": [],
            "custom_docker_run_options": self.run_options,
        }

    def application(self):
        return {"id": 3, "uuid": APP, "name": "sipostlog", "status": "running:healthy",
                "fqdn": "http://app.example.test", "git_branch": "main", "build_pack": "dockerfile",
                "health_check_enabled": True, "health_check_retries": 10, "health_check_interval": 5,
                "manual_webhook_secret_github": WEBHOOK_SECRET, "custom_labels": "bGFiZWxz" * 8}


MONGO_LOG = "\n".join([
    json.dumps({"t": {"$date": "2026-11-11T05:17:01.000+00:00"}, "s": "I", "c": "NETWORK", "id": 22942, "ctx": "listener",
                "msg": "Connection refused because there are too many open connections",
                "attr": {"remote": "10.0.1.9:51234", "connectionCount": 819}}),
    json.dumps({"t": {"$date": "2026-11-11T05:17:02.000+00:00"}, "s": "I", "c": "COMMAND", "id": 51803, "ctx": "conn7",
                "msg": "Slow query", "attr": {"type": "command", "ns": "sipostlog.user_sessions",
                                              "command": {"find": "user_sessions", "filter": {"session_token": SESSION_TOKEN}},
                                              "planSummary": "IXSCAN { session_token: 1 }", "durationMillis": 120}}),
    json.dumps({"t": {"$date": "2026-11-11T05:17:03.000+00:00"}, "s": "E", "c": "STORAGE", "id": 1, "ctx": "conn8",
                "msg": "Write failed", "attr": {"error": "E11000 duplicate key error dup key: { session_token: \"" + SESSION_TOKEN + "\" }"}}),
    json.dumps({"t": {"$date": "2026-11-11T05:17:04.000+00:00"}, "s": "I", "c": "NETWORK", "id": 22943, "ctx": "listener",
                "msg": "Connection accepted", "attr": {"remote": "10.0.1.9:51300", "connectionId": 9, "connectionCount": 819}}),
    json.dumps({"t": {"$date": "2026-11-11T05:17:04.100+00:00"}, "s": "I", "c": "NETWORK", "id": 51800, "ctx": "conn9",
                "msg": "client metadata", "attr": {"remote": "10.0.1.9:51300", "client": "conn9",
                                                   "doc": {"driver": {"name": "PyMongo|Motor", "version": "4.6.3"},
                                                           "application": {"name": "sipostlog"}}}}),
    json.dumps({"t": {"$date": "2026-11-11T05:17:05.000+00:00"}, "s": "I", "c": "NETWORK", "id": 22943, "ctx": "listener",
                "msg": "Connection accepted", "attr": {"remote": "203.0.113.77:40000", "connectionId": 10, "connectionCount": 819}}),
    "::stop-commands::pwned",
    '{"session_token": "' + SESSION_TOKEN + '", "fragment of a long line',
    "MongoDB init process complete; ready for start up.",
])
APP_LOG = "\n".join([
    'INFO:     36.71.1.2:5123 - "GET /api/health HTTP/1.1" 200 OK',
    f"pymongo connecting to {MONGO_URL}",
    f"ADMIN_PASSWORD_HASH={ADMIN_HASH}",
    "  ::stop-commands::pwned",
    "##[group]fake",
    "line with carriage return\r::stop-commands::x",
])
DEPLOY_LOG = json.dumps([
    {"command": f"docker build --build-arg MONGO_URL={MONGO_URL}", "output": f"secret arg {AI_KEY}", "hidden": True,
     "timestamp": "2026-11-12T03:30:00.000000Z"},
    {"output": "Building docker image started.", "hidden": False, "timestamp": "2026-11-12T03:30:01.000000Z"},
    {"output": f"env dump MONGO_URL={MONGO_URL}", "hidden": False, "timestamp": "2026-11-12T03:31:01.000000Z"},
    {"output": "Rolling update completed.", "hidden": False, "timestamp": "2026-11-12T03:36:01.000000Z"},
])


def make_handler(world: World):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, payload, text=False):
            raw = payload.encode() if text else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain" if text else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}") if length else {}

        def handle_any(self, method):
            path = self.path
            body = self.body() if method in ("POST", "PATCH") else {}
            world.requests.append((method, path, body))
            if path == "/app/api/health":
                healthy = world.app_ok and (world.is_public or not world.app_needs_public_port)
                if healthy:
                    return self.send(200, {"status": "ok", "items": 47, "ai": "custom", "admin_password_config": "ok"})
                return self.send(500, {"detail": "db"})
            if path in ("/api/health", "/api/v1/health"):
                return self.send(200, "OK", text=True)
            if path == "/login":
                return self.send(200, "<html>login</html>", text=True)
            if path.startswith("/ai/v1/"):
                if self.headers.get("Authorization") != f"Bearer {AI_KEY}":
                    return self.send(401, {"error": {"message": "Invalid API key"}})
                if path == "/ai/v1/models":
                    return self.send(200, {"data": [{"id": "text-embedding-kecil"}, {"id": "model-teks"},
                                                    {"id": "model-gambar"}]})
                if path == "/ai/v1/chat/completions":
                    world.ai_requests.append(body)
                    if "max_tokens" in body and world.ai_needs_completion_tokens:
                        return self.send(400, {"error": {"message": "Unsupported parameter: max_tokens"}})
                    content = body["messages"][0]["content"]
                    if isinstance(content, list):
                        return self.send(200, {"choices": [{"message": {"content": [{"type": "text", "text": "Merah."}]}}]})
                    return self.send(200, {"choices": [{"message": {"content": "siap"}}], "model": body.get("model")})
            if path.startswith("/ai/"):
                if path == "/ai/models":
                    return self.send(200, "<html>halaman depan</html>", text=True)
                if path == "/ai/redirect/models":
                    self.send_response(302)
                    self.send_header("Location", world.base + "/ai/v1/models")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
            if not path.startswith("/api/v1/"):
                return self.send(404, {"message": "Not found."})
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self.send(401, {"message": "Unauthenticated."})
            route = path[len("/api/v1"):].split("?")[0]
            if route == "/version":
                return self.send(200, "4.7.5", text=True)
            if route == "/servers":
                return self.send(200, [{"uuid": "srv1uuid", "name": "localhost", "ip": "host.docker.internal",
                                        "high_disk_usage_notification_sent": False, "unreachable_count": 0,
                                        "settings": {"is_reachable": True, "is_usable": True, "sentinel_token": SENTINEL}}])
            if route == "/databases":
                return self.send(200, [world.database()])
            if route == "/resources":
                return self.send(200, [{**world.application(), "type": "application"},
                                       {**world.database(), "type": "standalone-mongodb"},
                                       {"uuid": "ai1", "name": "gerbang-ai", "type": "application",
                                        "status": "running:healthy", "fqdn": "https://ai.example.test"}])
            if route == f"/databases/{DB}" and method == "GET":
                if world.restart_polls is not None:
                    world.restart_polls -= 1
                    if world.restart_polls <= 0:
                        world.mongo_accepts, world.db_status, world.restart_polls = True, "running:healthy", None
                return self.send(200, world.database())
            if route == f"/databases/{DB}" and method == "PATCH":
                if "custom_docker_run_options" in body:
                    return self.send(422, {"message": "Validation failed.",
                                           "errors": {"custom_docker_run_options": ["This field is not allowed."]}})
                world.is_public = bool(body.get("is_public"))
                return self.send(200, {"message": "Database updated."})
            if route == f"/databases/{DB}/restart":
                if method == "GET":
                    return self.send(405, {"message": "Method not allowed."})
                world.db_status, world.restart_polls = "restarting", 3
                return self.send(200, {"message": "Database restarting request queued."})
            if route == f"/databases/{DB}/backups" and method == "GET":
                return self.send(200, world.backups)
            if route == f"/databases/{DB}/backups" and method == "POST":
                world.backups.append({**body, "uuid": BACKUP, "s3_storage_uuid": None,
                                      "database": {"internal_db_url": MONGO_URL}})
                world.executions = [{"uuid": "exec0002", "status": "running", "size": 0, "filename": None,
                                     "created_at": "2026-11-12T18:00:00Z", "message": None}]
                world.backup_polls = 3
                return self.send(201, {"uuid": BACKUP, "message": "Backup configuration created successfully."})
            if route == f"/databases/{DB}/backups/{BACKUP}/executions":
                if world.backup_polls:
                    world.backup_polls -= 1
                    if not world.backup_polls:
                        world.executions[0].update({
                            "status": world.backup_result, "size": 123456,
                            "filename": "/data/coolify/backups/mongo-dump-1.tar.gz",
                            "message": f"mongodump --uri={MONGO_URL} selesai"})
                return self.send(200, {"executions": world.executions})
            if route == f"/databases/{DB}/logs":
                return self.send(200, {"logs": MONGO_LOG})
            if route == f"/applications/{APP}" and method == "GET":
                return self.send(200, world.application())
            if route == f"/applications/{APP}" and method == "PATCH":
                return self.send(200, {"uuid": APP})
            if route == f"/applications/{APP}/logs":
                return self.send(200, {"logs": APP_LOG})
            if route == f"/applications/{APP}/envs" and method == "GET":
                if world.strip_sensitive:
                    return self.send(200, [{"key": env["key"], "is_literal": env.get("is_literal")} for env in world.envs])
                return self.send(200, world.envs)
            if route == f"/applications/{APP}/envs" and method == "PATCH":
                for env in world.envs:
                    if env["key"] == body.get("key"):
                        env["value"] = env["real_value"] = body.get("value")
                        return self.send(201, env)
                return self.send(404, {"message": "Environment variable not found."})
            if route == f"/applications/{APP}/envs" and method == "POST":
                world.envs.append({"key": body.get("key"), "value": body.get("value"), "real_value": body.get("value")})
                return self.send(201, {"uuid": "env-new"})
            if route == f"/deployments/applications/{APP}":
                history = [{"deployment_uuid": "dep0old", "status": "failed", "commit": "abc1234", "is_webhook": True,
                            "commit_message": "fix: something\n\nbody", "created_at": "2026-11-12T03:27:00Z"}]
                if world.running_deployment:
                    history.insert(0, {"deployment_uuid": "deprunning1", "status": "in_progress", "commit": "def5678"})
                return self.send(200, {"count": len(history), "deployments": history})
            if route == "/deployments":
                running = [{"deployment_uuid": "deprunning1", "status": "in_progress", "application_name": "sipostlog",
                            "application_id": "3"}] if world.running_deployment else []
                return self.send(200, running)
            if route == "/deploy":
                return self.send(200, {"deployments": [{"message": "queued", "resource_uuid": APP,
                                                        "deployment_uuid": "depnew0001"}]})
            if route.startswith("/deployments/"):
                world.deploy_polls += 1
                status = "in_progress" if world.deploy_polls < 3 else world.deploy_result
                return self.send(200, {"deployment_uuid": route.rsplit("/", 1)[-1], "status": status,
                                       "commit": "abc1234", "logs": DEPLOY_LOG})
            return self.send(404, {"message": "Not found."})

        def do_GET(self):
            self.handle_any("GET")

        def do_POST(self):
            self.handle_any("POST")

        def do_PATCH(self):
            self.handle_any("PATCH")

    return Handler


def serve_mongo(world: World, server: socket.socket):
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            if not (world.mongo_accepts and world.is_public):
                continue                         # accept, then close at once: the production symptom
            try:
                conn.settimeout(2)
                conn.recv(4096)
                conn.sendall(op_msg_reply())
            except OSError:
                pass


@pytest.fixture()
def world():
    state = World()
    mongo = socket.socket()
    mongo.bind(("127.0.0.1", 0))
    mongo.listen(16)
    state.mongo_port = mongo.getsockname()[1]
    threading.Thread(target=serve_mongo, args=(state, mongo), daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield state
    httpd.shutdown()
    mongo.close()


def run(world: World, tmp_path, inputs: dict, token_in_secret: bool = False, extra_env: dict | None = None):
    event = tmp_path / "event.json"
    payload = dict(inputs)
    if not token_in_secret:
        payload.setdefault("token", TOKEN)
    event.write_text(json.dumps({"inputs": payload}))
    env = {
        "PATH": os.environ.get("PATH", ""), "GITHUB_EVENT_PATH": str(event), "COOLIFY_URL_SECRET": world.base,
        "COOLIFY_APP_UUID": APP, "APP_URL": world.base + "/app", "COOLIFY_OPS_TIME_SCALE": "0.002",
    }
    if token_in_secret:
        env["COOLIFY_TOKEN_SECRET"] = TOKEN
    env.update(extra_env or {})
    result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120)
    return result


def visible(result) -> str:
    """What a reader of the workflow log sees: the runner swallows ::add-mask:: lines."""
    lines = [line for line in result.stdout.splitlines() if not line.startswith("::add-mask::")]
    return "\n".join(lines) + "\n" + result.stderr


def assert_no_secret(result, world: World):
    text = visible(result)
    for secret in SECRETS + [str(world.mongo_port)]:
        assert secret not in text, f"bocor: {secret[:12]}…"
    # No line may be readable as a workflow command except the ones the script means to send.
    for line in text.splitlines():
        stripped = line.lstrip()
        assert not stripped.startswith("::") or stripped.startswith("::error::"), line
        assert not stripped.startswith("##["), line
        assert "\r" not in line


def test_status_prints_safe_facts_only(world, tmp_path):
    result = run(world, tmp_path, {"action": "status"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert "Versi Coolify: 4.7.5" in text
    assert "name=mongodb-database" in text and "is_public=True" in text and "public_port=ada" in text
    assert "MONGO_URL: skema=mongodb, host=['internal (nama container database)'], port=['27017']" in text
    assert "ADMIN_PASSWORD_HASH: format bcrypt valid" in text
    assert "AI_BASE_URL: https://api.penyedia.example/v1" in text and "AI_API_KEY: terisi" in text
    assert "items=47" in text and "ai=custom" in text
    assert "Koneksi BARU ke MongoDB lewat port publik" in text and "GAGAL: DITUTUP server" in text
    assert "- MONGO_URL [literal, runtime]" in text


def test_token_from_secret_and_missing_token(world, tmp_path):
    assert run(world, tmp_path, {"action": "status"}, token_in_secret=True).returncode == 0
    event_only = run(world, tmp_path, {"action": "status", "token": ""})
    assert event_only.returncode == 1 and "Token API Coolify belum diberikan" in visible(event_only)
    assert "GET /api/health -> HTTP 200" in visible(event_only)


def test_wrong_token_is_reported(world, tmp_path):
    result = run(world, tmp_path, {"action": "status", "token": "9|" + "x" * 40})
    assert result.returncode == 1 and "HTTP 401" in visible(result)
    assert "9|" + "x" * 40 not in visible(result)


def test_mutating_actions_need_confirmation(world, tmp_path):
    result = run(world, tmp_path, {"action": "db-restart"})
    assert result.returncode == 1 and "confirm" in visible(result)
    assert not any(path.endswith("/restart") for _, path, _ in world.requests)


def test_db_logs_never_show_documents(world, tmp_path):
    result = run(world, tmp_path, {"action": "db-logs", "lines": "50"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert "Baris 'too many open connections': 1" in text
    assert "connectionCount terakhir yang tercatat: 819" in text
    assert "Slow query" in text and "ns=sipostlog.user_sessions" in text
    assert "MongoDB init process complete" in text
    assert "disembunyikan" in text


def test_app_logs_are_scrubbed(world, tmp_path):
    result = run(world, tmp_path, {"action": "app-logs"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert "36.71.1.2" not in text and "<ip>" in text
    assert "mongodb://***@" in text or "***" in text


def test_deploy_logs_skip_hidden_entries(world, tmp_path):
    result = run(world, tmp_path, {"action": "deploy-logs"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert "Building docker image started." in text and "docker build" not in text


def test_db_restart_waits_for_new_connections(world, tmp_path):
    result = run(world, tmp_path, {"action": "db-restart", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert ("POST", f"/api/v1/databases/{DB}/restart", {}) in world.requests
    assert "Koneksi baru lewat port publik: GAGAL" in text
    assert "BERHASIL: database menerima koneksi baru." in text


def test_db_private_closes_port(world, tmp_path):
    world.mongo_accepts = True
    result = run(world, tmp_path, {"action": "db-private", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert world.is_public is False
    assert ("PATCH", f"/api/v1/databases/{DB}", {"is_public": False}) in world.requests


def test_db_private_rolls_back_when_app_depends_on_public_port(world, tmp_path):
    world.mongo_accepts, world.app_needs_public_port = True, True
    result = run(world, tmp_path, {"action": "db-private", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 1, text
    assert world.is_public is True
    assert ("PATCH", f"/api/v1/databases/{DB}", {"is_public": True, "public_port": world.mongo_port}) in world.requests
    assert "Penutupan port dibatalkan" in text
    assert_no_secret(result, world)


def test_deploy_polls_until_finished(world, tmp_path):
    result = run(world, tmp_path, {"action": "deploy", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert "Deploy dibuat: depnew0001" in text and "BERHASIL: versi baru berjalan." in text


def test_deploy_attaches_to_running_deployment(world, tmp_path):
    world.running_deployment = True
    result = run(world, tmp_path, {"action": "deploy", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 0, text
    assert "deprunning1" in text
    assert not any(path.startswith("/api/v1/deploy?") for _, path, _ in world.requests)


def test_failed_deploy_exits_non_zero(world, tmp_path):
    world.deploy_result = "failed"
    result = run(world, tmp_path, {"action": "deploy", "confirm": "ya"})
    assert result.returncode == 1 and "status failed" in visible(result)
    assert_no_secret(result, world)


def test_env_set_updates_or_creates_without_printing_values(world, tmp_path):
    result = run(world, tmp_path, {"action": "env-set", "confirm": "ya",
                                   "envs_json": json.dumps({"AI_API_KEY": NEW_SECRET_VALUE, "AI_MODEL": "model-x"})})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    values = {env["key"]: env["value"] for env in world.envs}
    assert values["AI_API_KEY"] == NEW_SECRET_VALUE and values["AI_MODEL"] == "model-x"
    assert "AI_API_KEY: diperbarui" in text and "AI_MODEL: dibuat" in text


def test_app_set_only_allows_healthcheck_numbers(world, tmp_path):
    bad = run(world, tmp_path, {"action": "app-set", "confirm": "ya", "key": "dockerfile", "value": "5"})
    assert bad.returncode == 1
    good = run(world, tmp_path, {"action": "app-set", "confirm": "ya", "key": "health_check_retries", "value": "40"})
    assert good.returncode == 0, visible(good)
    assert ("PATCH", f"/api/v1/applications/{APP}", {"health_check_retries": 40}) in world.requests


def test_status_lists_every_resource(world, tmp_path):
    text = visible(run(world, tmp_path, {"action": "status"}))
    assert "name=gerbang-ai, type=application, status=running:healthy, fqdn=https://ai.example.test" in text


def test_db_logs_show_who_holds_connections(world, tmp_path):
    result = run(world, tmp_path, {"action": "db-logs", "lines": "50"})
    text = visible(result)
    assert_no_secret(result, world)
    assert "10.0.1.9: 1 / 0 / 1" in text                      # private address shown: accepted / ended / refused
    assert "203.0.113.77" not in text and "<ip>: 1 / 0 / 0" in text   # public address hidden
    assert "1x  10.0.1.9 | PyMongo|Motor | sipostlog" in text


def test_db_private_can_be_forced_without_rollback(world, tmp_path):
    world.mongo_accepts, world.app_needs_public_port = True, True
    result = run(world, tmp_path, {"action": "db-private", "confirm": "ya", "force": "ya"})
    text = visible(result)
    assert result.returncode == 0, text
    assert world.is_public is False and "port TETAP ditutup" in text


def test_env_set_takes_empty_value_from_repository_secret(world, tmp_path):
    result = run(world, tmp_path, {"action": "env-set", "confirm": "ya", "key": "AI_API_KEY", "value": ""},
                 extra_env={"SECRET_ENV_AI_API_KEY": NEW_SECRET_VALUE})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert {env["key"]: env["value"] for env in world.envs}["AI_API_KEY"] == NEW_SECRET_VALUE
    missing = run(world, tmp_path, {"action": "env-set", "confirm": "ya", "key": "OPENROUTER_API_KEY", "value": ""})
    assert missing.returncode == 1 and "secret GitHub" in visible(missing)


def test_ai_probe_lists_models_and_chats_without_coolify_token(world, tmp_path):
    result = run(world, tmp_path, {"action": "ai-probe", "token": "", "value": world.base + "/ai/v1"},
                 extra_env={"SECRET_ENV_AI_API_KEY": AI_KEY})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert "3 model tersedia" in text and "model-gambar" in text and "Jawaban: siap" in text
    assert "Uji teks: model-teks" in text                      # the embedding model is skipped
    assert "BISA membaca gambar" in text and "AI_VISION_MODEL=model-teks" in text
    no_key = run(world, tmp_path, {"action": "ai-probe", "token": "", "value": world.base + "/ai/v1"})
    assert no_key.returncode == 1 and "AI_API_KEY belum diisi" in visible(no_key)
    bad_url = run(world, tmp_path, {"action": "ai-probe", "token": "", "value": "ftp://x"},
                  extra_env={"SECRET_ENV_AI_API_KEY": AI_KEY})
    assert bad_url.returncode == 1


def test_logs_are_refused_when_token_cannot_see_secret_values(world, tmp_path):
    world.strip_sensitive = True
    for action in ("app-logs", "db-logs", "deploy-logs"):
        result = run(world, tmp_path, {"action": action})
        text = visible(result)
        assert result.returncode == 1, text
        assert "read:sensitive" in text
        assert_no_secret(result, world)
    status = run(world, tmp_path, {"action": "status"})
    assert status.returncode == 0 and "MONGO_URL: nilai tidak terlihat" in visible(status)


def test_post_error_is_not_hidden_by_get_fallback(world, tmp_path):
    result = run(world, tmp_path, {"action": "db-restart", "confirm": "ya", "value": "db-does-not-exist"})
    assert result.returncode == 1 and "Butuh tepat satu database" in visible(result)


def test_ai_probe_finds_the_v1_base_and_adapts_to_completion_tokens(world, tmp_path):
    world.ai_needs_completion_tokens = True
    result = run(world, tmp_path, {"action": "ai-probe", "token": "", "value": world.base + "/ai"},
                 extra_env={"SECRET_ENV_AI_API_KEY": AI_KEY})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert f"AI_BASE_URL={world.base}/ai/v1" in text
    assert any("max_completion_tokens" in body for body in world.ai_requests)


def test_authorization_header_never_follows_a_redirect(world, tmp_path):
    result = run(world, tmp_path, {"action": "ai-probe", "token": "", "value": world.base + "/ai/redirect"},
                 extra_env={"SECRET_ENV_AI_API_KEY": AI_KEY})
    text = visible(result)
    assert "HTTP 302" in text and result.returncode == 1
    assert not world.ai_requests                                # nothing was sent on to the redirect target


def test_ai_probe_refuses_plain_http_to_other_hosts(world, tmp_path):
    result = run(world, tmp_path, {"action": "ai-probe", "token": "", "value": "http://contoh.example/v1"},
                 extra_env={"SECRET_ENV_AI_API_KEY": AI_KEY})
    assert result.returncode == 1 and "https" in visible(result)


def existing_backup():
    return {"uuid": BACKUP, "frequency": "0 18 * * *", "enabled": True, "save_s3": False,
            "database_backup_retention_amount_locally": 7, "database_backup_retention_max_storage_locally": 1,
            "s3": {"secret": WEBHOOK_SECRET}, "database": {"internal_db_url": MONGO_URL}}


def test_db_backup_status_lists_configs_and_executions(world, tmp_path):
    world.backups = [existing_backup()]
    world.executions = [{"uuid": "exec0001", "status": "success", "size": 4096, "created_at": "2026-11-11T18:00:01Z",
                         "filename": "/data/coolify/backups/mongo-dump-0.tar.gz",
                         "message": f"mongodump --uri={MONGO_URL} selesai"}]
    result = run(world, tmp_path, {"action": "db-backup-status"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert f"uuid={BACKUP}" in text and "frequency=0 18 * * *" in text and "enabled=True" in text
    assert "database_backup_retention_amount_locally=7" in text
    assert "status=success" in text and "size=4096" in text and "mongo-dump-0.tar.gz" in text
    assert "mongodump" not in text and "/data/coolify" not in text
    assert all(method == "GET" for method, path, _ in world.requests)


def test_db_backup_status_without_config(world, tmp_path):
    result = run(world, tmp_path, {"action": "db-backup-status"})
    assert result.returncode == 0, visible(result)
    assert "Belum ada jadwal backup" in visible(result)


def test_db_backup_single_config_object_counts_as_existing(world, tmp_path):
    world.backups = existing_backup()
    result = run(world, tmp_path, {"action": "db-backup-enable", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert f"uuid={BACKUP}" in text and "tidak ada yang diubah" in text
    assert not [1 for method, path, _ in world.requests if method != "GET"]


@pytest.mark.parametrize("answer", [{"message": "ok"}, {"data": {"uuid": "x"}}, "teks", None])
def test_db_backup_unrecognised_list_answer_changes_nothing(world, tmp_path, answer):
    world.backups = answer
    result = run(world, tmp_path, {"action": "db-backup-enable", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 1, text
    assert "Jawaban daftar jadwal backup tidak dikenali; tidak ada yang diubah." in text
    assert "Belum ada jadwal backup" not in text
    assert not [1 for method, path, _ in world.requests if method != "GET"]


def test_db_backup_wrapped_empty_list_means_no_schedule(world, tmp_path):
    world.backups = {"data": []}
    result = run(world, tmp_path, {"action": "db-backup-status"})
    assert result.returncode == 0, visible(result)
    assert "Belum ada jadwal backup" in visible(result)


def test_db_backup_enable_creates_schedule_and_reports_first_run(world, tmp_path):
    assert run(world, tmp_path, {"action": "db-backup-enable"}).returncode == 1
    assert not world.backups
    result = run(world, tmp_path, {"action": "db-backup-enable", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    posts = [body for method, path, body in world.requests if method == "POST" and path.endswith("/backups")]
    assert posts == [{"frequency": "0 18 * * *", "enabled": True, "save_s3": False, "backup_now": True,
                      "database_backup_retention_amount_locally": 7,
                      "database_backup_retention_max_storage_locally": 1}]
    assert "status=success" in text and "size=123456" in text and "BERHASIL" in text
    assert "mongodump" not in text


def test_db_backup_enable_changes_nothing_when_schedule_exists(world, tmp_path):
    world.backups = [existing_backup()]
    world.executions = [{"uuid": "exec0001", "status": "success", "size": 4096, "created_at": "2026-11-11T18:00:01Z",
                         "filename": "/data/coolify/backups/mongo-dump-0.tar.gz", "message": None}]
    result = run(world, tmp_path, {"action": "db-backup-enable", "confirm": "ya"})
    text = visible(result)
    assert result.returncode == 0, text
    assert_no_secret(result, world)
    assert "tidak ada yang diubah" in text and "status=success" in text
    assert not [1 for method, path, _ in world.requests if method != "GET"]


def test_db_backup_enable_reports_failed_first_run(world, tmp_path):
    world.backup_result = "failed"
    result = run(world, tmp_path, {"action": "db-backup-enable", "confirm": "ya"})
    assert result.returncode == 1
    assert_no_secret(result, world)
    assert "status=failed" in visible(result)


def test_db_init_explains_panel_step_and_never_patches(world, tmp_path):
    world.run_options = RUN_OPTIONS
    result = run(world, tmp_path, {"action": "db-init"})
    text = visible(result)
    assert result.returncode == 1
    assert_no_secret(result, world)
    assert "panel" in text and "Custom Docker Options" in text and "db-restart" in text
    assert not [1 for method, path, _ in world.requests if method != "GET"]


def test_db_init_passes_once_the_option_is_set(world, tmp_path):
    world.run_options = RUN_OPTIONS + " --init"
    result = run(world, tmp_path, {"action": "db-init"})
    assert result.returncode == 0, visible(result)
    assert_no_secret(result, world)
    assert "sudah" in visible(result)
