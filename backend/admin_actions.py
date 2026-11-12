"""Administrator deletion and cancellation with durable, exactly-once stock reversals."""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

from auth import public_user, require_role
from stock_lock import BUSY, UNFINISHED, recover_expired

router = APIRouter(prefix="/api")


class ReasonBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=500)


class ActionResult(BaseModel):
    ok: bool = True
    audit_id: str
    message: str


class AuditEntry(BaseModel):
    audit_id: str
    action: str
    entity_type: str
    entity_id: str
    label: str
    reason: str
    actor_id: str
    actor_name: str
    at: datetime
    stock_before: int | None = None
    stock_after: int | None = None
    stock_change: int | None = None
    original_type: str | None = None
    unit: str | None = None


def audit_record(entity_type, entity_id, label, body, user, action="DELETE"):
    return {"audit_id": f"{action.lower()}_{entity_type}_{entity_id}", "action": action,
            "entity_type": entity_type, "entity_id": entity_id, "label": label,
            "reason": body.reason, "actor_id": user["user_id"], "actor_name": user["name"],
            "at": datetime.now(timezone.utc)}


async def finalize_cancellation(db, receipt):
    tx_id = receipt["entity_id"]
    reversal_id = f"reversal_{tx_id}"
    await db.transactions.update_one({"transaction_id": tx_id}, {"$set": {
        "cancelled": True, "cancelled_at": receipt["at"], "cancelled_by": receipt["actor_id"],
        "cancellation_reason": receipt["reason"], "reversal_transaction_id": reversal_id,
    }})
    await db.admin_audit.update_one({"audit_id": receipt["audit_id"]}, {"$setOnInsert": dict(receipt)}, upsert=True)
    reversal = {"transaction_id": reversal_id, "type": "REVERSAL", "reversal_of": tx_id,
                "item_id": receipt["item_id"], "item_name": receipt["label"], "category": receipt["category"],
                "unit": receipt["unit"], "previous_quantity": receipt["stock_before"],
                "new_quantity": receipt["stock_after"], "change_quantity": receipt["stock_change"],
                "user_id": receipt["actor_id"], "user_name": receipt["actor_name"], "role": "admin",
                "occurred_at": receipt["at"], "created_at": receipt["at"], "reason": receipt["reason"],
                "notes": f"Pembatalan transaksi {tx_id}"}
    await db.transactions.update_one({"transaction_id": reversal_id}, {"$setOnInsert": reversal}, upsert=True)


async def reconcile_cancellations(db):
    # If interrupted after the atomic stock write, replay only the audit/ledger writes, never the stock change.
    async for item in db.items.find({"cancellation_receipts": {"$exists": True}}, {"_id": 0, "cancellation_receipts": 1}):
        for receipt in item["cancellation_receipts"]:
            await finalize_cancellation(db, receipt)
    # Complete audit writes if an account/item was removed before its final audit write.
    async for doc in db.deleted_users.find({}, {"_id": 0}):
        if not await db.users.find_one({"user_id": doc["user_id"]}, {"_id": 0, "user_id": 1}):
            receipt = AuditEntry.model_validate(doc).model_dump()
            await db.admin_audit.update_one({"audit_id": receipt["audit_id"]}, {"$setOnInsert": receipt}, upsert=True)
    async for doc in db.catalog_audit.find({"state": "prepared", "action": "DELETE"}, {"_id": 0}):
        item = await db.items.find_one({"id": doc["entity_id"]}, {"_id": 0, "id": 1, "currentStock": 1})
        if item is None:
            receipt = AuditEntry.model_validate(doc).model_dump()
            await db.admin_audit.update_one({"audit_id": receipt["audit_id"]}, {"$setOnInsert": receipt}, upsert=True)
            await db.catalog_audit.update_one({"audit_id": receipt["audit_id"]}, {"$set": {"state": "completed"}})


