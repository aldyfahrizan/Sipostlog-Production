"""Reserve stock rows while confirming a multi-row operation on standalone MongoDB.

A reservation is a lease: `_stock_lock` (token) + `_stock_lock_until` (expiry) on each item. A batch
also keeps a journal (`stock_journal`: its rows and every item's stock before the first write), so a
batch that did not finish, in this process or in one that died, is undone exactly: stock restored,
its ledger rows deleted. Recovery runs for the next writer that needs those items and at start-up.
"""
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

logger = logging.getLogger("sipostlog")
LEASE = timedelta(minutes=2)
BUSY = "Barang sedang diproses, coba lagi sebentar."
LEASE_LOST = "Proses terlalu lama dan dihentikan. Tidak ada baris yang tersimpan; silakan ulangi."
NOT_SAVED = "Data gagal disimpan. Tidak ada baris yang tersimpan; silakan coba lagi."
UNDO_PENDING = "Data gagal disimpan. Baris yang sempat tersimpan dibatalkan otomatis dalam beberapa menit; coba lagi setelah itu."
UNSURE = "Hasil penyimpanan belum dapat dipastikan. Periksa Riwayat sebelum mengunggah ulang."
UNFINISHED = ["open", "undoing"]
_UNLOCK = {"$unset": {"_stock_lock": "", "_stock_lock_until": ""}}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def held(token: str) -> dict:
    """Filter for a write under a lease: matches only while this holder still owns an unexpired lease."""
    return {"_stock_lock": token, "_stock_lock_until": {"$gt": _now()}}


async def undo_batch(db, token: str) -> None:
    """Undo whatever the lease holder `token` wrote (unless its batch finished) and release its items. Idempotent."""
    journal = await db.stock_journal.find_one_and_update(
        {"token": token, "state": {"$ne": "done"}}, {"$set": {"state": "undoing"}})
    if journal:
        # Ledger rows go first: the items stay leased until no row of this batch is left to cancel.
        await db.transactions.delete_many({"batch_id": journal["batch_id"]})
        for item in journal["items"]:
            restore = {"currentStock": item["stock_before"]}
            if item.get("last_updated") is not None:
                restore["lastUpdated"] = item["last_updated"]
            # restore + unlock in one document operation: the old holder cannot write to this item afterwards
            await db.items.update_one({"id": item["item_id"], "_stock_lock": token}, {"$set": restore, **_UNLOCK})
        await db.stock_journal.update_one({"token": token}, {"$set": {"state": "undone", "undone_at": _now()}})
    await db.items.update_many({"_stock_lock": token}, _UNLOCK)


async def recover_expired(db, item_ids=None) -> None:
    """Undo batches whose lease expired (dead or stuck holder). Live leases are left alone."""
    now = _now()
    items = {} if item_ids is None else {"id": {"$in": list(item_ids)}}
    journals = {} if item_ids is None else {"items.item_id": {"$in": list(item_ids)}}
    # A lock written before leases existed has no expiry: give it one instead of trusting or wiping it.
    await db.items.update_many({**items, "_stock_lock": {"$exists": True}, "_stock_lock_until": {"$exists": False}},
                               {"$set": {"_stock_lock_until": now + LEASE}})
    tokens = set(await db.items.distinct("_stock_lock", {**items, "_stock_lock_until": {"$lte": now}}))
    tokens |= set(await db.stock_journal.distinct(
        "token", {**journals, "state": {"$in": UNFINISHED}, "expires_at": {"$lte": now}}))
    for token in tokens:
        logger.warning("Memulihkan kunci stok yang kedaluwarsa (%s)", token[:8])
        await undo_batch(db, token)


async def raise_if_leased(db, item_id: str) -> None:
    """409 when the item is under a live lease; an expired lease is recovered first."""
    await recover_expired(db, [item_id])
    if await db.items.find_one({"id": item_id, "_stock_lock": {"$exists": True}}, {"_id": 0, "id": 1}):
        raise HTTPException(409, BUSY)


