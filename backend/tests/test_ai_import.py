"""Input dari Dokumen (AI): file reading, extraction normalisation, preview and all-or-nothing commit.

The AI call is mocked; no network. Run: cd backend && python -m pytest tests/test_ai_import.py -q
"""
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "sipostlog_test")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "$2b$12$" + "x" * 53)
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@sipostlog.local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

import ai_import  # noqa: E402
import server  # noqa: E402

ITEMS = [
    {"id": "beras", "name": "Beras", "category": "Pangan", "unit": "KG", "currentStock": 1000, "minThreshold": 100},
    {"id": "selimut", "name": "Selimut", "category": "Sandang", "unit": "BUAH", "currentStock": 40, "minThreshold": 10},
    {"id": "mie", "name": "Mi Instan", "category": "Pangan", "unit": "DOS", "currentStock": 85, "minThreshold": 10},
]
OUT_HEADER = {"destination": "Desa Sungai Tabuk", "incident_type": "Banjir", "date": "2026-11-05", "time": "14:30",
              "recipient_kk": 45, "recipient_jiwa": 160, "ref": "012/BAST"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    db = AsyncMongoMockClient()["ai_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True

    async def seed():
        now = datetime.now(timezone.utc)
        await db.items.insert_many([{**i, "lastUpdated": now} for i in ITEMS])
        for role in ("petugas", "opname"):
            await db.users.insert_one({"user_id": f"u_{role}", "email": f"{role}@x.id", "name": role.title(), "role": role, "active": True})
            await db.user_sessions.insert_one({"session_token": f"tok_{role}", "auth_method": "google", "user_id": f"u_{role}",
                                               "expires_at": now + timedelta(hours=1)})

    with TestClient(server.app) as c:
        c.portal.call(seed)
        c.db = db
        yield c


def h(role="petugas"):
    return {"Authorization": f"Bearer tok_{role}"}


def stock(c, item_id):
    return c.portal.call(c.db.items.find_one, {"id": item_id})["currentStock"]


def fake_ai(monkeypatch, data):
    seen = {}

    async def fake(kind, catalog, text, images):
        seen.update(kind=kind, text=text, images=images, catalog=catalog)
        return data, "fake/model:free"
    monkeypatch.setattr(ai_import, "extract_with_ai", fake)
    return seen


def docx_bytes(lines):
    d = Document()
    for line in lines:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------- file reading ----------
def test_reads_docx_xlsx_pdf_text_and_scans():
    text, images = ai_import.read_document("bast.docx", docx_bytes(["BAST Penyaluran", "Beras 300 KG"]))
    assert "Beras 300 KG" in text and images == []

    wb = Workbook()
    wb.active.append(["Barang", "Jumlah"])
    wb.active.append(["Selimut", 20])
    buf = io.BytesIO()
    wb.save(buf)
    text, images = ai_import.read_document("rekap.xlsx", buf.getvalue())
    assert "Selimut | 20" in text and images == []

    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "Berita Acara Stock Opname: Mi Instan 80 DOS, stok fisik gudang logistik")
    c.save()
    text, images = ai_import.read_document("opname.pdf", buf.getvalue())
    assert "Mi Instan 80 DOS" in text and images == []

    from PIL import Image
    img = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(img, format="PNG")
    text, images = ai_import.read_document("foto.png", img.getvalue())
    assert text == "" and images[0].startswith("data:image/jpeg;base64,")

    blank = io.BytesIO()
    c = canvas.Canvas(blank)
    c.rect(50, 50, 200, 200, fill=1)  # no text layer -> treated as a scan
    c.save()
    text, images = ai_import.read_document("scan.pdf", blank.getvalue())
    assert text == "" and len(images) == 1


def test_parse_model_json_tolerates_code_fences():
    data = ai_import.parse_model_json('Berikut hasilnya:\n```json\n{"header": {}, "rows": [{"name": "Beras", "quantity": 5}]}\n```')
    assert data["rows"][0]["quantity"] == 5
    with pytest.raises(ValueError):
        ai_import.parse_model_json("maaf, tidak bisa")


def test_item_matching_uses_id_then_name_then_fuzzy():
    header, rows = ai_import.normalise_extraction({"header": {"ref": None, "date": "2026-11-05"}, "rows": [
        {"item_id": "beras", "name": "Beras Premium", "quantity": 1},
        {"item_id": "tidak-ada", "name": "selimut", "quantity": 2},
        {"item_id": None, "name": "Mie Instan", "quantity": 3},
        {"item_id": None, "name": "Genset 5000 watt", "quantity": 4},
    ]}, ITEMS)
    assert [r["item_id"] for r in rows] == ["beras", "selimut", "mie", ""]
    assert header == {"date": "2026-11-05"}


# ---------- distribution ----------
def test_out_extract_preview_edit_and_commit(client, monkeypatch):
    seen = fake_ai(monkeypatch, {"header": OUT_HEADER, "rows": [
        {"item_id": "beras", "name": "Beras", "quantity": 300, "unit": "KG"},
        {"item_id": None, "name": "Tenda Pleton", "quantity": 2, "unit": "UNIT"},
    ]})
    r = client.post("/api/ai-import/out/extract", headers=h(), files={"file": ("bast.docx", docx_bytes(["BAST", "Beras 300 KG"]))})
    assert r.status_code == 200, r.text
    p = r.json()
    assert seen["kind"] == "out" and "Beras 300 KG" in seen["text"] and p["read_as"] == "teks"
    assert p["valid"] is False and "belum dikenali" in p["rows"][1]["error"]
    assert p["rows"][0]["after"] == 700 and stock(client, "beras") == 1000  # nothing written yet

    rows = [{"item_id": "beras", "quantity": 300, "note": ""}, {"item_id": "selimut", "quantity": 5, "note": "ganti tenda"}]
    v = client.post("/api/ai-import/out/validate", headers=h(), json={"header": OUT_HEADER, "rows": rows}).json()
    assert v["valid"] is True
    c = client.post("/api/ai-import/out/commit", headers=h(), json={"header": OUT_HEADER, "rows": rows, "snapshot": v["snapshot"], "filename": "bast.docx"})
    assert c.status_code == 201, c.text
    saved = c.json()
    assert saved["count"] == 2 and saved["batch_source"] == "ai"
    assert stock(client, "beras") == 700 and stock(client, "selimut") == 35
    tx = client.get("/api/transactions", headers=h(), params={"batch_id": saved["batch_id"]}).json()
    assert all(t["type"] == "OUT" and t["destination"] == "Desa Sungai Tabuk" and t["recipient_kk"] == 45 for t in tx)
    # same snapshot again -> stock changed -> rejected, no double entry
    again = client.post("/api/ai-import/out/commit", headers=h(), json={"header": OUT_HEADER, "rows": rows, "snapshot": v["snapshot"]})
    assert again.status_code == 409 and stock(client, "beras") == 700


def test_out_insufficient_stock_blocks_whole_document(client):
    rows = [{"item_id": "beras", "quantity": 10}, {"item_id": "selimut", "quantity": 999}]
    v = client.post("/api/ai-import/out/validate", headers=h(), json={"header": OUT_HEADER, "rows": rows}).json()
    assert v["valid"] is False and "Stok tidak cukup" in v["rows"][1]["error"]
    c = client.post("/api/ai-import/out/commit", headers=h(), json={"header": OUT_HEADER, "rows": rows, "snapshot": v["snapshot"]})
    assert c.status_code == 400 and stock(client, "beras") == 1000


def test_unit_mismatch_is_flagged_as_warning(client):
    rows = [{"item_id": "beras", "quantity": 10, "source_unit": "KARUNG"}]
    v = client.post("/api/ai-import/out/validate", headers=h(), json={"header": OUT_HEADER, "rows": rows}).json()
    assert v["valid"] is True and "KARUNG" in v["rows"][0]["warning"]


# ---------- stock opname ----------
def test_opname_commit_sets_physical_stock_and_skips_unchanged(client, monkeypatch):
    fake_ai(monkeypatch, {"header": {"date": "2026-11-05", "ref": "BA-OPN-03"}, "rows": [
        {"item_id": "beras", "name": "Beras", "quantity": 950}, {"item_id": "mie", "name": "Mi Instan", "quantity": 85},
        {"item_id": "selimut", "name": "Selimut", "quantity": 0}]})
    p = client.post("/api/ai-import/opname/extract", headers=h("opname"), files={"file": ("opname.docx", docx_bytes(["Opname", "Beras 950"]))}).json()
    assert p["valid"] is True and [r["after"] for r in p["rows"]] == [950, 85, 0]
    rows = [{"item_id": r["item_id"], "quantity": r["quantity"], "note": r["note"]} for r in p["rows"]]
    c = client.post("/api/ai-import/opname/commit", headers=h("opname"),
                    json={"header": p["header"], "rows": rows, "snapshot": p["snapshot"], "filename": "opname.docx"})
    assert c.status_code == 201, c.text
    assert c.json()["count"] == 2 and c.json()["unchanged"] == 1
    assert (stock(client, "beras"), stock(client, "mie"), stock(client, "selimut")) == (950, 85, 0)
    tx = client.get("/api/transactions", headers=h("opname"), params={"batch_id": c.json()["batch_id"]}).json()
    assert {t["type"] for t in tx} == {"ADJUSTMENT"} and all("dibaca AI" in t["reason"] for t in tx)


def test_opname_stale_stock_rejected(client):
    rows = [{"item_id": "beras", "quantity": 900}]
    v = client.post("/api/ai-import/opname/validate", headers=h("opname"), json={"header": {}, "rows": rows}).json()
    client.portal.call(client.db.items.update_one, {"id": "beras"}, {"$inc": {"currentStock": 5}})
    c = client.post("/api/ai-import/opname/commit", headers=h("opname"), json={"header": {}, "rows": rows, "snapshot": v["snapshot"]})
    assert c.status_code == 409 and stock(client, "beras") == 1005


# ---------- access & input checks ----------
def test_roles_and_formats(client, monkeypatch):
    fake_ai(monkeypatch, {"header": {}, "rows": []})
    assert client.post("/api/ai-import/out/validate", headers=h("opname"), json={"header": {}, "rows": []}).status_code == 403
    assert client.post("/api/ai-import/out/validate", json={"header": {}, "rows": []}).status_code == 401
    bad = client.post("/api/ai-import/out/extract", headers=h(), files={"file": ("virus.exe", b"MZ")})
    assert bad.status_code == 400 and "Format tidak didukung" in bad.json()["detail"]
    status = client.get("/api/ai-import/status", headers=h("opname")).json()
    assert status["kinds"] == ["opname"] and status["ai_enabled"] is True
