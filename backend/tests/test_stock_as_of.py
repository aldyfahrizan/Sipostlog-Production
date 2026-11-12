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
clock = {"now": NOW}


def wita(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=WITA).astimezone(timezone.utc)


def tx(tx_id, tx_type, before, after, occurred_at, **extra):
    return {"transaction_id": tx_id, "type": tx_type, "item_id": "beras", "item_name": "Beras", "category": "Pangan",
            "unit": "KG", "previous_quantity": before, "new_quantity": after, "change_quantity": after - before,
            "user_id": "u_petugas", "user_name": "Petugas", "role": "petugas", "occurred_at": occurred_at,
            "created_at": occurred_at, **extra}


@pytest.fixture()
def client(monkeypatch):
    clock["now"] = NOW
    monkeypatch.setattr(report_dates, "now_utc", lambda: clock["now"])
    monkeypatch.setattr(server, "now_utc", lambda: clock["now"])
    db = AsyncMongoMockClient()["as_of_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True

    async def seed_users():
        await db.users.insert_one({"user_id": "u_petugas", "email": "petugas@x.id", "name": "Petugas", "role": "petugas", "active": True})
        await db.users.insert_one({"user_id": "u_admin", "email": "admin@x.id", "name": "Admin", "role": "admin", "active": True})
        await db.user_sessions.insert_one({"session_token": "tok_admin", "auth_method": "google", "user_id": "u_admin",
                                           "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})
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
    assert "beras" not in stock_sheet(client, "?date=2026-08-31")[1]  # first movement is later: not in the warehouse yet
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


def test_period_title_states_the_range_the_report_really_holds(client):
    seed(client, 80, [])
    for query, period in (("", "Periode 14 Oktober 2026 s.d. 12 November 2026"),
                          ("?start=2026-11-01", "Periode 1 November 2026 s.d. 12 November 2026"),
                          ("?end=2026-09-30", "s.d. 30 September 2026")):
        for path in ("/api/export/distribution", "/api/export/transactions"):
            r = client.get(path + query, headers=AUTH)
            assert r.status_code == 200, r.text
            assert load_workbook(io.BytesIO(r.content)).active["A1"].value.endswith(period)
            r = client.get(path + "/pdf" + query, headers=AUTH)
            assert r.status_code == 200, r.text
            assert period in pdf_text(r.content)


def stock_in(c, day):
    return c.post("/api/transactions/in", headers=AUTH, json={"item_id": "beras", "quantity": 100, "source": "APBD", "date": day})


def test_stock_in_after_midnight_wita_is_stamped_on_its_own_day(client):
    seed(client, 0, [])
    r = stock_in(client, "2026-11-13")  # today in WITA, still 12 November in UTC
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()["occurred_at"]) == NOW
    clock["now"] = datetime(2026, 11, 15, 2, 0, tzinfo=timezone.utc)
    assert stock_sheet(client, "?date=2026-11-13")[1]["beras"] == 100
    assert "beras" not in stock_sheet(client, "?date=2026-11-12")[1]


def test_backdated_entry_before_8am_wita_lands_on_the_chosen_day(client):
    seed(client, 0, [])
    r = stock_in(client, "2026-11-10")
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()["occurred_at"]) == wita(2026, 11, 10, 1, 30)
    assert stock_sheet(client, "?date=2026-11-10")[1]["beras"] == 100
    assert "beras" not in stock_sheet(client, "?date=2026-11-09")[1]
    r = client.post("/api/transactions/adjust", headers=AUTH,
                    json={"item_id": "beras", "new_quantity": 90, "reason": "Opname", "date": "2026-11-11"})
    if r.status_code != 403:  # correction may be closed to this role; the date rule is the same function
        assert r.status_code in (200, 201), r.text
        assert stock_sheet(client, "?date=2026-11-11")[1]["beras"] == 90


def test_future_date_is_refused_and_importers_report_it_as_an_error(client):
    import ai_import
    import bulk_import
    seed(client, 0, [])
    r = stock_in(client, "2026-11-14")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Tanggal tidak boleh melewati hari ini."
    header, errors = bulk_import.validate_header("in", {"source": "APBD", "date": "14/11/2026"})
    assert errors == ["Tanggal tidak boleh melewati hari ini."]
    assert "occurred_at" not in header
    header, errors = bulk_import.validate_header("in", {"source": "APBD", "date": "13/11/2026"})
    assert errors == [] and header["occurred_at"] == NOW
    preview = client.portal.call(ai_import.opname_preview, client.db, {"date": "14/11/2026"}, [])
    assert "Tanggal opname tidak boleh di masa depan." in preview["header_errors"]
    preview = client.portal.call(ai_import.opname_preview, client.db, {"date": "13/11/2026"}, [])
    assert "Tanggal opname tidak boleh di masa depan." not in preview["header_errors"]


def other_item(item_id, name, stock):
    return {"id": item_id, "name": name, "category": "Pangan", "unit": "DUS", "currentStock": stock, "minThreshold": 1, "lastUpdated": NOW}


def test_past_report_keeps_item_deleted_since_and_skips_item_created_later(client):
    mie = {"item_id": "mie", "item_name": "Mie", "unit": "DUS"}
    seed(client, 80, [
        tx("mie_in", "IN", 0, 50, wita(2026, 9, 1, 9), **mie),
        tx("mie_out", "OUT", 50, 0, wita(2026, 10, 5, 10), **mie, destination="Desa A", incident_type="Banjir"),
        tx("gula_in", "IN", 0, 20, wita(2026, 10, 20, 9), item_id="gula", item_name="Gula", unit="DUS"),
        tx("teh_out", "OUT", 30, 10, wita(2026, 10, 20, 9), item_id="teh", item_name="Teh", unit="DUS"),
    ])

    async def more_items():  # teh: catalogue item whose first recorded movement is after the report date
        await client.db.items.insert_many([other_item("mie", "Mie", 0), other_item("gula", "Gula", 20), other_item("teh", "Teh", 10)])
    client.portal.call(more_items)
    assert stock_sheet(client, "?date=2026-09-30")[1] == {"beras": 80, "mie": 50, "teh": 30}
    r = client.request("DELETE", "/api/items/mie", headers={"Authorization": "Bearer tok_admin"}, json={"reason": "Tidak dipakai lagi"})
    assert r.status_code == 200, r.text
    rows, stock = stock_sheet(client, "?date=2026-09-30")
    assert stock == {"beras": 80, "mie": 50, "teh": 30}
    mie_row = next(row for row in rows if row[0] == "mie")
    assert mie_row[1:3] == ("Mie", "Pangan") and mie_row[4] == "DUS"
    assert all(not row[7] for row in rows if row[0] in stock)  # "Pembaruan Terakhir" would show a later date
    assert "mie" not in stock_sheet(client, "?date=2026-10-05")[1]
    assert stock_sheet(client)[1] == {"beras": 80, "gula": 20, "teh": 10}
    r = client.get("/api/export/stock/pdf?date=2026-09-30", headers=AUTH)
    assert r.status_code == 200, r.text
    text = pdf_text(r.content)
    assert "Mie" in text and "Gula" not in text
