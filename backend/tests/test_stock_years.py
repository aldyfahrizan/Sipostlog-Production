"""In-process tests: the realised year follows the WITA calendar, the plan year follows the plan data.

Run: cd backend && python -m pytest tests/test_stock_years.py -q
"""
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
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

import server  # noqa: E402

WITA = timezone(timedelta(hours=8))
LAST_DAY_2026 = datetime(2026, 12, 31, 23, 59, tzinfo=WITA)  # 15:59 UTC, still 2026 everywhere
FIRST_DAY_2027 = datetime(2027, 1, 1, 0, 0, tzinfo=WITA)  # 16:00 UTC on 31 December 2026
ITEMS = [
    {"id": "beras", "name": "Beras", "category": "Pangan", "unit": "KG", "currentStock": 400, "minThreshold": 100, "planYear": {"2026": 1000, "2027": 800}},
    {"id": "selimut", "name": "Selimut", "category": "Sandang", "unit": "BUAH", "currentStock": 0, "minThreshold": 10, "planYear": {"2026": 50, "2027": 0}},
    {"id": "tenda", "name": "Tenda", "category": "Sandang", "unit": "BUAH", "currentStock": 5, "minThreshold": 1, "planYear": {}},
]
AUTH = {"Authorization": "Bearer tok_petugas"}


@pytest.fixture()
def client():
    db = AsyncMongoMockClient()["years_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True

    async def seed():
        now = datetime.now(timezone.utc)
        await db.items.insert_many([{**i, "lastUpdated": now} for i in ITEMS])
        await db.users.insert_one({"user_id": "u_petugas", "email": "petugas@x.id", "name": "Petugas", "role": "petugas", "active": True})
        await db.user_sessions.insert_one({"session_token": "tok_petugas", "auth_method": "google", "user_id": "u_petugas",
                                           "expires_at": now + timedelta(hours=1)})

    with TestClient(server.app) as c:
        c.portal.call(seed)
        c.db = db
        yield c


def freeze(monkeypatch, moment):
    monkeypatch.setattr(server, "now_utc", lambda: moment.astimezone(timezone.utc))


def stock(client, **params):
    r = client.get("/api/dashboard/stock", params=params, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def by_id(rows):
    return {i["id"]: i for i in rows}


def years(payload):
    return payload["year"], payload["realized"], payload["realized_year"], payload["plan_year"]


def test_last_day_of_2026_is_unchanged(client, monkeypatch):
    freeze(monkeypatch, LAST_DAY_2026)
    for params in ({}, {"year": "2026"}):
        real = stock(client, **params)
        assert years(real) == ("2026", True, "2026", "2027")
        assert by_id(real["items"])["beras"]["currentStock"] == 400
        assert by_id(real["items"])["beras"]["planQuantity"] == 1000
        assert real["status_counts"] == {"aman": 2, "menipis": 0, "habis": 1}
    plan = stock(client, year="2027")
    assert years(plan) == ("2027", False, "2026", "2027")
    assert plan["status_counts"] == {"rencana": 1, "tidak-dianggarkan": 2}
    assert by_id(plan["items"])["beras"]["currentStock"] == 0
    assert by_id(plan["items"])["beras"]["planQuantity"] == 800


def test_first_day_of_2027_wita_shows_real_stock_for_2027(client, monkeypatch):
    freeze(monkeypatch, FIRST_DAY_2027)
    for params in ({}, {"year": "2027"}):
        real = stock(client, **params)
        # No 2028 plan yet: the plan year stays at the latest year that has plan data.
        assert years(real) == ("2027", True, "2027", "2027")
        assert by_id(real["items"])["beras"]["currentStock"] == 400
        assert by_id(real["items"])["beras"]["planQuantity"] == 800
        assert "low_items" in real and "weekly_activity" in real
    rows = by_id(client.get("/api/items", params={"year": "2027"}, headers=AUTH).json())
    assert rows["beras"]["currentStock"] == 400 and rows["beras"]["status"] == "aman" and rows["beras"]["planQuantity"] == 800
    assert rows["tenda"]["planQuantity"] == 0


def test_plan_year_moves_on_once_next_year_has_plan_data(client, monkeypatch):
    freeze(monkeypatch, FIRST_DAY_2027)

    async def add_plan():
        await client.db.items.update_one({"id": "beras"}, {"$set": {"planYear.2028": 900}})

    client.portal.call(add_plan)
    plan = stock(client, year="2028")
    assert years(plan) == ("2028", False, "2027", "2028")
    assert by_id(plan["items"])["beras"]["currentStock"] == 0 and by_id(plan["items"])["beras"]["planQuantity"] == 900
    assert stock(client)["plan_year"] == "2028"


def test_items_list_in_2026_is_unchanged(client, monkeypatch):
    freeze(monkeypatch, LAST_DAY_2026)
    real = by_id(client.get("/api/items", headers=AUTH).json())
    assert real["beras"]["currentStock"] == 400 and real["beras"]["planQuantity"] == 1000
    plan = by_id(client.get("/api/items", params={"year": "2027"}, headers=AUTH).json())
    assert (plan["beras"]["currentStock"], plan["beras"]["status"], plan["beras"]["planQuantity"]) == (0, "rencana", 800)
    assert plan["selimut"]["status"] == "tidak-dianggarkan"


def test_realised_year_is_never_before_2026(client, monkeypatch):
    freeze(monkeypatch, datetime(2025, 6, 1, tzinfo=WITA))
    assert stock(client)["year"] == "2026"


def test_a_past_year_is_never_offered_as_the_plan(client, monkeypatch):
    freeze(monkeypatch, datetime(2028, 3, 1, tzinfo=WITA))
    assert years(stock(client, year="2027")) == ("2028", True, "2028", "2028")
