"""Batches are all-or-nothing, even when the process dies; item locks are leases that expire.

In-process (mongomock). Run: cd backend && python -m pytest tests/test_stock_integrity.py -q
"""
from datetime import datetime, timedelta, timezone

from pymongo.errors import AutoReconnect

from test_bulk_import import IN_HEADER, OUT_HEADER, auth, client, commit, preview, stock, template  # noqa: F401

import server
import stock_lock

BUSY = "Barang sedang diproses, coba lagi sebentar."


def tx_count(c):
    return c.portal.call(c.db.transactions.count_documents, {})


def lock_of(c, item_id):
    doc = c.portal.call(c.db.items.find_one, {"id": item_id})
    return doc.get("_stock_lock"), doc.get("_stock_lock_until")


def fail_second_ledger_write(monkeypatch):
    """The database drops the connection while the 2nd row is being saved."""
    real, calls = server.record_transaction, {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise AutoReconnect("connection closed")
        return await real(*args, **kwargs)

    monkeypatch.setattr(server, "record_transaction", flaky)


def die_mid_batch(c, monkeypatch):
    """A holder that stops after row 1 of a Barang Masuk batch (beras +200, mie +15) and cannot clean up."""
    content = template(c, "in", IN_HEADER, {"beras": 200, "mie": 15})
    snap = preview(c, "in", content).json()["snapshot"]
    fail_second_ledger_write(monkeypatch)

    async def unreachable(db, token):
        raise AutoReconnect("connection closed")

    monkeypatch.setattr(stock_lock, "undo_batch", unreachable)
    r = commit(c, "in", content, snap)
    assert r.status_code == 500 and "dibatalkan otomatis" in r.json()["detail"]
    monkeypatch.undo()
    assert stock(c, "beras") == 1200 and tx_count(c) == 1 and lock_of(c, "mie")[0]  # half-applied, still leased
    return content


def expire_leases(c):
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    c.portal.call(c.db.items.update_many, {"_stock_lock": {"$exists": True}}, {"$set": {"_stock_lock_until": past}})
    c.portal.call(c.db.stock_journal.update_many, {}, {"$set": {"expires_at": past}})


def restart(c):
    c.portal.call(c.db.meta.update_one, {"key": "init_fingerprint"}, {"$set": {"value": server.INIT_FINGERPRINT}}, True)
    server._init_done = False
    c.portal.call(server.ensure_initialized)


def stock_in(c, item_id="mie"):
    return c.post("/api/transactions/in", headers=auth(), json={"item_id": item_id, "quantity": 1, "source": "BNPB"})


def test_failure_on_second_row_leaves_stock_and_ledger_untouched(client, monkeypatch):
    content = template(client, "out", OUT_HEADER, {"beras": 300, "mie": 5, "selimut": 2})
    snap = preview(client, "out", content).json()["snapshot"]
    fail_second_ledger_write(monkeypatch)

    r = commit(client, "out", content, snap)

    assert r.status_code == 500 and "Tidak ada baris yang tersimpan" in r.json()["detail"]
    assert (stock(client, "beras"), stock(client, "mie"), stock(client, "selimut"), tx_count(client)) == (1000, 85, 40, 0)
    assert lock_of(client, "beras") == (None, None) and lock_of(client, "mie") == (None, None)
    journal = client.portal.call(client.db.stock_journal.find_one, {})
    assert journal["state"] == "undone" and {i["item_id"]: i["stock_before"] for i in journal["items"]} == {
        "beras": 1000, "mie": 85, "selimut": 40}


def test_reupload_after_failed_commit_applies_once(client, monkeypatch):
    content = template(client, "in", IN_HEADER, {"beras": 200, "mie": 15})
    snap = preview(client, "in", content).json()["snapshot"]
    fail_second_ledger_write(monkeypatch)
    assert commit(client, "in", content, snap).status_code == 500
    monkeypatch.undo()

    again = preview(client, "in", content).json()
    assert again["snapshot"] == snap
    assert commit(client, "in", content, again["snapshot"]).status_code == 201

    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1200, 100, 2)
    assert client.portal.call(client.db.stock_journal.count_documents, {"state": "done"}) == 1


def test_ai_opname_failure_on_second_row_saves_nothing(client, monkeypatch):
    rows = [{"item_id": "beras", "quantity": 950}, {"item_id": "mie", "quantity": 80}]
    v = client.post("/api/ai-import/opname/validate", headers=auth("opname"), json={"header": {}, "rows": rows}).json()
    fail_second_ledger_write(monkeypatch)

    r = client.post("/api/ai-import/opname/commit", headers=auth("opname"),
                    json={"header": {}, "rows": rows, "snapshot": v["snapshot"]})

    assert r.status_code == 500 and "Tidak ada baris yang tersimpan" in r.json()["detail"]
    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1000, 85, 0)
    assert lock_of(client, "beras") == (None, None)


