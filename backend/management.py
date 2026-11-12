"""Inventory catalogue and administrator-managed local accounts."""
import re
import uuid
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool

from admin_login import UserResponse
from account_limits import MAX_USERS, insert_user_with_limit
from auth import is_primary_admin, public_user, require_role
from admin_actions import ReasonBody, ActionResult, audit_record, finalize_cancellation
from stock_lock import BUSY, held, reserve_stock_with_token

router = APIRouter(prefix="/api")


class ItemBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=2, max_length=80)
    unit: str = Field(min_length=1, max_length=20)
    currentStock: int = Field(default=0, ge=0, le=100000000, strict=True)
    minThreshold: int = Field(default=0, ge=0, le=100000000, strict=True)
    description: str = Field(default="", max_length=300)


class ItemResponse(ItemBody):
    id: str
    categoryId: str
    planYear: dict
    lastUpdated: datetime
    status: str


class CreateUserBody(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=6, max_length=72)
    role: str


class ResetPasswordBody(BaseModel):
    password: str = Field(min_length=6, max_length=72)


class UserCapacityResponse(BaseModel):
    total: int
    limit: int
    remaining: int


@router.get("/users/capacity", response_model=UserCapacityResponse)
async def user_capacity(request: Request, user=Depends(require_role("admin"))):
    total = await request.app.state.db.users.count_documents({})
    return {"total": total, "limit": MAX_USERS, "remaining": max(0, MAX_USERS - total)}


async def password_hash(value):
    if len(value.encode()) > 72:
        raise HTTPException(400, "Kata sandi maksimal 72 byte.")
    return (await run_in_threadpool(bcrypt.hashpw, value.encode(), bcrypt.gensalt())).decode()


@router.post("/items", response_model=ItemResponse, status_code=201)
async def create_item(body: ItemBody, request: Request, user=Depends(require_role("admin", "petugas"))):
    db = request.app.state.db
    if await db.items.find_one({"name": {"$regex": f"^{re.escape(body.name)}$", "$options": "i"}}, {"_id": 0, "id": 1}):
        raise HTTPException(409, "Nama barang sudah ada. Gunakan menu Barang Masuk untuk menambah stok.")
    now = datetime.now(timezone.utc)
    item = {**body.model_dump(), "id": f"BRG-{uuid.uuid4().hex[:8].upper()}",
            "name_key": body.name.casefold(), "categoryId": re.sub(r"[^a-z0-9]+", "-", body.category.lower()).strip("-"),
            "planYear": {}, "lastUpdated": now}
    try:
        await db.items.insert_one(dict(item))
    except DuplicateKeyError:
        raise HTTPException(409, "Nama barang sudah ada.")
    if body.currentStock:
        from server import record_transaction
        await record_transaction("IN", {**item, "currentStock": 0}, body.currentStock, user, now, source="Stok awal barang baru", notes=body.description)
    await db.catalog_audit.insert_one({"action": "CREATE", "item_id": item["id"], "user_id": user["user_id"], "at": now})
    return {**item, "status": "habis" if not item["currentStock"] else "menipis" if item["currentStock"] <= item["minThreshold"] else "aman"}


@router.delete("/items/{item_id}", response_model=ActionResult)
async def delete_item(item_id: str, body: ReasonBody, request: Request, user=Depends(require_role("admin"))):
    db = request.app.state.db
    if not await db.items.find_one({"id": item_id}, {"_id": 0, "id": 1}):
        raise HTTPException(404, "Barang tidak ditemukan.")
    async with reserve_stock_with_token(db, [{"item_id": item_id}]) as (token, reserved):
        item = reserved[item_id]
        if item["currentStock"] != 0:
            raise HTTPException(409, "Stok barang harus nol. Selesaikan pembatalan atau koreksi stok sebelum menghapus barang.")
        for cancellation in item.get("cancellation_receipts", []):
            await finalize_cancellation(db, cancellation)
        receipt = audit_record("item", item_id, item["name"], body, user)
        await db.catalog_audit.update_one({"audit_id": receipt["audit_id"]}, {"$setOnInsert": {**receipt, "snapshot": item, "state": "prepared"}}, upsert=True)
        if not (await db.items.delete_one({"id": item_id, "currentStock": 0, **held(token)})).deleted_count:
            raise HTTPException(409, BUSY)
        await db.admin_audit.update_one({"audit_id": receipt["audit_id"]}, {"$setOnInsert": receipt}, upsert=True)
        await db.catalog_audit.update_one({"audit_id": receipt["audit_id"]}, {"$set": {"state": "completed"}})
    return {"ok": True, "audit_id": receipt["audit_id"], "message": "Barang dihapus dari katalog. Riwayat dan alasan penghapusan tetap tersimpan."}


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(body: CreateUserBody, request: Request, user=Depends(require_role("admin"))):
    if body.role not in ("admin", "petugas", "opname") or len(body.name.strip()) < 2:
        raise HTTPException(400, "Nama atau peran tidak valid.")
    username = body.username.lower()
    doc = {"user_id": f"user_{uuid.uuid4().hex[:12]}", "username": username,
           "email": f"{username}@sipostlog.local", "name": body.name.strip(), "picture": "",
           "password_hash": await password_hash(body.password), "auth_method": "password", "role": body.role,
           "active": True, "created_at": datetime.now(timezone.utc), "last_login": None, "created_by": user["user_id"]}
    await insert_user_with_limit(request.app.state.db, doc)
    return public_user(doc)


@router.post("/users/{user_id}/password")
async def reset_password(user_id: str, body: ResetPasswordBody, request: Request, user=Depends(require_role("admin"))):
    db = request.app.state.db
    target = await db.users.find_one({"user_id": user_id, "auth_method": "password"}, {"_id": 0})
    if not target:
        raise HTTPException(404, "Akun lokal tidak ditemukan.")
    if is_primary_admin(target):
        raise HTTPException(400, "Akun admin utama dikelola melalui konfigurasi; ubah sandi akun petugas melalui menu ini.")
    await db.users.update_one({"user_id": user_id}, {"$set": {"password_hash": await password_hash(body.password)}})
    await db.user_sessions.delete_many({"user_id": user_id})
    return {"ok": True}