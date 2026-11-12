"""Batches are all-or-nothing, even when the process dies; item locks are leases that expire.

In-process (mongomock). Run: cd backend && python -m pytest tests/test_stock_integrity.py -q
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from mongomock_motor import AsyncMongoMockCollection as Coll
from fastapi.testclient import TestClient
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


def admin(c):
    async def seed():
        await c.db.users.insert_one({"user_id": "u_admin", "email": "a@x.id", "name": "Admin", "role": "admin", "active": True})
        await c.db.user_sessions.insert_one({"session_token": "tok_admin", "auth_method": "google", "user_id": "u_admin",
                                             "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})
    c.portal.call(seed)
    return {"Authorization": "Bearer tok_admin"}


def cancel(c, headers):
    tx = c.portal.call(c.db.transactions.find_one, {})
    return c.post(f"/api/transactions/{tx['transaction_id']}/cancel", headers=headers, json={"reason": "salah input"})


def ledger(c):
    return [(t["type"], t["change_quantity"]) for t in c.portal.call(lambda: c.db.transactions.find({}).to_list(10))]


@pytest.fixture()
def pipeline_updates(monkeypatch):
    """mongomock cannot run the pipeline update of cancel_transaction: emulate that one statement, same filter."""
    real = Coll.find_one_and_update

    async def fou(self, flt, upd, *a, **k):
        if not isinstance(upd, list):
            return await real(self, flt, upd, *a, **k)
        doc = await self.find_one(flt)
        if not doc:
            return None
        tpl = upd[0]["$set"]["cancellation_receipts"]["$concatArrays"][1][0]["$mergeObjects"][0]["$literal"]
        change = tpl["stock_change"]
        receipt = {**tpl, "stock_before": doc["currentStock"], "stock_after": doc["currentStock"] + change}
        await self.update_one({"id": doc["id"]}, {"$inc": {"currentStock": change}, "$set": {"lastUpdated": receipt["at"]},
                                                  "$push": {"cancellation_receipts": receipt}})
        return await self.find_one({"id": doc["id"]}, {"_id": 0, "cancellation_receipts": 1})

    monkeypatch.setattr(Coll, "find_one_and_update", fou)


def test_plain_cancel_still_reverses_the_stock(client, pipeline_updates):
    assert stock_in(client, "beras").status_code == 201

    r = cancel(client, admin(client))

    assert r.status_code == 200, r.text
    assert stock(client, "beras") == 1000 and ledger(client) == [("IN", 1), ("REVERSAL", -1)]


def test_cancel_of_a_dead_batch_row_undoes_the_batch_only(client, monkeypatch, pipeline_updates):
    die_mid_batch(client, monkeypatch)
    expire_leases(client)

    r = cancel(client, admin(client))

    assert (r.status_code, r.json()["detail"]) == (404, "Transaksi tidak ditemukan.")
    assert (stock(client, "beras"), stock(client, "mie"), ledger(client)) == (1000, 85, [])


def test_cancel_of_a_row_whose_batch_is_unfinished_answers_409(client, monkeypatch, pipeline_updates):
    die_mid_batch(client, monkeypatch)
    client.portal.call(client.db.items.update_many, {}, {"$unset": {"_stock_lock": "", "_stock_lock_until": ""}})

    r = cancel(client, admin(client))  # items free, journal still unfinished and not expired

    assert (r.status_code, r.json()["detail"]) == (409, BUSY)
    assert stock(client, "beras") == 1200 and ledger(client) == [("IN", 200)]


def test_undo_keeps_items_leased_until_the_ledger_rows_are_gone(client, monkeypatch, pipeline_updates):
    content = template(client, "in", IN_HEADER, {"beras": 200, "mie": 15})
    snap = preview(client, "in", content).json()["snapshot"]
    fail_second_ledger_write(monkeypatch)
    real_delete = Coll.delete_many

    async def drop(self, *a, **k):
        if self.name == "transactions":
            raise AutoReconnect("connection closed")
        return await real_delete(self, *a, **k)

    monkeypatch.setattr(Coll, "delete_many", drop)
    r = commit(client, "in", content, snap)
    monkeypatch.setattr(Coll, "delete_many", real_delete)

    assert r.status_code == 500 and "dibatalkan otomatis" in r.json()["detail"]
    assert ledger(client) == [("IN", 200)] and lock_of(client, "beras")[0] and lock_of(client, "mie")[0]
    assert cancel(client, admin(client)).status_code == 409
    expire_leases(client)
    restart(client)
    assert (stock(client, "beras"), stock(client, "mie"), ledger(client)) == (1000, 85, [])
    assert lock_of(client, "beras") == (None, None)


def test_background_sweep_undoes_a_batch_killed_just_before_the_restart(client, monkeypatch):
    die_mid_batch(client, monkeypatch)
    restart(client)  # the new process starts while the dead holder's lease is still live
    expire_leases(client)  # time passes; nobody writes to beras or mie
    assert not client.app.state.lease_sweeper.done()  # started by the start-up handler

    real, calls = server.recover_expired, []

    async def flaky(db):  # the first sweep cannot reach the database; the next one still runs
        calls.append(1)
        if len(calls) == 1:
            raise AutoReconnect("connection closed")
        await real(db)

    async def sweep_for_a_while():
        task = asyncio.create_task(server._sweep_leases())
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        task.cancel()

    assert server.LEASE_SWEEP_SECONDS == 60
    monkeypatch.setattr(server, "LEASE_SWEEP_SECONDS", 0.01)
    monkeypatch.setattr(server, "recover_expired", flaky)
    client.portal.call(sweep_for_a_while)

    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1000, 85, 0)
    assert lock_of(client, "mie") == (None, None)


def test_shutdown_stops_the_background_sweep():
    with TestClient(server.app) as c:
        sweeper = c.app.state.lease_sweeper
        assert not sweeper.done()
    assert sweeper.cancelled()


def lose_done_reply(monkeypatch):
    real = Coll.update_one

    async def ack_lost(self, flt, upd, *a, **k):
        res = await real(self, flt, upd, *a, **k)
        if self.name == "stock_journal" and upd.get("$set", {}).get("state") == "done":
            raise AutoReconnect("connection closed")  # applied by the database, reply lost
        return res

    monkeypatch.setattr(Coll, "update_one", ack_lost)


def test_committed_batch_whose_done_reply_was_lost_is_reported_as_saved(client, monkeypatch):
    content = template(client, "in", IN_HEADER, {"beras": 200, "mie": 15})
    snap = preview(client, "in", content).json()["snapshot"]
    lose_done_reply(monkeypatch)

    r = commit(client, "in", content, snap)

    assert r.status_code == 201, r.text
    assert (stock(client, "beras"), stock(client, "mie"), tx_count(client)) == (1200, 100, 2)
    assert lock_of(client, "beras") == (None, None) and lock_of(client, "mie") == (None, None)


def test_unknown_batch_outcome_does_not_say_nothing_was_saved(client, monkeypatch):
    content = template(client, "in", IN_HEADER, {"beras": 200, "mie": 15})
    snap = preview(client, "in", content).json()["snapshot"]
    lose_done_reply(monkeypatch)
    real_find = Coll.find_one

    async def down(self, *a, **k):
        if self.name == "stock_journal":
            raise AutoReconnect("connection closed")
        return await real_find(self, *a, **k)

    monkeypatch.setattr(Coll, "find_one", down)

    r = commit(client, "in", content, snap)

    assert r.status_code == 500 and "belum dapat dipastikan" in r.json()["detail"]
    assert "Tidak ada baris" not in r.json()["detail"]
