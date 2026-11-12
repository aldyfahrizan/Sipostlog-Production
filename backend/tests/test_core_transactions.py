"""In-process tests for the core stock paths (no live server needed).

Covers single Barang Masuk, Penyaluran, Koreksi (adjust), admin cancellation with its reversal,
the Excel opname import, the role rules on every write endpoint, and the /api/health shape.

Run: cd backend && python -m pytest tests/test_core_transactions.py -q
Requires: pip install mongomock-motor pytest
"""
import io
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
from openpyxl import load_workbook  # noqa: E402
from pymongo import ReturnDocument  # noqa: E402

import server  # noqa: E402

ITEMS = [
    {"id": "beras", "name": "Beras", "category": "Pangan", "unit": "KG", "currentStock": 1000, "minThreshold": 100, "planYear": {"2026": 1000}},
    {"id": "selimut", "name": "Selimut", "category": "Sandang", "unit": "BUAH", "currentStock": 40, "minThreshold": 10, "planYear": {}},
    {"id": "mie", "name": "Mi Instan", "category": "Pangan", "unit": "DOS", "currentStock": 85, "minThreshold": 10, "planYear": {}},
]
ROLES = ("admin", "petugas", "opname", "pending")
TOKENS = {role: f"tok_{role}" for role in ROLES}

IN_BODY = {"item_id": "beras", "quantity": 200, "source": "BNPB", "date": "2026-11-05", "notes": "Karung 50kg"}
OUT_BODY = {"item_id": "beras", "quantity": 300, "destination": "Desa Sungai Tabuk", "incident_type": "Banjir",
            "date": "2026-11-05", "time": "14:30", "recipient_kk": 45, "recipient_jiwa": 160, "notes": "Tahap 1"}
ADJUST_BODY = {"item_id": "beras", "new_quantity": 940, "reason": "Hasil hitung fisik", "date": "2026-11-05"}
CANCEL_BODY = {"reason": "Salah input jumlah"}


def evaluate(expr, doc):
    """The aggregation expressions the cancellation write uses, evaluated against one document."""
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if isinstance(expr, list):
        return [evaluate(e, doc) for e in expr]
    if not isinstance(expr, dict):
        return expr
    if len(expr) == 1 and next(iter(expr)).startswith("$"):
        (op, arg), = expr.items()
        if op == "$literal":
            return arg
        args = [evaluate(a, doc) for a in arg]
        if op == "$add":
            return sum(args)
        if op == "$ifNull":
            return next((a for a in args if a is not None), None)
        if op == "$concatArrays":
            return [x for a in args for x in a]
        if op == "$mergeObjects":
            return {k: v for a in args for k, v in a.items()}
        raise NotImplementedError(op)
    return {k: evaluate(v, doc) for k, v in expr.items()}


@pytest.fixture()
def pipeline_updates(monkeypatch):
    """mongomock ignores a pipeline update that has a projection and leaves expressions inside an array
    unevaluated. MongoDB runs it as one atomic write; here it becomes a find plus a plain $set on the
    single test event loop, with the application's own filter deciding whether the document matches."""
    plain = AsyncMongoMockCollection.find_one_and_update

    async def find_one_and_update(self, query, update, *args, projection=None, **kwargs):
        if not isinstance(update, list):
            return await plain(self, query, update, *args, projection=projection, **kwargs)
        doc = await self.find_one(query)
        if doc is None:
            return None
        for stage in update:
            (op, fields), = stage.items()
            assert op == "$set", op
            doc.update({k: evaluate(v, doc) for k, v in fields.items()})  # every field reads the stage's input
        await self.update_one({"_id": doc["_id"]}, {"$set": {k: v for k, v in doc.items() if k != "_id"}})
        assert kwargs.get("return_document") == ReturnDocument.AFTER  # the only form the application uses
        return await self.find_one({"_id": doc["_id"]}, projection)

    monkeypatch.setattr(AsyncMongoMockCollection, "find_one_and_update", find_one_and_update)


@pytest.fixture()
def client(pipeline_updates):
    db = AsyncMongoMockClient()["core_test"]
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
    return {"Authorization": f"Bearer {TOKENS[role]}"} if role else {}


