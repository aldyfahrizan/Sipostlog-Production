"""Login throttling per username + client IP, and protection of the primary admin.

Run: cd backend && python -m pytest tests/test_login_guard.py -q   (needs mongomock-motor)
"""
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import pytest

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "sipostlog_test")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "$2b$12$" + "x" * 53)
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@sipostlog.local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

import admin_login  # noqa: E402
import server  # noqa: E402

PASSWORD = "Lima55555@"
GOOD = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
TOKEN = "token-admin-kedua"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _user(user_id, username, email, role="admin"):
    return {"user_id": user_id, "email": email, "username": username, "name": username, "picture": "",
            "auth_method": "password", "role": role, "active": True, "password_hash": GOOD,
            "created_at": datetime.now(timezone.utc), "last_login": None}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "kepala")
    monkeypatch.setenv("LOCAL_ADMIN_EMAIL", "kepala@sipostlog.local")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", GOOD)
    db = AsyncMongoMockClient()["login_guard_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True
    ip = {"value": "203.0.113.1"}
    monkeypatch.setattr(admin_login, "client_ip", lambda request: ip["value"], raising=False)
    with TestClient(server.app) as c:
        async def seed():
            now = datetime.now(timezone.utc)
            await db.users.insert_many([
                _user("u_primary", "kepala", "kepala@sipostlog.local"),
                _user("u_second", "admin2", "admin2@sipostlog.local"),
                _user("u_staff", "petugas1", "petugas1@sipostlog.local", role="petugas"),
            ])
            await db.user_sessions.insert_one({
                "user_id": "u_second", "session_token": hashlib.sha256(TOKEN.encode()).hexdigest(),
                "auth_method": "password", "created_at": now, "expires_at": now + timedelta(days=1)})
        c.portal.call(seed)
        c.db, c.ip = db, ip
        yield c


def login(c, password, ip, username="kepala"):
    c.ip["value"] = ip
    return c.post("/api/auth/login", json={"username": username, "password": password})


def test_locked_ip_does_not_lock_out_correct_password_from_another_ip(client):
    assert [login(client, "salah", "203.0.113.1").status_code for _ in range(5)] == [401] * 5
    locked = login(client, PASSWORD, "203.0.113.1")
    assert locked.status_code == 429
    assert "Terlalu banyak" in locked.json()["detail"]
    assert login(client, PASSWORD, "198.51.100.7").status_code == 200


def test_locked_ip_cannot_use_up_the_username_ceiling(client):
    for _ in range(40):
        login(client, "salah", "203.0.113.1")
    assert login(client, PASSWORD, "198.51.100.7").status_code == 200


def test_username_ceiling_limits_distributed_guessing(client):
    assert admin_login.USERNAME_ATTEMPT_LIMIT == 10
    statuses = [login(client, "salah", f"198.51.100.{n}").status_code for n in range(10)]
    assert statuses == [401] * 10
    blocked = login(client, PASSWORD, "192.0.2.200")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "900"
    # Another account is not affected.
    assert login(client, PASSWORD, "192.0.2.200", username="admin2").status_code == 200


def test_username_lock_does_not_use_up_the_owner_address_allowance(client):
    identifier = hashlib.sha256(b"username:kepala").hexdigest()
    client.portal.call(client.db.login_attempts.insert_one, {
        "identifier": identifier, "count": admin_login.USERNAME_ATTEMPT_LIMIT + 1,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15)})
    assert [login(client, PASSWORD, "203.0.113.9").status_code for _ in range(6)] == [429] * 6
    client.portal.call(client.db.login_attempts.delete_one, {"identifier": identifier})
    assert login(client, PASSWORD, "203.0.113.9").status_code == 200


def test_primary_admin_cannot_be_demoted_or_deactivated(client):
    for body in ({"role": "petugas"}, {"role": "pending"}, {"active": False}, {"role": "admin", "active": False}):
        r = client.patch("/api/users/u_primary", json=body, headers=AUTH)
        assert r.status_code == 409, body
        assert "Admin utama" in r.json()["detail"]
    stored = client.portal.call(client.db.users.find_one, {"user_id": "u_primary"})
    assert stored["role"] == "admin" and stored["active"] is True


def test_primary_admin_matched_by_email_is_protected(client):
    client.portal.call(client.db.users.update_one, {"user_id": "u_primary"}, {"$set": {"username": "lama"}})
    assert client.patch("/api/users/u_primary", json={"active": False}, headers=AUTH).status_code == 409


def test_other_users_can_still_be_changed(client):
    r = client.patch("/api/users/u_staff", json={"role": "opname", "active": False}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["role"] == "opname" and r.json()["active"] is False
    assert client.patch("/api/users/u_primary", json={"role": "admin", "active": True}, headers=AUTH).status_code == 200


def test_reset_password_guard_follows_admin_username(client):
    r = client.post("/api/users/u_primary/password", json={"password": "SandiBaru1"}, headers=AUTH)
    assert r.status_code == 400
    assert client.post("/api/users/u_staff/password", json={"password": "SandiBaru1"}, headers=AUTH).status_code == 200
