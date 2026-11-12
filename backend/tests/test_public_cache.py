"""In-process tests for the public endpoint cache, row limit and index (no live server needed)."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "sipostlog_test")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "$2b$12$" + "x" * 53)
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@sipostlog.local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockCollection  # noqa: E402

import public_cache  # noqa: E402
import public_distribution  # noqa: E402
import server  # noqa: E402

URLS = ["/api/public/items", "/api/public/summary", "/api/public/distribution?days=7"]


def tx(n, minutes_ago):
    return {"transaction_id": f"t{n}", "type": "OUT", "user_id": "u1", "item_name": f"Barang {n}",
            "destination": "Desa A", "incident_type": "Banjir",
            "occurred_at": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)}


@pytest.fixture()
def client(monkeypatch):
    db = AsyncMongoMockClient()["public_cache_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True
    clock = {"now": 1000.0}
    monkeypatch.setattr(public_cache, "_now", lambda: clock["now"])

    async def seed():
        await db.items.insert_many([
            {"id": "beras", "name": "Beras", "category": "Pangan", "unit": "KG", "currentStock": 5},
            {"id": "selimut", "name": "Selimut", "category": "Sandang", "unit": "BUAH", "currentStock": 7},
        ])
        await db.transactions.insert_many([tx(1, 3), tx(2, 2), tx(3, 1)])

    with TestClient(server.app) as c:
        c.portal.call(seed)
        c.db, c.clock = db, clock
        yield c


def count_finds(client, monkeypatch):
    """Count find() calls; collections are created per access, so patch the class."""
    calls = []
    original = AsyncMongoMockCollection.find

    def find(self, *args, **kwargs):
        calls.append(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AsyncMongoMockCollection, "find", find)
    return calls


@pytest.mark.parametrize("url", URLS)
def test_second_call_within_ttl_skips_database(client, monkeypatch, url):
    calls = count_finds(client, monkeypatch)
    first = client.get(url)
    assert len(calls) == 1
    client.clock["now"] += 59
    second = client.get(url)
    assert len(calls) == 1
    assert second.json() == first.json()
    assert second.headers["cache-control"] == first.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("url", URLS)
def test_cache_expires_after_ttl(client, monkeypatch, url):
    calls = count_finds(client, monkeypatch)
    client.get(url)
    client.clock["now"] += 61
    client.get(url)
    assert len(calls) == 2


def test_cache_is_keyed_by_parameters(client, monkeypatch):
    calls = count_finds(client, monkeypatch)
    assert client.get("/api/public/distribution?days=7").json()["days"] == 7
    assert client.get("/api/public/distribution?days=14").json()["days"] == 14
    assert len(calls) == 2


def test_results_unchanged(client):
    assert client.get("/api/public/items").json() == [
        {"id": "beras", "name": "Beras", "category": "Pangan", "unit": "KG"},
        {"id": "selimut", "name": "Selimut", "category": "Sandang", "unit": "BUAH"}]
    assert client.get("/api/public/summary").json() == {
        "total_items": 2, "categories": [{"category": "Pangan", "count": 1}, {"category": "Sandang", "count": 1}]}
    body = client.get("/api/public/distribution?days=7").json()
    assert [e["event_id"] for e in body["events"]] == ["t3", "t2", "t1"]
    assert set(body) == {"days", "start_date", "end_date", "timezone", "incident_types", "events"}


def test_distribution_has_hard_row_limit(client, monkeypatch):
    assert public_distribution.MAX_ROWS == 5000
    monkeypatch.setattr(public_distribution, "MAX_ROWS", 2)
    events = client.get("/api/public/distribution?days=7").json()["events"]
    assert [e["event_id"] for e in events] == ["t3", "t2"]


def test_initialize_database_creates_distribution_index(client):
    assert server.SCHEMA_VERSION == 2
    client.portal.call(server.initialize_database)
    keys = [i["key"] for i in client.portal.call(client.db.transactions.index_information).values()]
    assert [("type", 1), ("occurred_at", -1), ("transaction_id", 1)] in keys
