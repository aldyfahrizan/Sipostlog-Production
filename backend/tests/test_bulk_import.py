"""In-process tests for bulk Barang Masuk / Penyaluran import (no live server needed).

Run: cd backend && python -m pytest tests/test_bulk_import.py -q
Requires: pip install mongomock-motor pytest
"""
import io
import json
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
from openpyxl import load_workbook  # noqa: E402

import server  # noqa: E402

ITEMS = [
    {"id": "beras", "name": "Beras", "category": "Pangan", "unit": "KG", "currentStock": 1000, "minThreshold": 100},
    {"id": "selimut", "name": "Selimut", "category": "Sandang", "unit": "BUAH", "currentStock": 40, "minThreshold": 10},
    {"id": "mie", "name": "Mi Instan", "category": "Pangan", "unit": "DOS", "currentStock": 85, "minThreshold": 10},
]
TOKENS = {"petugas": "tok_petugas", "opname": "tok_opname"}


@pytest.fixture()
def client():
    db = AsyncMongoMockClient()["bulk_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True

    async def seed():
        now = datetime.now(timezone.utc)
        await db.items.insert_many([{**i, "lastUpdated": now} for i in ITEMS])
        for role, token in TOKENS.items():
            await db.users.insert_one({"user_id": f"u_{role}", "email": f"{role}@x.id", "name": role.title(), "role": role, "active": True})
            await db.user_sessions.insert_one({"session_token": token, "auth_method": "google", "user_id": f"u_{role}",
                                               "expires_at": now + timedelta(hours=1)})

    with TestClient(server.app) as c:
        c.portal.call(seed)
        c.db = db
        yield c


def auth(role="petugas"):
    return {"Authorization": f"Bearer {TOKENS[role]}"}


def template(c, kind, header: dict, quantities: dict, notes: dict | None = None, extra_rows=()):
    r = c.get(f"/api/excel/template/{kind}", headers=auth())
    assert r.status_code == 200, r.text
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    for row in ws.iter_rows():
        label = str(row[0].value or "").rstrip(" *")
        if label in header:
            row[1].value = header[label]
        if row[0].value in quantities:
            row[5].value = quantities[row[0].value]
            if notes and row[0].value in notes:
                row[6].value = notes[row[0].value]
    for extra in extra_rows:
        ws.append(list(extra))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


WITA_TODAY = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date()
RECENT = (WITA_TODAY - timedelta(days=7)).strftime("%d/%m/%Y")
IN_HEADER = {"Sumber": "BNPB", "Tanggal (DD/MM/YYYY)": RECENT, "No. BAST/Dokumen": "BAST-01"}
OUT_HEADER = {"Lokasi Tujuan": "Desa Sungai Tabuk", "Jenis Kejadian": "Banjir", "Tanggal (DD/MM/YYYY)": RECENT,
              "Jam (HH:MM, WITA)": "14:30", "Jumlah KK": 45, "Jumlah Jiwa": 160, "No. BAST/Dokumen": "012/BAST"}


def preview(c, kind, content, role="petugas"):
    return c.post(f"/api/excel/import/{kind}/preview", headers=auth(role),
                  files={"file": ("bast.xlsx", content, "application/octet-stream")})


def commit(c, kind, content, snapshot):
    return c.post(f"/api/excel/import/{kind}/commit", headers=auth(),
                  files={"file": ("bast.xlsx", content, "application/octet-stream")}, data={"snapshot": json.dumps(snapshot)})


def stock(c, item_id):
    return c.portal.call(c.db.items.find_one, {"id": item_id})["currentStock"]


def test_in_preview_then_commit_saves_all_rows_with_batch(client):
    content = template(client, "in", IN_HEADER, {"beras": 200, "mie": 15}, {"beras": "Karung 50kg"})
    p = preview(client, "in", content)
    assert p.status_code == 200, p.text
    data = p.json()
    assert data["valid"] is True and data["row_errors"] == 0
    assert [(r["item_id"], r["quantity"], r["after"]) for r in data["rows"]] == [("beras", 200, 1200), ("mie", 15, 100)]
    assert stock(client, "beras") == 1000  # preview never writes

    r = commit(client, "in", content, data["snapshot"])
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved["count"] == 2 and saved["batch_ref"] == "BAST-01"
    assert stock(client, "beras") == 1200 and stock(client, "mie") == 100
    tx = client.get("/api/transactions", headers=auth(), params={"batch_id": saved["batch_id"]}).json()
    assert {t["item_id"] for t in tx} == {"beras", "mie"}
    assert all(t["type"] == "IN" and t["source"] == "BNPB" and t["batch_file"] == "bast.xlsx" for t in tx)
    assert next(t for t in tx if t["item_id"] == "beras")["notes"] == "Karung 50kg"


def test_out_with_one_bad_row_is_not_valid_and_commit_saves_nothing(client):
    content = template(client, "out", OUT_HEADER, {"beras": 300, "selimut": 60})
    data = preview(client, "out", content).json()
    assert data["valid"] is False and data["row_errors"] == 1
    bad = next(r for r in data["rows"] if r["item_id"] == "selimut")
    assert "Stok tidak cukup" in bad["error"]
    r = commit(client, "out", content, data["snapshot"])
    assert r.status_code == 400
    assert stock(client, "beras") == 1000 and stock(client, "selimut") == 40


def test_commit_rejected_when_stock_changed_after_preview(client):
    content = template(client, "out", OUT_HEADER, {"beras": 300})
    data = preview(client, "out", content).json()
    client.portal.call(client.db.items.update_one, {"id": "beras"}, {"$inc": {"currentStock": -1}})
    r = commit(client, "out", content, data["snapshot"])
    assert r.status_code == 409
    assert stock(client, "beras") == 999


def test_double_commit_is_rejected(client):
    content = template(client, "in", IN_HEADER, {"beras": 10})
    snap = preview(client, "in", content).json()["snapshot"]
    assert commit(client, "in", content, snap).status_code == 201
    assert commit(client, "in", content, snap).status_code == 409
    assert stock(client, "beras") == 1010


def test_row_validation_messages(client):
    content = template(client, "in", IN_HEADER, {"beras": 2.5, "mie": -3},
                       extra_rows=[("beras", "Beras", "", "", "", 5, ""), ("tidak-ada", "X", "", "", "", 1, "")])
    rows = {(r["item_id"], r["row"]): r["error"] for r in preview(client, "in", content).json()["rows"]}
    errors = list(rows.values())
    assert any("bilangan bulat" in (e or "") for e in errors)
    assert any("Item ganda" in (e or "") for e in errors)
    assert any("tidak dikenal" in (e or "") for e in errors)


def test_header_validation(client):
    bad = {**OUT_HEADER, "Jenis Kejadian": "Gempa Bumi", "Lokasi Tujuan": "", "Jam (HH:MM, WITA)": "25:99"}
    data = preview(client, "out", template(client, "out", bad, {"beras": 1})).json()
    assert data["valid"] is False
    text = " ".join(data["header_errors"])
    assert "Jenis Kejadian" in text and "Lokasi Tujuan" in text and "Jam" in text


def test_empty_file_and_wrong_template(client):
    empty = preview(client, "in", template(client, "in", IN_HEADER, {})).json()
    assert empty["valid"] is False and any("Belum ada baris" in e for e in empty["header_errors"])
    wrong = preview(client, "in", template(client, "out", OUT_HEADER, {"beras": 1}))
    assert wrong.status_code == 400 and "Penyaluran" in wrong.json()["detail"]


def test_opname_role_cannot_bulk_import(client):
    r = client.get("/api/excel/template/in", headers=auth("opname"))
    assert r.status_code == 403


def test_public_distribution_groups_batch_and_counts_kk_once(client):
    now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    header = {**OUT_HEADER, "Tanggal (DD/MM/YYYY)": now.strftime("%d/%m/%Y"),
              "Jam (HH:MM, WITA)": (now - timedelta(minutes=5)).strftime("%H:%M") if now.hour or now.minute >= 5 else "00:00"}
    content = template(client, "out", header, {"beras": 100, "mie": 5, "selimut": 2})
    data = preview(client, "out", content).json()
    assert data["valid"], data
    assert commit(client, "out", content, data["snapshot"]).status_code == 201
    events = client.get("/api/public/distribution", params={"days": 7}).json()["events"]
    assert len(events) == 1
    assert sorted(events[0]["item_names"]) == ["Beras", "Mi Instan", "Selimut"]
    assert events[0]["recipient_kk"] == 45 and events[0]["recipient_jiwa"] == 160
    assert events[0]["incident_type"] == "Banjir"
    summary = client.get("/api/public/distribution", params={"days": 7}).json()["incident_types"]
    assert [s["incident_type"] for s in summary] == ["Banjir", "Kebakaran", "Tanah Longsor", "Angin Kencang", "Kekeringan", "Lainnya"]
    assert summary[0] == {"incident_type": "Banjir", "events": 1, "locations": 1}
    assert all(s["events"] == 0 for s in summary[1:])
    assert set(events[0]) == {"event_id", "occurred_at", "location", "item_names", "incident_type", "recipient_kk", "recipient_jiwa"}


def test_numeric_time_cell_is_rejected_not_misread(client):
    for number in (14.3, 14, 0.6):
        data = preview(client, "out", template(client, "out", {**OUT_HEADER, "Jam (HH:MM, WITA)": number}, {"beras": 1})).json()
        assert data["valid"] is False and data["header"]["time"] is None
        assert any("Jam" in e and "angka" in e for e in data["header_errors"]), data["header_errors"]
    for text, expected in (("14.30", "14:30"), ("7:05", "07:05")):
        data = preview(client, "out", template(client, "out", {**OUT_HEADER, "Jam (HH:MM, WITA)": text}, {"beras": 1})).json()
        assert data["valid"] is True and data["header"]["time"] == expected, data["header_errors"]
    data = preview(client, "out", template(client, "out", {**OUT_HEADER, "Jam (HH:MM, WITA)": "14:3"}, {"beras": 1})).json()
    assert data["valid"] is False


@pytest.mark.parametrize("kind,base", [("in", IN_HEADER), ("out", OUT_HEADER)])
def test_future_and_very_old_dates_are_rejected(client, kind, base):
    for day in (WITA_TODAY + timedelta(days=1), WITA_TODAY - timedelta(days=731)):
        header = {**base, "Tanggal (DD/MM/YYYY)": day.strftime("%d/%m/%Y")}
        data = preview(client, kind, template(client, kind, header, {"beras": 1})).json()
        assert data["valid"] is False
        assert any(e.startswith("Tanggal tidak masuk akal: " + day.strftime("%d/%m/%Y")) for e in data["header_errors"]), data["header_errors"]
    for day in (WITA_TODAY - timedelta(days=1), WITA_TODAY - timedelta(days=729)):
        header = {**base, "Tanggal (DD/MM/YYYY)": day.strftime("%d/%m/%Y")}
        data = preview(client, kind, template(client, kind, header, {"beras": 1})).json()
        assert data["valid"] is True, data["header_errors"]


def test_template_dropdown_shows_error_message(client):
    r = client.get("/api/excel/template/out", headers=auth())
    ws = load_workbook(io.BytesIO(r.content)).active
    validations = ws.data_validations.dataValidation
    assert validations and all(dv.showErrorMessage for dv in validations)


def test_missing_required_header_label_is_reported(client):
    r = client.get("/api/excel/template/out", headers=auth())
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    for row in ws.iter_rows():
        label = str(row[0].value or "").rstrip(" *")
        if label in OUT_HEADER:
            row[1].value = OUT_HEADER[label]
        if label == "Jam (HH:MM, WITA)":
            row[0].value = "Waktu"
        if row[0].value == "beras":
            row[5].value = 1
    buf = io.BytesIO()
    wb.save(buf)
    resp = preview(client, "out", buf.getvalue())
    assert resp.status_code == 400
    assert "Jam (HH:MM, WITA)" in resp.json()["detail"] and "tidak ditemukan" in resp.json()["detail"]