def stock(c, item_id="beras"):
    return c.portal.call(c.db.items.find_one, {"id": item_id})["currentStock"]


def ledger(c, **query):
    async def read():
        return await c.db.transactions.find(query, {"_id": 0}).to_list(100)
    return c.portal.call(read)


def post_ok(c, path, body, role="petugas"):
    r = c.post(path, json=body, headers=auth(role))
    assert r.status_code == 201, r.text
    return r.json()


def cancel(c, tx_id, role="admin", body=CANCEL_BODY):
    return c.post(f"/api/transactions/{tx_id}/cancel", json=body, headers=auth(role))


def opname_file(c, physical: dict, notes: dict | None = None, extra_rows=()):
    r = c.get("/api/excel/template", headers=auth("opname"))
    assert r.status_code == 200, r.text
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    for row in ws.iter_rows():
        if row[0].value in physical:
            row[5].value = physical[row[0].value]
            if notes and row[0].value in notes:
                row[6].value = notes[row[0].value]
    for extra in extra_rows:
        ws.append(list(extra))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload_opname(c, content, role="opname", filename="opname.xlsx"):
    return c.post("/api/excel/import", headers=auth(role), files={"file": (filename, content, "application/octet-stream")})


# ---------- Barang Masuk ----------
def test_in_increases_stock_and_writes_ledger_row(client):
    tx = post_ok(client, "/api/transactions/in", IN_BODY)
    assert stock(client) == 1200
    assert (tx["type"], tx["item_id"], tx["item_name"], tx["category"], tx["unit"]) == ("IN", "beras", "Beras", "Pangan", "KG")
    assert (tx["previous_quantity"], tx["new_quantity"], tx["change_quantity"]) == (1000, 1200, 200)
    assert (tx["source"], tx["notes"]) == ("BNPB", "Karung 50kg")
    assert (tx["user_id"], tx["user_name"], tx["role"]) == ("u_petugas", "Petugas", "petugas")
    assert tx["occurred_at"].startswith("2026-11-05") and tx["transaction_id"].startswith("tx_") and "_id" not in tx
    rows = ledger(client)
    assert len(rows) == 1 and rows[0]["transaction_id"] == tx["transaction_id"] and rows[0]["change_quantity"] == 200
    listed = client.get("/api/transactions", headers=auth(), params={"type": "IN", "item_id": "beras"}).json()
    assert [t["transaction_id"] for t in listed] == [tx["transaction_id"]]


@pytest.mark.parametrize("patch, status", [
    ({"quantity": 0}, 422), ({"quantity": -5}, 422), ({"quantity": 2.5}, 422), ({"source": "B"}, 422),
    ({"date": "05/11/2026"}, 400), ({"item_id": "tidak-ada"}, 404),
])
def test_in_rejects_bad_input_without_touching_stock(client, patch, status):
    r = client.post("/api/transactions/in", json={**IN_BODY, **patch}, headers=auth())
    assert r.status_code == status, r.text
    assert stock(client) == 1000 and ledger(client) == []


# ---------- Penyaluran ----------
def test_out_decreases_stock_and_records_distribution(client):
    tx = post_ok(client, "/api/transactions/out", OUT_BODY)
    assert stock(client) == 700
    assert (tx["type"], tx["previous_quantity"], tx["new_quantity"], tx["change_quantity"]) == ("OUT", 1000, 700, -300)
    assert (tx["destination"], tx["incident_type"], tx["notes"]) == ("Desa Sungai Tabuk", "Banjir", "Tahap 1")
    assert (tx["recipient_kk"], tx["recipient_jiwa"]) == (45, 160)
    assert tx["occurred_at"].startswith("2026-11-05T06:30")  # 14:30 WITA is 06:30 UTC
    assert len(ledger(client, type="OUT")) == 1


def test_out_refuses_when_stock_is_insufficient(client):
    r = client.post("/api/transactions/out", json={**OUT_BODY, "item_id": "selimut", "quantity": 41}, headers=auth())
    assert r.status_code == 400
    assert r.json()["detail"] == "Stok tidak mencukupi. Tersedia 40 BUAH, diminta 41 BUAH"
    assert stock(client, "selimut") == 40 and ledger(client) == []