@router.post("/transactions/{transaction_id}/cancel", response_model=ActionResult)
@router.delete("/transactions/{transaction_id}", response_model=ActionResult)
async def cancel_transaction(transaction_id: str, body: ReasonBody, request: Request, user=Depends(require_role("admin"))):
    db = request.app.state.db
    tx = await db.transactions.find_one({"transaction_id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(404, "Transaksi tidak ditemukan.")
    # Recover first, then re-read: a row of a batch that never finished is removed by the recovery, not cancelled.
    await recover_expired(db, [tx["item_id"]])
    tx = await db.transactions.find_one({"transaction_id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(404, "Transaksi tidak ditemukan.")
    if tx.get("cancelled"):
        raise HTTPException(409, "Transaksi sudah dibatalkan; stok tidak diubah lagi.")
    if tx["type"] not in ("IN", "OUT", "ADJUSTMENT") or tx.get("reversal_of"):
        raise HTTPException(409, "Catatan pembatalan merupakan jejak audit dan tidak dapat dihapus.")
    if tx.get("batch_id") and await db.stock_journal.find_one(
            {"batch_id": tx["batch_id"], "state": {"$in": UNFINISHED}}, {"_id": 0, "token": 1}):
        raise HTTPException(409, BUSY)
    change = -tx["change_quantity"]
    receipt = {**audit_record("transaction", transaction_id, tx["item_name"], body, user, "CANCEL"),
               "stock_change": change, "original_type": tx["type"], "item_id": tx["item_id"],
               "category": tx["category"], "unit": tx["unit"]}
    # Receipt and quantity are written in the SAME atomic document operation.
    # Competing/repeated requests cannot apply this transaction's inverse twice.
    query = {"id": tx["item_id"], "_stock_lock": {"$exists": False},
             "currentStock": {"$gte": max(0, -change)}, "cancellation_receipts.entity_id": {"$ne": transaction_id}}
    item = await db.items.find_one_and_update(query, [{"$set": {
        "currentStock": {"$add": ["$currentStock", change]}, "lastUpdated": receipt["at"],
        "cancellation_receipts": {"$concatArrays": [{"$ifNull": ["$cancellation_receipts", []]}, [
            {"$mergeObjects": [{"$literal": receipt}, {"stock_before": "$currentStock", "stock_after": {"$add": ["$currentStock", change]}}]}
        ]]},
    }}], projection={"_id": 0, "cancellation_receipts": 1}, return_document=ReturnDocument.AFTER)
    if not item:
        existing = await db.items.find_one({"id": tx["item_id"]}, {"_id": 0, "currentStock": 1, "cancellation_receipts": 1})
        if not existing:
            raise HTTPException(409, "Barang telah dihapus dari katalog. Transaksi tidak dapat dibatalkan tanpa barang asal.")
        previous = next((r for r in existing.get("cancellation_receipts", []) if r["entity_id"] == transaction_id), None)
        if previous:
            await finalize_cancellation(db, previous)
            raise HTTPException(409, "Transaksi sudah dibatalkan; stok tidak diubah lagi.")
        raise HTTPException(409, "Pembatalan membuat stok negatif atau barang sedang diproses. Periksa transaksi berikutnya terlebih dahulu.")
    saved = next(r for r in item["cancellation_receipts"] if r["entity_id"] == transaction_id)
    await finalize_cancellation(db, saved)
    return {"ok": True, "audit_id": saved["audit_id"], "message": "Transaksi dibatalkan. Stok disesuaikan otomatis dan jejak audit disimpan."}


@router.delete("/users/{user_id}", response_model=ActionResult)
async def delete_user(user_id: str, body: ReasonBody, request: Request, user=Depends(require_role("admin"))):
    db = request.app.state.db
    if user_id == user["user_id"]:
        raise HTTPException(409, "Akun yang sedang digunakan tidak dapat dihapus.")
    target = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Pengguna tidak ditemukan.")
    if target.get("username") == os.environ["ADMIN_USERNAME"].strip().lower():
        raise HTTPException(409, "Admin utama dilindungi agar akses aplikasi tetap tersedia.")
    receipt = audit_record("user", user_id, target["name"], body, user)
    # Retain audit identity, never the password hash or session token.
    await db.deleted_users.update_one({"user_id": user_id}, {"$setOnInsert": {"snapshot": public_user(target), "email": target["email"], **receipt}}, upsert=True)
    reserved = await db.deleted_users.find_one({"user_id": user_id}, {"_id": 0})
    receipt = AuditEntry.model_validate(reserved).model_dump()
    removed = await db.users.delete_one({"user_id": user_id})
    if not removed.deleted_count:
        raise HTTPException(409, "Akun sudah dihapus.")
    await db.user_sessions.delete_many({"user_id": user_id})
    await db.admin_audit.update_one({"audit_id": receipt["audit_id"]}, {"$setOnInsert": receipt}, upsert=True)
    return {"ok": True, "audit_id": receipt["audit_id"], "message": "Akun dihapus, sesi dicabut, dan slot pengguna tersedia kembali."}


@router.get("/admin/audit", response_model=list[AuditEntry])
async def deletion_audit(request: Request, user=Depends(require_role("admin"))):
    return await request.app.state.db.admin_audit.find({}, {"_id": 0}).sort("at", -1).limit(500).to_list(500)