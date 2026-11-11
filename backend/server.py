import os
import uuid
import asyncio
import hashlib
import logging
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env", override=False)

from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request, Response, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response as RawResponse, JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError
from pydantic import BaseModel, Field

from auth import (
    ROLE_ADMIN, ROLE_PENDING, ROLE_PETUGAS, ROLE_OPNAME, exchange_session, get_current_user, logout as do_logout, require_role, public_user,
)
from admin_login import AdminLoginBody, UserResponse, admin_password_config_ok, login_admin, seed_local_admin
from excel_utils import build_opname_template, build_workbook, parse_opname_upload
from pdf_utils import build_pdf_report
from seed_data import CATALOG_VERSION, DESTINATION_CYCLE, EXPECTED_ITEM_COUNT, INCIDENT_TYPES, INITIAL_ITEMS
from management import router as management_router
from public_catalog import router as public_catalog_router
from public_distribution import router as public_distribution_router
from distribution_utils import distribution_occurred_at
from admin_actions import router as admin_actions_router, reconcile_cancellations
from bulk_import import router as bulk_import_router
from sop import router as sop_router
from knowledge import router as knowledge_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sipostlog")

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="SIPOSTLOG API", version="2.0.0")
app.state.db = db
api = APIRouter(prefix="/api")

WAREHOUSE = (ROLE_ADMIN, ROLE_PETUGAS)
STAFF = (*WAREHOUSE, ROLE_OPNAME)
ACTIVE_TX = {"cancelled": {"$ne": True}}


def configured_origins():
    return sorted({o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()})


ALLOWED_ORIGINS = configured_origins()


def origin_allowed(request: Request, origin: str) -> bool:
    origin = origin.rstrip("/")
    if origin in ALLOWED_ORIGINS:
        return True
    hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")}
    return origin.split("://", 1)[-1] in hosts


# ---------- helpers ----------
def now_utc():
    return datetime.now(timezone.utc)


def clean(doc):
    if isinstance(doc, list):
        return [clean(d) for d in doc]
    if isinstance(doc, dict):
        return {k: clean(v) for k, v in doc.items() if k not in ("_id", "_stock_lock", "cancellation_receipts")}
    if isinstance(doc, datetime):
        if doc.tzinfo is None:
            doc = doc.replace(tzinfo=timezone.utc)
        return doc.isoformat()
    return doc


def stock_status(item) -> str:
    if item["currentStock"] <= 0:
        return "habis"
    if item["currentStock"] <= item["minThreshold"]:
        return "menipis"
    return "aman"