def test_dead_holder_is_undone_by_the_next_movement(client, monkeypatch):
    die_mid_batch(client, monkeypatch)
    expire_leases(client)

    r = stock_in(client, "mie")

    assert r.status_code == 201, r.text
    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1000, 86, 1)
    assert lock_of(client, "beras") == (None, None) and lock_of(client, "mie") == (None, None)


def test_dead_holder_is_undone_at_startup_and_reupload_applies_once(client, monkeypatch):
    content = die_mid_batch(client, monkeypatch)
    expire_leases(client)

    restart(client)

    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1000, 85, 0)
    assert lock_of(client, "beras") == (None, None) and lock_of(client, "mie") == (None, None)
    again = preview(client, "in", content).json()
    assert commit(client, "in", content, again["snapshot"]).status_code == 201
    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1200, 100, 2)


def test_live_foreign_lease_is_left_alone_and_answers_409(client, monkeypatch):
    content = die_mid_batch(client, monkeypatch)  # lease not expired: the holder may still be working
    before = lock_of(client, "mie")

    restart(client)
    r = stock_in(client, "mie")
    out = client.post("/api/transactions/out", headers=auth(), json={
        "item_id": "beras", "quantity": 1, "destination": "Desa Sungai Tabuk", "incident_type": "Banjir"})
    again = preview(client, "in", content).json()

    assert (r.status_code, r.json()["detail"]) == (409, BUSY)
    assert (out.status_code, out.json()["detail"]) == (409, BUSY)
    assert commit(client, "in", content, again["snapshot"]).status_code == 409
    assert lock_of(client, "mie") == before
    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1200, 100, 1)  # untouched by others


def test_holder_that_lost_its_lease_does_not_write(client, monkeypatch):
    content = template(client, "in", IN_HEADER, {"beras": 200, "mie": 15})
    snap = preview(client, "in", content).json()["snapshot"]
    real = server.record_transaction

    async def slow(*args, **kwargs):  # the lease runs out and another writer recovers the items after row 1
        tx = await real(*args, **kwargs)
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        await client.db.items.update_many({"_stock_lock": {"$exists": True}}, {"$set": {"_stock_lock_until": past}})
        await client.db.stock_journal.update_many({}, {"$set": {"expires_at": past}})
        await stock_lock.recover_expired(client.db, ["mie"])
        return tx

    monkeypatch.setattr(server, "record_transaction", slow)

    r = commit(client, "in", content, snap)

    assert r.status_code == 409 and "Tidak ada baris yang tersimpan" in r.json()["detail"]
    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1000, 85, 0)


def test_lock_without_expiry_from_older_version_gets_one(client):
    client.portal.call(client.db.items.update_one, {"id": "mie"}, {"$set": {"_stock_lock": "legacy"}})

    assert stock_in(client, "mie").status_code == 409
    token, until = lock_of(client, "mie")
    assert token == "legacy" and until is not None

    expire_leases(client)
    assert stock_in(client, "mie").status_code == 201
    assert lock_of(client, "mie") == (None, None)


def test_quantity_above_cap_is_a_row_error(client):
    content = template(client, "in", IN_HEADER, {"beras": 1_000_000_000, "mie": 1_000_000_001, "selimut": 10 ** 19})
    data = preview(client, "in", content).json()

    errors = {r["item_id"]: r["error"] for r in data["rows"]}
    assert data["valid"] is False and data["row_errors"] == 2
    assert errors["beras"] is None and "terlalu besar" in errors["mie"] and "terlalu besar" in errors["selimut"]
    assert commit(client, "in", content, data["snapshot"]).status_code == 400

    rows = [{"item_id": "beras", "quantity": 1_000_000_001}]
    v = client.post("/api/ai-import/opname/validate", headers=auth("opname"), json={"header": {}, "rows": rows}).json()
    assert v["valid"] is False and "terlalu besar" in v["rows"][0]["error"]
    header = {"destination": "Desa Sungai Tabuk", "incident_type": "Banjir", "date": "05/11/2026", "time": "14:30"}
    v = client.post("/api/ai-import/out/validate", headers=auth(), json={"header": header, "rows": rows}).json()
    assert v["valid"] is False and "terlalu besar" in v["rows"][0]["error"]
