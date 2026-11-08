"""Malformed ADMIN_PASSWORD_HASH must never turn login into HTTP 500.

Run: cd backend && python -m pytest tests/test_admin_hash_config.py -q   (needs mongomock-motor)
"""
import os
import sys
from datetime import datetime, timezone
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

GOOD = bcrypt.hashpw(b"Lima55555@", bcrypt.gensalt(rounds=4)).decode()
TRUNCATED = "$2b$12"


@pytest.fixture()
def make_client(monkeypatch):
    def _make(env_hash, stored_hash=None):
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", env_hash)
        db = AsyncMongoMockClient()["hash_test"]
        server.db = db
        server.app.state.db = db
        server._init_done = True
        client = TestClient(server.app)
        client.__enter__()

        async def seed():
            if stored_hash is not None:
                await db.users.insert_one({"user_id": "u_admin", "email": "admin@sipostlog.local", "username": "admin",
                                           "name": "Admin", "auth_method": "password", "role": "admin", "active": True,
                                           "password_hash": stored_hash, "created_at": datetime.now(timezone.utc)})
            await admin_login.seed_local_admin(db)
        client.portal.call(seed)
        client.db = db
        return client
    return _make


def login(c, username, password):
    return c.post("/api/auth/login", json={"username": username, "password": password})


def test_valid_hash_login_works(make_client):
    c = make_client(GOOD)
    assert login(c, "admin", "Lima55555@").status_code == 200
    assert login(c, "admin", "salah").status_code == 401
    assert c.get("/api/health").json()["admin_password_config"] == "ok"


def test_unknown_user_never_500_even_with_broken_env_hash(make_client):
    c = make_client(TRUNCATED, stored_hash=GOOD)
    assert login(c, "cek-otomatis-github", "x").status_code == 401
    assert c.get("/api/health").json()["admin_password_config"] == "invalid"


def test_broken_env_hash_keeps_previous_admin_password(make_client):
    c = make_client(TRUNCATED, stored_hash=GOOD)
    stored = c.portal.call(c.db.users.find_one, {"username": "admin"})["password_hash"]
    assert stored == GOOD
    assert login(c, "admin", "Lima55555@").status_code == 200


def test_broken_stored_hash_gives_clear_503_not_500(make_client):
    c = make_client(TRUNCATED, stored_hash=TRUNCATED)
    r = login(c, "admin", "apa-saja")
    assert r.status_code == 503
    assert "Konfigurasi kata sandi" in r.json()["detail"]