def parse_date(value: Optional[str], end=False) -> datetime:
    if not value:
        base = now_utc()
    else:
        try:
            d = date_type.fromisoformat(value[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")
        base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if end:
        base = base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return base


def occurred_at_from(value: Optional[str]) -> datetime:
    if not value:
        return now_utc()
    try:
        d = date_type.fromisoformat(value[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail="Format tanggal harus YYYY-MM-DD")
    now = now_utc()
    return datetime(d.year, d.month, d.day, now.hour, now.minute, now.second, tzinfo=timezone.utc)


def date_range(start: Optional[str], end: Optional[str]):
    end_dt = parse_date(end, end=True)
    start_dt = parse_date(start) if start else (end_dt - timedelta(days=30))
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="Tanggal mulai harus sebelum tanggal akhir")
    return start_dt, end_dt


async def get_item_or_404(item_id: str) -> dict:
    item = await db.items.find_one({"id": item_id}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan")
    return item


async def record_transaction(tx_type: str, before: dict, after_qty: int, user: dict, occurred_at: datetime, **extra) -> dict:
    tx = {
        "transaction_id": f"tx_{uuid.uuid4().hex[:12]}",
        "type": tx_type,
        "item_id": before["id"],
        "item_name": before["name"],
        "category": before["category"],
        "unit": before["unit"],
        "previous_quantity": before["currentStock"],
        "new_quantity": after_qty,
        "change_quantity": after_qty - before["currentStock"],
        "user_id": user["user_id"],
        "user_name": user.get("name") or user.get("email", ""),
        "role": user.get("role", ""),
        "occurred_at": occurred_at,
        "created_at": now_utc(),
        **extra,
    }
    await db.transactions.insert_one(dict(tx))
    await db.items.update_one({"id": before["id"]}, {"$set": {"lastUpdated": tx["created_at"]}})
    return clean(tx)


async def apply_stock_in(item_id: str, qty: int, user: dict, occurred_at: datetime, source: str, notes: str):
    before = await db.items.find_one_and_update(
        {"id": item_id, "_stock_lock": {"$exists": False}}, {"$inc": {"currentStock": qty}}, projection={"_id": 0}, return_document=ReturnDocument.BEFORE,
    )
    if not before:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan")
    return await record_transaction("IN", before, before["currentStock"] + qty, user, occurred_at, source=source, notes=notes)


async def apply_stock_out(item_id: str, qty: int, user: dict, occurred_at: datetime, destination: str, incident_type: str, notes: str, recipient_kk=None, recipient_jiwa=None):
    before = await db.items.find_one_and_update(
        {"id": item_id, "currentStock": {"$gte": qty}, "_stock_lock": {"$exists": False}}, {"$inc": {"currentStock": -qty}},
        projection={"_id": 0}, return_document=ReturnDocument.BEFORE,
    )
    if not before:
        item = await get_item_or_404(item_id)
        raise HTTPException(status_code=400, detail=f"Stok tidak mencukupi. Tersedia {item['currentStock']} {item['unit']}, diminta {qty} {item['unit']}")
    return await record_transaction(
        "OUT", before, before["currentStock"] - qty, user, occurred_at,
        destination=destination.strip(), incident_type=incident_type, notes=notes,
        recipient_kk=recipient_kk, recipient_jiwa=recipient_jiwa,
    )


async def apply_adjustment(item_id: str, new_qty: int, user: dict, occurred_at: datetime, reason: str, notes: str):
    before = await db.items.find_one_and_update(
        {"id": item_id, "_stock_lock": {"$exists": False}}, {"$set": {"currentStock": new_qty}}, projection={"_id": 0}, return_document=ReturnDocument.BEFORE,
    )
    if not before:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan")
    return await record_transaction("ADJUSTMENT", before, new_qty, user, occurred_at, reason=reason.strip(), notes=notes)


# ---------- models ----------
class SessionBody(BaseModel):
    session_id: str


class StockInBody(BaseModel):
    item_id: str
    quantity: int = Field(gt=0)
    source: str = Field(min_length=2, max_length=120)
    date: Optional[str] = None
    notes: str = Field(default="", max_length=500)


class StockOutBody(BaseModel):
    item_id: str
    quantity: int = Field(gt=0)
    destination: str = Field(min_length=2, max_length=120)
    incident_type: str
    date: Optional[str] = None
    time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    recipient_kk: Optional[int] = Field(default=None, ge=0, le=10000000, strict=True)
    recipient_jiwa: Optional[int] = Field(default=None, ge=0, le=10000000, strict=True)
    notes: str = Field(default="", max_length=500)


class AdjustBody(BaseModel):
    item_id: str
    new_quantity: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=300)
    date: Optional[str] = None
    notes: str = Field(default="", max_length=500)


class ItemPatch(BaseModel):
    minThreshold: Optional[int] = Field(default=None, ge=0)
    description: Optional[str] = Field(default=None, max_length=300)


class UserPatch(BaseModel):
    role: Optional[str] = None
    active: Optional[bool] = None


# ---------- public ----------
@api.get("/health")
async def health():
    count = await db.items.count_documents({})
    return {"status": "ok", "items": count, "expected": EXPECTED_ITEM_COUNT,
            "admin_password_config": "ok" if admin_password_config_ok() else "invalid", "time": now_utc().isoformat()}


# ---------- auth ----------
@api.post("/auth/admin/login", response_model=UserResponse)
@api.post("/auth/login", response_model=UserResponse)
async def auth_admin_login(body: AdminLoginBody, request: Request, response: Response):
    return await login_admin(body, request, response)


@api.post("/auth/session")
async def auth_session(body: SessionBody, response: Response):
    return clean(await exchange_session(db, body.session_id, response))


@api.get("/auth/me")
async def auth_me(user=Depends(get_current_user)):
    return clean(user)


@api.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    return await do_logout(request, response)


# ---------- items (staff) ----------
@api.get("/items")
async def list_items(year: str = "2026", user=Depends(require_role(*STAFF))):
    items = await db.items.find({}, {"_id": 0}).sort("id", 1).to_list(200)
    if year == "2027":
        return [
            {**clean(i), "currentStock": 0, "status": "rencana" if i["planYear"].get("2027", 0) > 0 else "tidak-dianggarkan", "planQuantity": i["planYear"].get("2027", 0)}
            for i in items
        ]
    return [{**clean(i), "status": stock_status(i), "planQuantity": i["planYear"].get("2026", 0)} for i in items]


@api.get("/meta/incident-types")
async def incident_types(user=Depends(require_role(*STAFF))):
    return INCIDENT_TYPES


@api.get("/meta/destinations")
async def destinations(user=Depends(require_role(*STAFF))):
    return await db.transactions.distinct("destination", {**ACTIVE_TX, "type": "OUT"})


@api.patch("/items/{item_id}")
async def patch_item(item_id: str, body: ItemPatch, user=Depends(require_role(ROLE_ADMIN))):
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="Tidak ada perubahan")
    update["lastUpdated"] = now_utc()
    item = await db.items.find_one_and_update({"id": item_id}, {"$set": update}, projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if not item:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan")
    return {**clean(item), "status": stock_status(item)}


# ---------- dashboards ----------
@api.get("/dashboard/stock")
async def dashboard_stock(year: str = "2026", user=Depends(require_role(*STAFF))):
    items = await db.items.find({}, {"_id": 0}).sort("id", 1).to_list(200)

    if year == "2027":
        categories = {}
        rencana = tidak = 0
        rows = []
        for i in items:
            plan = i["planYear"].get("2027", 0)
            st = "rencana" if plan > 0 else "tidak-dianggarkan"
            rencana += st == "rencana"
            tidak += st == "tidak-dianggarkan"
            c = categories.setdefault(i["category"], {"category": i["category"], "count": 0, "low": 0})
            c["count"] += 1
            rows.append({**clean(i), "currentStock": 0, "status": st, "planQuantity": plan})
        return {
            "year": "2027",
            "realized": False,
            "total_items": len(items),
            "status_counts": {"rencana": rencana, "tidak-dianggarkan": tidak},
            "categories": sorted(categories.values(), key=lambda c: -c["count"]),
            "items": rows,
            "total_categories": len(categories),
        }

    categories = {}
    for i in items:
        c = categories.setdefault(i["category"], {"category": i["category"], "count": 0, "low": 0})
        c["count"] += 1
        if stock_status(i) != "aman":
            c["low"] += 1
    low_items = [{**clean(i), "status": stock_status(i)} for i in items if stock_status(i) != "aman"]
    recent = await db.transactions.find(ACTIVE_TX, {"_id": 0}).sort("created_at", -1).limit(8).to_list(8)
    since = now_utc() - timedelta(days=7)
    weekly = {"IN": 0, "OUT": 0, "ADJUSTMENT": 0, "REVERSAL": 0}
    async for tx in db.transactions.find({**ACTIVE_TX, "created_at": {"$gte": since}}, {"_id": 0, "type": 1}):
        weekly[tx["type"]] = weekly.get(tx["type"], 0) + 1
    return {
        "year": "2026",
        "realized": True,
        "total_items": len(items),
        "status_counts": {s: sum(1 for i in items if stock_status(i) == s) for s in ("aman", "menipis", "habis")},
        "categories": sorted(categories.values(), key=lambda c: -c["count"]),
        "low_items": low_items,
        "items": [{**clean(i), "status": stock_status(i), "planQuantity": i["planYear"].get("2026", 0)} for i in items],
        "recent_transactions": clean(recent),
        "weekly_activity": weekly,
    }


@api.get("/items/{item_id}/history-chart")
async def item_history_chart(item_id: str, user=Depends(require_role(*STAFF))):
    item = await get_item_or_404(item_id)
    txs = await db.transactions.find({"item_id": item_id}, {"_id": 0}).sort("occurred_at", 1).to_list(500)
    points = [{"date": clean(t["occurred_at"]), "stock": t["new_quantity"], "type": t["type"]} for t in txs]
    return {"item_id": item_id, "item_name": item["name"], "unit": item["unit"], "points": points}


@api.get("/dashboard/distribution")
async def dashboard_distribution(start: Optional[str] = None, end: Optional[str] = None, user=Depends(require_role(*STAFF))):
    start_dt, end_dt = date_range(start, end)
    txs = await db.transactions.find(
        {**ACTIVE_TX, "type": "OUT", "occurred_at": {"$gte": start_dt, "$lt": end_dt}}, {"_id": 0}
    ).sort("occurred_at", -1).to_list(5000)

    per_item, per_dest, per_incident, per_day = {}, {}, {}, {}
    for tx in txs:
        qty = -tx["change_quantity"]
        pi = per_item.setdefault(tx["item_id"], {"item_id": tx["item_id"], "name": tx["item_name"], "unit": tx["unit"], "category": tx["category"], "quantity": 0, "count": 0})
        pi["quantity"] += qty
        pi["count"] += 1
        pd = per_dest.setdefault(tx["destination"], {"destination": tx["destination"], "count": 0, "items": {}})
        pd["count"] += 1
        key = f"{tx['item_name']}|{tx['unit']}"
        pd["items"].setdefault(key, {"name": tx["item_name"], "unit": tx["unit"], "quantity": 0})["quantity"] += qty
        inc = tx.get("incident_type", "Lainnya")
        per_incident[inc] = per_incident.get(inc, 0) + 1
        day = tx["occurred_at"].strftime("%Y-%m-%d") if isinstance(tx["occurred_at"], datetime) else str(tx["occurred_at"])[:10]
        per_day[day] = per_day.get(day, 0) + 1

    days = []
    cursor = start_dt
    while cursor < end_dt:
        k = cursor.strftime("%Y-%m-%d")
        days.append({"date": k, "count": per_day.get(k, 0)})
        cursor += timedelta(days=1)

    return {
        "range": {"start": start_dt.strftime("%Y-%m-%d"), "end": (end_dt - timedelta(days=1)).strftime("%Y-%m-%d")},
        "total_transactions": len(txs),
        "unique_destinations": len(per_dest),
        "unique_items": len(per_item),
        "per_item": sorted(per_item.values(), key=lambda x: -x["count"]),
        "per_destination": sorted(
            [{**d, "items": list(d["items"].values())} for d in per_dest.values()], key=lambda x: -x["count"]
        ),
        "per_incident": sorted([{"incident_type": k, "count": v} for k, v in per_incident.items()], key=lambda x: -x["count"]),
        "daily": days,
        "recent": clean(txs[:15]),
    }


# ---------- transactions ----------
@api.post("/transactions/in", status_code=201)
async def stock_in(body: StockInBody, user=Depends(require_role(*WAREHOUSE))):
    return await apply_stock_in(body.item_id, body.quantity, user, occurred_at_from(body.date), body.source.strip(), body.notes.strip())


@api.post("/transactions/out", status_code=201)
async def stock_out(body: StockOutBody, user=Depends(require_role(*WAREHOUSE))):
    if body.incident_type not in INCIDENT_TYPES:
        raise HTTPException(status_code=400, detail="Jenis kejadian tidak valid")
    if len(body.destination.strip()) < 2:
        raise HTTPException(status_code=400, detail="Lokasi penyaluran wajib diisi.")
    return await apply_stock_out(body.item_id, body.quantity, user, distribution_occurred_at(body.date, body.time), body.destination, body.incident_type, body.notes.strip(), body.recipient_kk, body.recipient_jiwa)


@api.post("/transactions/adjust", status_code=201)
async def stock_adjust(body: AdjustBody, user=Depends(require_role(*STAFF))):
    return await apply_adjustment(body.item_id, body.new_quantity, user, occurred_at_from(body.date), body.reason, body.notes.strip())


@api.get("/transactions")
async def list_transactions(
    type: Optional[str] = None, item_id: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
    batch_id: Optional[str] = None, status: str = "active", limit: int = Query(default=100, ge=1, le=1000), user=Depends(require_role(*STAFF)),
):
    if status not in ("active", "cancelled", "all"):
        raise HTTPException(422, "Status transaksi tidak valid.")
    if status != "active" and user["role"] != ROLE_ADMIN:
        raise HTTPException(403, "Riwayat pembatalan hanya dapat dilihat admin.")
    q = dict(ACTIVE_TX) if status == "active" else {"cancelled": True} if status == "cancelled" else {}
    if type:
        q["type"] = type
    if item_id:
        q["item_id"] = item_id
    if batch_id:
        q["batch_id"] = batch_id
    if (start or end) and not batch_id:
        start_dt, end_dt = date_range(start, end)
        q["occurred_at"] = {"$gte": start_dt, "$lt": end_dt}
    txs = await db.transactions.find(q, {"_id": 0}).sort("occurred_at", -1).limit(limit).to_list(limit)
    return clean(txs)


# ---------- excel ----------
def xlsx_response(content: bytes, filename: str):
    return RawResponse(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def fmt_dt(v):
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    if isinstance(v, datetime):
        return v.astimezone(timezone(timedelta(hours=8))).strftime("%d/%m/%Y %H:%M")
    return ""


STATUS_LABEL = {"aman": "Aman", "menipis": "Menipis", "habis": "Habis"}
TYPE_LABEL = {"IN": "Barang Masuk", "OUT": "Penyaluran", "ADJUSTMENT": "Koreksi", "REVERSAL": "Pembatalan"}


def pdf_response(content: bytes, filename: str):
    return RawResponse(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def now_wita():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%d/%m/%Y %H:%M WITA")


@api.get("/export/stock")
async def export_stock(user=Depends(require_role(*STAFF))):
    items = await db.items.find({}, {"_id": 0}).sort("id", 1).to_list(200)
    rows = [[i["id"], i["name"], i["category"], i["currentStock"], i["unit"], i["minThreshold"], STATUS_LABEL[stock_status(i)], fmt_dt(i.get("lastUpdated"))] for i in items]
    content = build_workbook([{
        "name": "Laporan Stok",
        "title": "Laporan Stok Logistik",
        "meta": [("Dicetak", now_wita()), ("Oleh", user["name"] or user["email"]), ("Jumlah Item", len(items))],
        "headers": ["ID", "Nama Item", "Kategori", "Stok", "Satuan", "Ambang Minimum", "Status", "Pembaruan Terakhir"],
        "rows": rows,
    }])
    return xlsx_response(content, f"SIPOSTLOG_Laporan_Stok_{datetime.now().strftime('%Y%m%d')}.xlsx")


@api.get("/export/stock/pdf")
async def export_stock_pdf(user=Depends(require_role(*STAFF))):
    items = await db.items.find({}, {"_id": 0}).sort("id", 1).to_list(200)
    rows = [[i["id"], i["name"], i["category"], f'{i["currentStock"]:,}'.replace(",", "."), i["unit"], f'{i["minThreshold"]:,}'.replace(",", "."), STATUS_LABEL[stock_status(i)]] for i in items]
    content = build_pdf_report(
        "LAPORAN STOK LOGISTIK BENCANA",
        [{
            "title": "Rekapitulasi Posisi Stok",
            "meta": [("Dicetak", now_wita()), ("Jumlah Item", str(len(items)))],
            "headers": ["ID", "Nama Item", "Kategori", "Stok", "Satuan", "Ambang Min.", "Status"],
            "rows": rows,
        }],
        user["name"] or user["email"],
    )
    return pdf_response(content, f"SIPOSTLOG_Laporan_Stok_{datetime.now().strftime('%Y%m%d')}.pdf")


@api.get("/export/distribution")
async def export_distribution(start: Optional[str] = None, end: Optional[str] = None, user=Depends(require_role(*STAFF))):
    data = await dashboard_distribution(start, end, user)
    meta = [("Periode", f"{data['range']['start']} s/d {data['range']['end']}"), ("Dicetak", now_wita()), ("Oleh", user["name"] or user["email"])]
    txs = await db.transactions.find({**ACTIVE_TX, "type": "OUT", "occurred_at": {"$gte": parse_date(data["range"]["start"]), "$lt": parse_date(data["range"]["end"], end=True)}}, {"_id": 0}).sort("occurred_at", -1).to_list(5000)
    content = build_workbook([
        {"name": "Per Item", "title": "Laporan Penyaluran per Item", "meta": meta,
         "headers": ["Nama Item", "Kategori", "Total Disalurkan", "Satuan", "Jumlah Transaksi"],
         "rows": [[p["name"], p["category"], p["quantity"], p["unit"], p["count"]] for p in data["per_item"]]},
        {"name": "Per Tujuan", "title": "Laporan Penyaluran per Tujuan", "meta": meta,
         "headers": ["Tujuan", "Nama Item", "Total Disalurkan", "Satuan"],
         "rows": [[d["destination"], it["name"], it["quantity"], it["unit"]] for d in data["per_destination"] for it in d["items"]]},
        {"name": "Rincian", "title": "Rincian Transaksi Penyaluran", "meta": meta,
         "headers": ["Waktu", "Nama Item", "Jumlah", "Satuan", "Tujuan", "Jenis Kejadian", "Petugas", "Catatan"],
         "rows": [[fmt_dt(t["occurred_at"]), t["item_name"], -t["change_quantity"], t["unit"], t.get("destination", ""), t.get("incident_type", ""), t["user_name"], t.get("notes", "")] for t in txs]},
    ])
    return xlsx_response(content, f"SIPOSTLOG_Laporan_Penyaluran_{data['range']['start']}_{data['range']['end']}.xlsx")


@api.get("/export/distribution/pdf")
async def export_distribution_pdf(start: Optional[str] = None, end: Optional[str] = None, user=Depends(require_role(*STAFF))):
    data = await dashboard_distribution(start, end, user)
    meta = [("Periode", f"{data['range']['start']} s/d {data['range']['end']}"), ("Dicetak", now_wita())]
    txs = await db.transactions.find({**ACTIVE_TX, "type": "OUT", "occurred_at": {"$gte": parse_date(data["range"]["start"]), "$lt": parse_date(data["range"]["end"], end=True)}}, {"_id": 0}).sort("occurred_at", -1).to_list(5000)
    content = build_pdf_report(
        "LAPORAN PENYALURAN LOGISTIK BENCANA",
        [
            {"title": "Rekap per Item", "meta": meta, "headers": ["Nama Item", "Kategori", "Total Disalurkan", "Satuan", "Jml Transaksi"],
             "rows": [[p["name"], p["category"], p["quantity"], p["unit"], p["count"]] for p in data["per_item"]]},
            {"title": "Rekap per Tujuan", "meta": meta, "headers": ["Tujuan", "Nama Item", "Total Disalurkan", "Satuan"],
             "rows": [[d["destination"], it["name"], it["quantity"], it["unit"]] for d in data["per_destination"] for it in d["items"]]},
            {"title": "Rincian Transaksi", "meta": meta, "headers": ["Waktu", "Nama Item", "Jumlah", "Satuan", "Tujuan", "Kejadian", "Petugas"],
             "rows": [[fmt_dt(t["occurred_at"]), t["item_name"], -t["change_quantity"], t["unit"], t.get("destination", ""), t.get("incident_type", ""), t["user_name"]] for t in txs]},
        ],
        user["name"] or user["email"],
    )
    return pdf_response(content, f"SIPOSTLOG_Laporan_Penyaluran_{data['range']['start']}_{data['range']['end']}.pdf")


@api.get("/export/transactions")
async def export_transactions(start: Optional[str] = None, end: Optional[str] = None, user=Depends(require_role(*STAFF))):
    start_dt, end_dt = date_range(start, end)
    txs = await db.transactions.find({**ACTIVE_TX, "occurred_at": {"$gte": start_dt, "$lt": end_dt}}, {"_id": 0}).sort("occurred_at", -1).to_list(10000)
    rows = [[fmt_dt(t["occurred_at"]), TYPE_LABEL.get(t["type"], t["type"]), t["item_name"], t["previous_quantity"], t["new_quantity"], t["change_quantity"], t["unit"],
             t.get("source") or t.get("destination") or "", t.get("incident_type", ""), t.get("reason", ""), t["user_name"], t.get("notes", "")] for t in txs]
    content = build_workbook([{
        "name": "Riwayat Transaksi", "title": "Riwayat Transaksi Stok",
        "meta": [("Periode", f"{start_dt.strftime('%Y-%m-%d')} s/d {(end_dt - timedelta(days=1)).strftime('%Y-%m-%d')}"), ("Dicetak", now_wita()), ("Oleh", user["name"] or user["email"])],
        "headers": ["Waktu", "Jenis", "Nama Item", "Stok Sebelum", "Stok Sesudah", "Perubahan", "Satuan", "Sumber / Tujuan", "Jenis Kejadian", "Alasan", "Petugas", "Catatan"],
        "rows": rows,
    }])
    return xlsx_response(content, f"SIPOSTLOG_Riwayat_{start_dt.strftime('%Y%m%d')}_{(end_dt - timedelta(days=1)).strftime('%Y%m%d')}.xlsx")


@api.get("/export/transactions/pdf")
async def export_transactions_pdf(start: Optional[str] = None, end: Optional[str] = None, user=Depends(require_role(*STAFF))):
    start_dt, end_dt = date_range(start, end)
    txs = await db.transactions.find({**ACTIVE_TX, "occurred_at": {"$gte": start_dt, "$lt": end_dt}}, {"_id": 0}).sort("occurred_at", -1).to_list(10000)
    rows = [[fmt_dt(t["occurred_at"]), TYPE_LABEL.get(t["type"], t["type"]), t["item_name"], t["previous_quantity"], t["new_quantity"],
             t["change_quantity"], t["unit"], t.get("source") or t.get("destination") or "", t["user_name"]] for t in txs]
    content = build_pdf_report(
        "RIWAYAT TRANSAKSI STOK LOGISTIK",
        [{
            "title": "Rincian Mutasi Stok",
            "meta": [("Periode", f"{start_dt.strftime('%Y-%m-%d')} s/d {(end_dt - timedelta(days=1)).strftime('%Y-%m-%d')}"), ("Dicetak", now_wita())],
            "headers": ["Waktu", "Jenis", "Nama Item", "Sebelum", "Sesudah", "Perubahan", "Satuan", "Sumber/Tujuan", "Petugas"],
            "rows": rows,
        }],
        user["name"] or user["email"],
    )
    return pdf_response(content, f"SIPOSTLOG_Riwayat_{start_dt.strftime('%Y%m%d')}_{(end_dt - timedelta(days=1)).strftime('%Y%m%d')}.pdf")


@api.get("/excel/template")
async def excel_template(user=Depends(require_role(*STAFF))):
    items = await db.items.find({}, {"_id": 0}).sort("id", 1).to_list(200)
    return xlsx_response(build_opname_template(items, user["name"] or user["email"]), f"SIPOSTLOG_Template_Opname_{datetime.now().strftime('%Y%m%d')}.xlsx")


@api.post("/excel/import")
async def excel_import(file: UploadFile = File(...), user=Depends(require_role(*STAFF))):
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Berkas harus berformat .xlsx")
    content = await file.read()
    try:
        entries = parse_opname_upload(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=400, detail="Berkas Excel tidak dapat dibaca")

    updated, unchanged, errors = [], [], []
    occurred = now_utc()
    for e in entries:
        item = await db.items.find_one({"id": e["item_id"]}, {"_id": 0})
        if not item:
            errors.append({"item_id": e["item_id"], "error": "ID item tidak dikenal"})
            continue
        try:
            qty = int(float(e["physical"]))
            if qty < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append({"item_id": e["item_id"], "error": f"Stok fisik tidak valid: {e['physical']}"})
            continue
        if qty == item["currentStock"]:
            unchanged.append(e["item_id"])
            continue
        reason = "Stock opname via Excel" + (f": {e['note']}" if e["note"] else "")
        tx = await apply_adjustment(item["id"], qty, user, occurred, reason, f"Import berkas {file.filename}")
        updated.append(tx)
    return {"processed": len(entries), "updated": updated, "unchanged": len(unchanged), "errors": errors}


# ---------- users (admin) ----------
@api.get("/users", response_model=list[UserResponse])
async def list_users(user=Depends(require_role(ROLE_ADMIN))):
    users = await db.users.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [public_user(u) for u in users]


@api.patch("/users/{user_id}", response_model=UserResponse)
async def patch_user(user_id: str, body: UserPatch, user=Depends(require_role(ROLE_ADMIN))):
    update = {}
    if body.role is not None:
        if body.role not in (ROLE_ADMIN, ROLE_PETUGAS, ROLE_OPNAME, ROLE_PENDING):
            raise HTTPException(status_code=400, detail="Peran tidak valid")
        update["role"] = body.role
    if body.active is not None:
        update["active"] = body.active
    if not update:
        raise HTTPException(status_code=400, detail="Tidak ada perubahan")
    if user_id == user["user_id"] and (update.get("role", ROLE_ADMIN) != ROLE_ADMIN or update.get("active") is False):
        raise HTTPException(status_code=400, detail="Tidak dapat mengubah peran/status akun sendiri")
    target = await db.users.find_one_and_update({"user_id": user_id}, {"$set": update}, projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if not target:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")
    if update.get("active") is False or update.get("role") == ROLE_PENDING:
        await db.user_sessions.delete_many({"user_id": user_id})
    return public_user(target)


app.include_router(api)
app.include_router(management_router)
app.include_router(public_catalog_router)
app.include_router(public_distribution_router)
app.include_router(admin_actions_router)
app.include_router(bulk_import_router)
app.include_router(sop_router)
app.include_router(knowledge_router)


@app.middleware("http")
async def protect_session_requests(request: Request, call_next):
    await ensure_initialized()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and not origin_allowed(request, origin):
            logger.warning("Origin rejected: %r; configured: %r", origin, ALLOWED_ORIGINS)
            return JSONResponse(status_code=403, content={"detail": "Asal permintaan tidak diizinkan"})
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


# ---------- startup ----------
SYSTEM_USER = {"user_id": "system", "name": "Sistem (data contoh)", "email": "system@sipostlog", "role": "admin"}


async def seed_2026_history():
    """Simulate 2026 realization: full procurement in, then depletion to the real opname baseline."""
    for idx, item in enumerate(INITIAL_ITEMS):
        plan = item["planYear"].get("2026", 0)
        target = item["currentStock"]
        if plan <= 0:
            continue
        await db.items.update_one({"id": item["id"]}, {"$set": {"currentStock": 0}})
        in_date = now_utc() - timedelta(days=200 - (idx % 15))
        await apply_stock_in(item["id"], plan, SYSTEM_USER, in_date, "Pengadaan APBD 2026", "Realisasi pengadaan awal tahun anggaran 2026")
        out_qty = plan - target
        if out_qty <= 0:
            continue
        dest = DESTINATION_CYCLE[idx % len(DESTINATION_CYCLE)]
        inc = INCIDENT_TYPES[idx % len(INCIDENT_TYPES)]
        if out_qty > 4:
            half = out_qty // 2
            d1 = now_utc() - timedelta(days=140 - (idx % 20))
            d2 = now_utc() - timedelta(days=50 - (idx % 15))
            await apply_stock_out(item["id"], half, SYSTEM_USER, d1, dest, inc, "Distribusi bantuan bencana 2026")
            await apply_stock_out(
                item["id"], out_qty - half, SYSTEM_USER, d2,
                DESTINATION_CYCLE[(idx + 1) % len(DESTINATION_CYCLE)], INCIDENT_TYPES[(idx + 1) % len(INCIDENT_TYPES)],
                "Distribusi bantuan bencana 2026",
            )
        else:
            d1 = now_utc() - timedelta(days=90 - (idx % 10))
            await apply_stock_out(item["id"], out_qty, SYSTEM_USER, d1, dest, inc, "Distribusi bantuan bencana 2026")
    logger.info("Seeded 2026 realization history")


SCHEMA_VERSION = 1
INIT_FINGERPRINT = hashlib.sha256(
    f"{SCHEMA_VERSION}|{CATALOG_VERSION}|{os.environ['ADMIN_USERNAME']}|{os.environ['ADMIN_PASSWORD_HASH']}".encode()
).hexdigest()
_init_lock = asyncio.Lock()
_init_done = False

if len(INITIAL_ITEMS) != EXPECTED_ITEM_COUNT:
    raise RuntimeError(f"Data integrity violation: expected {EXPECTED_ITEM_COUNT} items, got {len(INITIAL_ITEMS)}")


async def initialize_database():
    await db.items.create_index("id", unique=True)
    await db.items.create_index("name_key", unique=True, sparse=True)
    await db.transactions.create_index([("occurred_at", -1)])
    await db.transactions.create_index("type")
    await db.transactions.create_index("transaction_id", unique=True)
    await db.admin_audit.create_index("audit_id", unique=True)
    await db.transactions.create_index([("type", 1), ("occurred_at", -1)])
    await db.users.create_index("email", unique=True)
    await db.user_sessions.create_index("session_token", unique=True)
    await seed_local_admin(db)

    if not await db.meta.find_one({"key": "catalog_version"}) and await db.items.count_documents({}) == 0:
        ts = now_utc()
        try:
            await db.items.insert_many([{**i, "lastUpdated": ts} for i in INITIAL_ITEMS], ordered=False)
            logger.info("Seeded %d items", len(INITIAL_ITEMS))
        except BulkWriteError:
            logger.info("Katalog sudah diisi oleh proses lain")

    if await db.transactions.count_documents({}) == 0:
        claim = await db.meta.update_one({"key": "sample_seeded"}, {"$setOnInsert": {"at": now_utc()}}, upsert=True)
        if claim.upserted_id is not None:
            await seed_2026_history()
    await db.meta.update_one({"key": "catalog_version"}, {"$set": {"value": CATALOG_VERSION}}, upsert=True)
    await db.meta.update_one({"key": "init_fingerprint"}, {"$set": {"value": INIT_FINGERPRINT, "at": now_utc()}}, upsert=True)


async def ensure_initialized():
    """Idempotent, once-per-process bootstrap so serverless cold starts stay cheap."""
    global _init_done
    if _init_done:
        return
    async with _init_lock:
        if _init_done:
            return
        stamp = await db.meta.find_one({"key": "init_fingerprint"}, {"_id": 0, "value": 1})
        if not stamp or stamp.get("value") != INIT_FINGERPRINT:
            await initialize_database()
        await reconcile_cancellations(db)
        _init_done = True
        logger.info("Katalog aktif: %d item", await db.items.count_documents({}))


@app.on_event("startup")
async def startup():
    await ensure_initialized()


@app.on_event("shutdown")
async def shutdown():
    client.close()