def test_out_can_take_the_last_unit(client):
    tx = post_ok(client, "/api/transactions/out", {**OUT_BODY, "item_id": "selimut", "quantity": 40})
    assert tx["new_quantity"] == 0 and stock(client, "selimut") == 0


@pytest.mark.parametrize("patch, status", [
    ({"incident_type": "Gempa Mars"}, 400), ({"destination": "   "}, 400), ({"date": "2999-01-01"}, 400),
    ({"time": "25:99"}, 400), ({"time": "2pm"}, 422), ({"quantity": 0}, 422), ({"recipient_kk": -1}, 422),
    ({"recipient_jiwa": "160"}, 422), ({"item_id": "tidak-ada"}, 404),
])
def test_out_rejects_bad_input_without_touching_stock(client, patch, status):
    r = client.post("/api/transactions/out", json={**OUT_BODY, **patch}, headers=auth())
    assert r.status_code == status, r.text
    assert stock(client) == 1000 and ledger(client) == []


# ---------- Koreksi ----------
@pytest.mark.parametrize("new_quantity, change", [(940, -60), (1010, 10), (0, -1000)])
def test_adjust_sets_stock_and_records_signed_change(client, new_quantity, change):
    tx = post_ok(client, "/api/transactions/adjust", {**ADJUST_BODY, "new_quantity": new_quantity}, role="opname")
    assert stock(client) == new_quantity
    assert (tx["type"], tx["previous_quantity"], tx["new_quantity"], tx["change_quantity"]) == ("ADJUSTMENT", 1000, new_quantity, change)
    assert (tx["reason"], tx["role"], tx["user_id"]) == ("Hasil hitung fisik", "opname", "u_opname")
    assert len(ledger(client, type="ADJUSTMENT")) == 1


@pytest.mark.parametrize("patch, status", [
    ({"new_quantity": -1}, 422), ({"reason": "x"}, 422), ({"item_id": "tidak-ada"}, 404), ({"date": "kemarin"}, 400),
])
def test_adjust_rejects_bad_input_without_touching_stock(client, patch, status):
    r = client.post("/api/transactions/adjust", json={**ADJUST_BODY, **patch}, headers=auth("opname"))
    assert r.status_code == status, r.text
    assert stock(client) == 1000 and ledger(client) == []


# ---------- Pembatalan ----------
@pytest.mark.parametrize("path, body, role, after, restored_change", [
    ("/api/transactions/in", IN_BODY, "petugas", 1200, -200),
    ("/api/transactions/out", OUT_BODY, "petugas", 700, 300),
    ("/api/transactions/adjust", ADJUST_BODY, "opname", 940, 60),
])
def test_cancel_restores_stock_and_writes_reversal(client, path, body, role, after, restored_change):
    tx = post_ok(client, path, body, role)
    assert stock(client) == after
    r = cancel(client, tx["transaction_id"])
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["audit_id"] == f"cancel_transaction_{tx['transaction_id']}"
    assert stock(client) == 1000

    original = ledger(client, transaction_id=tx["transaction_id"])[0]
    assert original["cancelled"] is True and original["cancelled_by"] == "u_admin"
    assert original["cancellation_reason"] == "Salah input jumlah"
    reversal = ledger(client, type="REVERSAL")
    assert len(reversal) == 1
    rev = reversal[0]
    assert rev["transaction_id"] == original["reversal_transaction_id"] == f"reversal_{tx['transaction_id']}"
    assert rev["reversal_of"] == tx["transaction_id"] and rev["item_id"] == "beras"
    assert (rev["previous_quantity"], rev["new_quantity"], rev["change_quantity"]) == (after, 1000, restored_change)

    audit = client.get("/api/admin/audit", headers=auth("admin")).json()
    assert [(a["action"], a["entity_id"], a["original_type"], a["stock_change"]) for a in audit] == \
        [("CANCEL", tx["transaction_id"], tx["type"], restored_change)]
    # The cancelled row leaves the normal history; the reversal stays as the audit trail.
    active = client.get("/api/transactions", headers=auth()).json()
    assert [t["type"] for t in active] == ["REVERSAL"]
    cancelled = client.get("/api/transactions", headers=auth("admin"), params={"status": "cancelled"}).json()
    assert [t["transaction_id"] for t in cancelled] == [tx["transaction_id"]]