async def _acquire(db, rows) -> tuple[str, dict]:
    await recover_expired(db, [r["item_id"] for r in rows])
    token = uuid.uuid4().hex
    lease = {"_stock_lock": token, "_stock_lock_until": _now() + LEASE}
    acquired = []
    try:
        for row in sorted(rows, key=lambda r: r["item_id"]):
            query = {"id": row["item_id"], "_stock_lock": {"$exists": False}}
            if "previous_quantity" in row:
                query["currentStock"] = row["previous_quantity"]
            if "last_updated" in row:
                query["lastUpdated"] = row["last_updated"]
            doc = await db.items.find_one_and_update(query, {"$set": lease}, projection={"_id": 0})
            if not doc:
                raise HTTPException(409, "Stok berubah, barang dihapus, atau sedang diproses. Muat ulang pratinjau.")
            acquired.append(doc)
    except BaseException:
        await db.items.update_many({"_stock_lock": token}, _UNLOCK)
        raise
    return token, {i["id"]: i for i in acquired}


@asynccontextmanager
async def reserve_stock_with_token(db, rows):
    """Lease every listed item; yields (token, {item_id: doc}). Writers must match `held(token)`."""
    token, reserved = await _acquire(db, rows)
    try:
        yield token, reserved
    finally:
        await db.items.update_many({"_stock_lock": token}, _UNLOCK)


@asynccontextmanager
async def stock_batch(db, lock_rows, batch_id: str, rows: list):
    """Lease the items and journal the batch; unless the block completes, everything it wrote is undone.

    Ledger rows written inside must carry `batch_id`. Yields (token, {item_id: doc}); writers must match `held(token)`.
    """
    token, reserved = await _acquire(db, lock_rows)
    try:
        await db.stock_journal.insert_one({
            "token": token, "batch_id": batch_id, "state": "open", "created_at": _now(), "expires_at": _now() + LEASE,
            "rows": [{"item_id": r["item_id"], "quantity": r["quantity"]} for r in rows],
            "items": [{"item_id": d["id"], "stock_before": d["currentStock"], "last_updated": d.get("lastUpdated")}
                      for d in reserved.values()],
        })
        yield token, reserved
        finished = await db.stock_journal.update_one({"token": token, "state": "open"},
                                                     {"$set": {"state": "done", "finished_at": _now()}})
        if not finished.matched_count:  # another process judged this holder dead and undid the batch
            raise HTTPException(409, LEASE_LOST)
    except BaseException as exc:
        detail = NOT_SAVED
        try:
            await undo_batch(db, token)
        except Exception:
            # Lease and journal stay in place; the next writer or the next start-up undoes the batch.
            logger.exception("Pembatalan batch %s tertunda", batch_id)
            detail = UNDO_PENDING
        if isinstance(exc, HTTPException) or not isinstance(exc, Exception):
            raise
        if detail == NOT_SAVED:  # nothing was undone if the batch had finished and only the reply was lost
            try:
                state = (await db.stock_journal.find_one({"token": token}, {"_id": 0, "state": 1}) or {}).get("state")
            except Exception:
                logger.exception("Hasil batch %s belum dapat dipastikan", batch_id)
                raise HTTPException(500, UNSURE) from exc
            if state == "done":
                return  # the batch stands and undo_batch released the leases
        logger.exception("Batch %s gagal dan dibatalkan", batch_id)
        raise HTTPException(500, detail) from exc
    try:
        await db.items.update_many({"_stock_lock": token}, _UNLOCK)
    except Exception:
        logger.warning("Kunci stok batch %s dilepas saat masa berlakunya habis", batch_id)


@asynccontextmanager
async def reserve_stock(db, rows):
    async with reserve_stock_with_token(db, rows) as (_, reserved):
        yield reserved
