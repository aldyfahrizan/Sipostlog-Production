"""/api/health when the database is unreachable (no live server needed).

Run: cd backend && python -m pytest tests/test_health.py -q
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from pymongo.errors import ServerSelectionTimeoutError

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "sipostlog_test")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "$2b$12$" + "x" * 53)
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@sipostlog.local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


def call_health(monkeypatch, count_documents):
    monkeypatch.setattr(server, "db", SimpleNamespace(items=SimpleNamespace(count_documents=count_documents)))
    return asyncio.run(server.health())


def test_health_reports_ok_when_database_answers(monkeypatch):
    async def count(_filter):
        return 7

    body = call_health(monkeypatch, count)
    assert body["status"] == "ok"
    assert body["items"] == 7
    assert set(body) == {"status", "items", "expected", "admin_password_config", "ai", "time"}


def test_health_answers_503_when_database_is_down(monkeypatch):
    async def count(_filter):
        raise ServerSelectionTimeoutError("db:27017: connection closed")

    response = call_health(monkeypatch, count)
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "db_unavailable"}


def test_health_does_not_wait_for_a_hanging_database(monkeypatch):
    async def count(_filter):
        await asyncio.sleep(30)

    monkeypatch.setattr(server, "HEALTH_DB_TIMEOUT_SECONDS", 0.05)
    response = call_health(monkeypatch, count)
    assert response.status_code == 503
    assert json.loads(response.body) == {"status": "db_unavailable"}


def test_health_timeout_is_three_seconds():
    assert server.HEALTH_DB_TIMEOUT_SECONDS == 3