def test_cancel_twice_is_rejected_and_stock_moves_once(client):
    tx = post_ok(client, "/api/transactions/in", IN_BODY)
    assert cancel(client, tx["transaction_id"]).status_code == 200
    again = cancel(client, tx["transaction_id"])
    assert again.status_code == 409 and "sudah dibatalkan" in again.json()["detail"]
    assert stock(client) == 1000 and len(ledger(client, type="REVERSAL")) == 1


def test_cancel_via_delete_method_also_reverses(client):
    tx = post_ok(client, "/api/transactions/out", OUT_BODY)
    r = client.request("DELETE", f"/api/transactions/{tx['transaction_id']}", json=CANCEL_BODY, headers=auth("admin"))
    assert r.status_code == 200, r.text
    assert stock(client) == 1000


def test_cancel_in_that_would_make_stock_negative_is_refused(client):
    received = post_ok(client, "/api/transactions/in", {**IN_BODY, "item_id": "selimut", "quantity": 10})
    post_ok(client, "/api/transactions/out", {**OUT_BODY, "item_id": "selimut", "quantity": 45})
    r = cancel(client, received["transaction_id"])
    assert r.status_code == 409 and "stok negatif" in r.json()["detail"]
    assert stock(client, "selimut") == 5 and ledger(client, type="REVERSAL") == []
    assert ledger(client, transaction_id=received["transaction_id"])[0].get("cancelled") is not True


def test_cancel_unknown_reversal_or_unreasoned(client):
    assert cancel(client, "tx_tidak_ada").status_code == 404
    tx = post_ok(client, "/api/transactions/in", IN_BODY)
    assert cancel(client, tx["transaction_id"], body={"reason": "  x "}).status_code == 422
    assert stock(client) == 1200
    assert cancel(client, tx["transaction_id"]).status_code == 200
    reversal = cancel(client, f"reversal_{tx['transaction_id']}")
    assert reversal.status_code == 409 and "jejak audit" in reversal.json()["detail"]
    assert stock(client) == 1000


def test_cancelled_history_is_admin_only(client):
    for role in ("petugas", "opname"):
        r = client.get("/api/transactions", headers=auth(role), params={"status": "cancelled"})
        assert r.status_code == 403, role
    assert client.get("/api/transactions", headers=auth("admin"), params={"status": "apa"}).status_code == 422


# ---------- Opname Excel ----------
def test_opname_import_adjusts_only_changed_rows(client):
    content = opname_file(client, {"beras": 950, "selimut": 40, "mie": 90}, {"beras": "Karung rusak"})
    r = upload_opname(client, content)
    assert r.status_code == 200, r.text
    data = r.json()
    assert (data["processed"], data["unchanged"], data["errors"]) == (3, 1, [])
    assert {(t["item_id"], t["type"], t["change_quantity"]) for t in data["updated"]} == {("beras", "ADJUSTMENT", -50), ("mie", "ADJUSTMENT", 5)}
    beras = next(t for t in data["updated"] if t["item_id"] == "beras")
    assert beras["reason"] == "Stock opname via Excel: Karung rusak" and beras["notes"] == "Import berkas opname.xlsx"
    assert (stock(client), stock(client, "selimut"), stock(client, "mie")) == (950, 40, 90)
    assert len(ledger(client)) == 2


def test_opname_import_reports_bad_rows_and_still_applies_good_ones(client):
    content = opname_file(client, {"beras": "banyak", "selimut": -3, "mie": 80},
                          extra_rows=[("tidak-ada", "X", "", "", 0, 7, "")])
    data = upload_opname(client, content).json()
    assert data["processed"] == 4 and [t["item_id"] for t in data["updated"]] == ["mie"]
    errors = {e["item_id"]: e["error"] for e in data["errors"]}
    assert errors == {"beras": "Stok fisik tidak valid: banyak", "selimut": "Stok fisik tidak valid: -3",
                      "tidak-ada": "ID item tidak dikenal"}
    assert (stock(client), stock(client, "selimut"), stock(client, "mie")) == (1000, 40, 80)


