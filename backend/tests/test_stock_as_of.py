"""In-process tests for dated report titles and stock position as of a past date.

Run: cd backend && python -m pytest tests/test_stock_as_of.py -q
"""
import io
import os
import sys
from datetime import date, datetime, timedelta, timezone
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
from openpyxl import load_workbook  # noqa: E402

import report_dates  # noqa: E402
import server  # noqa: E402

WITA = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 12, 17, 30, tzinfo=timezone.utc)  # 13 November 2026 01:30 WITA
AUTH = {"Authorization": "Bearer tok_petugas"}


def wita(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=WITA).astimezone(timezone.utc)


def tx(tx_id, tx_type, before, after, occurred_at, **extra):
    return {"transaction_id": tx_id, "type": tx_type, "item_id": "beras", "item_name": "Beras", "category": "Pangan",
            "unit": "KG", "previous_quantity": before, "new_quantity": after, "change_quantity": after - before,
            "user_id": "u_petugas", "user_name": "Petugas", "role": "petugas", "occurred_at": occurred_at,
            "created_at": occurred_at, **extra}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(report_dates, "now_utc", lambda: NOW)
    db = AsyncMongoMockClient()["as_of_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True

    async def seed_users():
        await db.users.insert_one({"user_id": "u_petugas", "email": "petugas@x.id", "name": "Petugas", "role": "petugas", "active": True})
        await db.user_sessions.insert_one({"session_token": "tok_petugas", "auth_method": "google", "user_id": "u_petugas",
                                           "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})

    with TestClient(server.app) as c:
        c.portal.call(seed_users)
        c.db = db
        yield c


def seed(c, stock, txs):
    async def run():
        await c.db.items.insert_one({"id": "beras", "name": "Beras", "category": "Pangan", "unit": "KG",
                                     "currentStock": stock, "minThreshold": 10, "lastUpdated": NOW})
        if txs:
            await c.db.transactions.insert_many(txs)
    c.portal.call(run)


def stock_sheet(c, query=""):
    r = c.get(f"/api/export/stock{query}", headers=AUTH)
    assert r.status_code == 200, r.text
    ws = load_workbook(io.BytesIO(r.content)).active
    rows = list(ws.iter_rows(values_only=True))
    header = next(i for i, row in enumerate(rows) if row[0] == "ID")
    stock = {row[0]: row[3] for row in rows[header + 1:]}
    return rows, stock


def pdf_text(content):
    from pypdf import PdfReader
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(content)).pages)


def test_tanggal_id_spells_the_month():
    assert report_dates.tanggal_id(date(2026, 9, 30)) == "30 September 2026"
    assert report_dates.tanggal_id(date(2026, 8, 1)) == "1 Agustus 2026"


def test_no_date_uses_today_in_wita(client):
    seed(client, 80, [])
    rows, stock = stock_sheet(client)
    assert rows[0][0] == "Rekapitulasi Posisi Stok Pada Tanggal 13 November 2026"
    assert stock["beras"] == 80
    assert any(row[0] == "Dicetak: 13/11/2026 01:30 WITA" for row in rows)
    r = client.get("/api/export/stock/pdf", headers=AUTH)
    assert r.status_code == 200, r.text
    assert "Rekapitulasi Posisi Stok Pada Tanggal 13 November 2026" in pdf_text(r.content)


def test_past_date_takes_back_later_movements(client):
    seed(client, 80, [
        tx("tx_in", "IN", 0, 100, wita(2026, 9, 1, 9)),
        tx("tx_out", "OUT", 100, 80, wita(2026, 10, 1, 0, 0)),
    ])
    rows, stock = stock_sheet(client, "?date=2026-09-30")
    assert rows[0][0] == "Rekapitulasi Posisi Stok Pada Tanggal 30 September 2026"
    assert stock["beras"] == 100
    assert stock_sheet(client, "?date=2026-10-01")[1]["beras"] == 80
    assert stock_sheet(client, "?date=2026-08-31")[1]["beras"] == 0
    r = client.get("/api/export/stock/pdf?date=2026-09-30", headers=AUTH)
    assert r.status_code == 200, r.text
    assert "Rekapitulasi Posisi Stok Pada Tanggal 30 September 2026" in pdf_text(r.content)


def test_cancelled_transaction_and_its_reversal_are_ignored(client):
    cancelled_at = wita(2026, 10, 5, 10)
    seed(client, 100, [
        tx("tx_in", "IN", 0, 100, wita(2026, 9, 1, 9)),
        tx("tx_out", "OUT", 100, 80, wita(2026, 9, 10, 9), cancelled=True, cancelled_at=cancelled_at,
           reversal_transaction_id="reversal_tx_out"),
        tx("reversal_tx_out", "REVERSAL", 80, 100, cancelled_at, reversal_of="tx_out"),
    ])
    assert stock_sheet(client, "?date=2026-09-30")[1]["beras"] == 100


def test_later_correction_is_taken_back(client):
    seed(client, 95, [
        tx("tx_in", "IN", 0, 100, wita(2026, 9, 1, 9)),
        tx("tx_adj", "ADJUSTMENT", 100, 95, wita(2026, 10, 2, 9), reason="Opname"),
    ])
    assert stock_sheet(client, "?date=2026-09-30")[1]["beras"] == 100


@pytest.mark.parametrize("value", ["30-09-2026", "2026-13-01", "kemarin", "2026-11-14"])
@pytest.mark.parametrize("path", ["/api/export/stock", "/api/export/stock/pdf"])
def test_malformed_or_future_date_is_rejected(client, path, value):
    seed(client, 80, [])
    r = client.get(f"{path}?date={value}", headers=AUTH)
    assert r.status_code == 400, r.text
    assert "anggal" in r.json()["detail"]


def test_period_reports_state_dates_in_words(client):
    seed(client, 80, [tx("tx_out", "OUT", 100, 80, wita(2026, 9, 10, 9), destination="Desa A", incident_type="Banjir")])
    period = "Periode 1 September 2026 s.d. 30 September 2026"
    query = "?start=2026-09-01&end=2026-09-30"
    for path in ("/api/export/distribution", "/api/export/transactions"):
        r = client.get(path + query, headers=AUTH)
        assert r.status_code == 200, r.text
        for ws in load_workbook(io.BytesIO(r.content)).worksheets:
            assert ws["A1"].value.endswith(period), ws["A1"].value
        r = client.get(path + "/pdf" + query, headers=AUTH)
        assert r.status_code == 200, r.text
        assert period in pdf_text(r.content)
    r = client.get("/api/export/transactions", headers=AUTH)
    assert r.status_code == 200, r.text
    assert load_workbook(io.BytesIO(r.content)).active["A1"].value.endswith("Sampai Dengan Tanggal 13 November 2026")
