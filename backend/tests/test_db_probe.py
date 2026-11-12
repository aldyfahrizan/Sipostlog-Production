"""Fresh-connection database probe: GET /api/health/db.

Run: cd backend && python -m pytest tests/test_db_probe.py -q   (needs mongomock-motor)
"""
import logging
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "sipostlog_test")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "$2b$12$" + "x" * 53)
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@sipostlog.local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

import db_probe  # noqa: E402
import server  # noqa: E402

SECRET = "s3cretPassw0rd"


class Fake:
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.now = 1000.0

    def probe(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"auth failed for mongodb://root:{SECRET}@db.internal:27017/?authSource=admin")

    def clock(self):
        return self.now


@pytest.fixture()
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(db_probe, "_ping_new_connection", f.probe)
    monkeypatch.setattr(db_probe, "_clock", f.clock)
    monkeypatch.setattr(db_probe, "_cache", None)
    return f


@pytest.fixture()
def client(fake):
    db = AsyncMongoMockClient()["db_probe_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True
    with TestClient(server.app) as c:
        yield c


def test_success_returns_exact_body(client, fake):
    r = client.get("/api/health/db")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"status", "checked_at", "age_seconds"}
    assert body["status"] == "ok" and body["age_seconds"] == 0
    assert body["checked_at"].endswith("+00:00")
    fake.now += 42.9
    again = client.get("/api/health/db").json()
    assert again["age_seconds"] == 42 and again["checked_at"] == body["checked_at"]
    assert fake.calls == 1


def test_failure_is_503_and_leaks_nothing(client, fake, caplog):
    fake.fail = True
    with caplog.at_level(logging.DEBUG):
        r = client.get("/api/health/db")
    assert r.status_code == 503
    assert r.headers["cache-control"] == "no-store"
    assert set(r.json()) == {"status", "checked_at", "age_seconds"}
    assert r.json()["status"] == "fail"
    logged = "\n".join(rec.getMessage() + str(rec.exc_info or "") for rec in caplog.records)
    assert "RuntimeError" in logged
    for text in (r.text, str(r.headers), logged, caplog.text):
        for needle in (SECRET, "mongodb://", "db.internal", "auth failed"):
            assert needle not in text
    assert client.get("/api/health").status_code == 200


def test_failure_is_probed_once_per_minute(client, fake):
    fake.fail = True
    for _ in range(20):
        assert client.get("/api/health/db").status_code == 503
    assert fake.calls == 1
    fake.now += 59
    assert client.get("/api/health/db").status_code == 503
    assert fake.calls == 1
    fake.now += 1
    fake.fail = False
    r = client.get("/api/health/db")
    assert r.status_code == 200 and r.json()["age_seconds"] == 0
    assert fake.calls == 2