def test_opname_import_rejects_wrong_files(client):
    content = opname_file(client, {"beras": 1})
    assert upload_opname(client, content, filename="opname.csv").status_code == 400
    broken = upload_opname(client, b"bukan excel")
    assert broken.status_code == 400 and broken.json()["detail"] == "Berkas Excel tidak dapat dibaca"
    wb = load_workbook(io.BytesIO(content))
    wb.active.delete_cols(1)
    buf = io.BytesIO()
    wb.save(buf)
    headerless = upload_opname(client, buf.getvalue())
    assert headerless.status_code == 400 and "ID Item" in headerless.json()["detail"]
    assert stock(client) == 1000 and ledger(client) == []


# ---------- Peran ----------
def write_requests(c):
    """Every stock-writing endpoint as (name, call(role) -> response, roles allowed)."""
    seeded = post_ok(c, "/api/transactions/in", {**IN_BODY, "item_id": "mie", "quantity": 1})
    return [
        ("in", lambda role: c.post("/api/transactions/in", json=IN_BODY, headers=auth(role)), {"admin", "petugas"}),
        ("out", lambda role: c.post("/api/transactions/out", json=OUT_BODY, headers=auth(role)), {"admin", "petugas"}),
        ("adjust", lambda role: c.post("/api/transactions/adjust", json=ADJUST_BODY, headers=auth(role)), {"admin", "petugas", "opname"}),
        ("excel", lambda role: upload_opname(c, opname_file(c, {"beras": 900}), role), {"admin", "petugas", "opname"}),
        ("cancel", lambda role: cancel(c, seeded["transaction_id"], role), {"admin"}),
        ("delete", lambda role: c.request("DELETE", f"/api/transactions/{seeded['transaction_id']}", json=CANCEL_BODY,
                                          headers=auth(role)), {"admin"}),
    ]


@pytest.mark.parametrize("name", ["in", "out", "adjust", "excel", "cancel", "delete"])
@pytest.mark.parametrize("role", [*ROLES, None])
def test_role_rules_on_write_endpoints(client, name, role):
    call, allowed = next((call, allowed) for n, call, allowed in write_requests(client) if n == name)
    before = {i["id"]: stock(client, i["id"]) for i in ITEMS}
    count = len(ledger(client))
    r = call(role)
    if role in allowed:
        assert r.status_code in (200, 201), r.text
        assert len(ledger(client)) == count + 1
        return
    assert r.status_code == (401 if role is None else 403), r.text
    assert r.json()["detail"] == ("Belum masuk" if role is None else "Akses ditolak untuk peran ini")
    assert {i["id"]: stock(client, i["id"]) for i in ITEMS} == before and len(ledger(client)) == count


def test_unknown_expired_and_deactivated_sessions_cannot_write(client):
    async def spoil():
        await client.db.user_sessions.update_one({"session_token": TOKENS["admin"]},
                                                 {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
        await client.db.users.update_one({"user_id": "u_petugas"}, {"$set": {"active": False}})
    client.portal.call(spoil)
    unknown = client.post("/api/transactions/in", json=IN_BODY, headers={"Authorization": "Bearer tok_palsu"})
    expired = client.post("/api/transactions/in", json=IN_BODY, headers=auth("admin"))
    inactive = client.post("/api/transactions/in", json=IN_BODY, headers=auth("petugas"))
    assert (unknown.status_code, expired.status_code, inactive.status_code) == (401, 401, 403)
    assert inactive.json()["detail"] == "Akun dinonaktifkan"
    assert stock(client) == 1000 and ledger(client) == []


@pytest.mark.parametrize("role, status", [("admin", 200), ("petugas", 200), ("opname", 200), ("pending", 403), (None, 401)])
def test_reading_stock_and_history_needs_a_staff_role(client, role, status):
    for path in ("/api/items", "/api/transactions", "/api/excel/template"):
        assert client.get(path, headers=auth(role)).status_code == status, path


# ---------- Health ----------
def test_health_is_public_and_has_the_expected_shape(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"status", "items", "expected", "admin_password_config", "ai", "time"}
    assert data["status"] == "ok" and data["items"] == len(ITEMS)
    assert data["expected"] == server.EXPECTED_ITEM_COUNT == len(server.INITIAL_ITEMS)
    assert data["admin_password_config"] in ("ok", "invalid") and isinstance(data["ai"], str)
    assert datetime.fromisoformat(data["time"]).tzinfo is not None
