"""Bulk Barang Masuk / Penyaluran import: preview first, then save all rows or none."""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pymongo import ReturnDocument

from auth import ROLE_ADMIN, ROLE_PETUGAS, require_role
from bulk_excel import parse_bulk_upload, to_date, to_int, to_time
from distribution_utils import distribution_occurred_at
from seed_data import INCIDENT_TYPES
from stock_lock import LEASE_LOST, held, stock_batch

router = APIRouter(prefix="/api")
WAREHOUSE = (ROLE_ADMIN, ROLE_PETUGAS)
KIND_LABEL = {"in": "Barang Masuk", "out": "Penyaluran"}


def _kind(kind: str) -> str:
    if kind not in KIND_LABEL:
        raise HTTPException(404, "Jenis impor tidak dikenal.")
    return kind


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def validate_header(kind: str, raw: dict) -> tuple[dict, list]:
    """Return (clean header, errors)."""
    from server import occurred_at_from
    errors, header = [], {"ref": _text(raw.get("ref"))[:120]}
    if kind == "in":
        source = _text(raw.get("source"))
        if not 2 <= len(source) <= 120:
            errors.append("Sumber wajib diisi (2–120 karakter).")
        header["source"] = source
    else:
        destination = _text(raw.get("destination"))
        if not 2 <= len(destination) <= 120:
            errors.append("Lokasi Tujuan wajib diisi (2–120 karakter).")
        header["destination"] = destination
        incident = _text(raw.get("incident_type"))
        if incident not in INCIDENT_TYPES:
            errors.append(f"Jenis Kejadian tidak valid. Pilih salah satu: {', '.join(INCIDENT_TYPES)}.")
        header["incident_type"] = incident
        for key, label in (("recipient_kk", "Jumlah KK"), ("recipient_jiwa", "Jumlah Jiwa")):
            value = raw.get(key)
            if value in (None, ""):
                header[key] = None
                continue
            try:
                header[key] = to_int(value, positive=False)
                if header[key] > 10_000_000:
                    raise ValueError
            except ValueError:
                errors.append(f"{label} harus bilangan bulat 0 atau lebih.")
                header[key] = None
    try:
        day = to_date(raw.get("date"))
        header["date"] = day.isoformat()
    except ValueError as e:
        errors.append(str(e))
        header["date"] = None
    if kind == "out":
        try:
            header["time"] = to_time(raw.get("time"))
        except ValueError as e:
            errors.append(str(e))
            header["time"] = None
        if header["date"] and header["time"]:
            try:
                header["occurred_at"] = distribution_occurred_at(header["date"], header["time"])
            except HTTPException as e:
                errors.append(e.detail)
    elif header["date"]:
        header["occurred_at"] = occurred_at_from(header["date"])
    return header, errors


def validate_rows(kind: str, rows: list, items: dict) -> list:
    """Attach item info, numbers and an error message (or None) to each parsed row."""
    seen = {}
    out = []
    for r in rows:
        row = {"row": r["row"], "item_id": r["item_id"], "note": r["note"][:500], "item_name": None, "unit": None,
               "current_stock": None, "quantity": None, "after": None, "error": None}
        item = items.get(r["item_id"])
        try:
            row["quantity"] = to_int(r["quantity"], positive=True)
        except ValueError as e:
            row["error"] = str(e) or f"Jumlah harus bilangan bulat lebih dari 0 (tertulis: {r['quantity']})."
        if not item:
            row["error"] = "ID item tidak dikenal." if r["item_id"] else "ID Item kosong."
        else:
            row.update(item_name=item["name"], unit=item["unit"], current_stock=item["currentStock"])
            if r["item_id"] in seen:
                row["error"] = f"Item ganda: sudah ada di baris {seen[r['item_id']]}."
            seen.setdefault(r["item_id"], r["row"])
        if row["error"] is None:
            delta = row["quantity"] if kind == "in" else -row["quantity"]
            row["after"] = item["currentStock"] + delta
            if row["after"] < 0:
                row["error"] = f"Stok tidak cukup. Tersedia {item['currentStock']} {item['unit']}."
                row["after"] = None
        out.append(row)
    return out


async def build_preview(db, kind: str, content: bytes) -> dict:
    try:
        parsed = parse_bulk_upload(kind, content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Berkas Excel tidak dapat dibaca.")
    return await preview_from(db, kind, parsed["header"], parsed["rows"])


async def preview_from(db, kind: str, raw_header: dict, raw_rows: list) -> dict:
    """Validate a header + rows (from Excel or any other source) against live stock, without writing."""
    header, header_errors = validate_header(kind, raw_header)
    ids = list({r["item_id"] for r in raw_rows if r["item_id"]})
    items = {i["id"]: i async for i in db.items.find({"id": {"$in": ids}}, {"_id": 0})}
    rows = validate_rows(kind, raw_rows, items)
    if not rows:
        header_errors.append("Belum ada baris yang diisi jumlahnya.")
    row_errors = sum(1 for r in rows if r["error"])
    snapshot = {r["item_id"]: r["current_stock"] for r in rows if not r["error"]}
    public_header = {k: v for k, v in header.items() if k != "occurred_at"}
    if "occurred_at" in header:
        public_header["occurred_at"] = header["occurred_at"].isoformat()
    return {
        "kind": kind, "header": public_header, "header_errors": header_errors, "rows": rows,
        "row_errors": row_errors, "valid": not header_errors and row_errors == 0,
        "snapshot": snapshot, "_occurred_at": header.get("occurred_at"),
    }


async def write_batch(db, kind: str, preview: dict, user: dict, batch: dict) -> list:
    """Write every row of a valid preview while all its items are locked: all rows or none."""
    from server import record_transaction
    header, rows, occurred = preview["header"], preview["rows"], preview["_occurred_at"]
    lock_rows = [{"item_id": r["item_id"], "previous_quantity": r["current_stock"]} for r in rows]
    saved = []
    async with stock_batch(db, lock_rows, batch["batch_id"], rows) as (token, locked):
        for r in rows:  # re-check against locked documents before any write
            if locked[r["item_id"]]["currentStock"] != r["current_stock"]:
                raise HTTPException(409, "Stok berubah sejak pratinjau. Unggah ulang berkas untuk melihat angka terbaru.")
        for r in rows:
            delta = r["quantity"] if kind == "in" else -r["quantity"]
            before = await db.items.find_one_and_update(
                {"id": r["item_id"], **held(token)}, {"$inc": {"currentStock": delta}},
                projection={"_id": 0}, return_document=ReturnDocument.BEFORE,
            )
            if not before:  # the lease was lost: this holder must not write
                raise HTTPException(409, LEASE_LOST)
            if kind == "in":
                extra = {"source": header["source"], "notes": r["note"]}
            else:
                extra = {"destination": header["destination"], "incident_type": header["incident_type"], "notes": r["note"],
                         "recipient_kk": header["recipient_kk"], "recipient_jiwa": header["recipient_jiwa"]}
            tx = await record_transaction("IN" if kind == "in" else "OUT", before, before["currentStock"] + delta,
                                          user, occurred, **extra, **batch)
            saved.append(tx)
    return saved


async def _read(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "Berkas harus berformat .xlsx")
    return await file.read()


@router.get("/excel/template/{kind}")
async def bulk_template(kind: str, request: Request, user=Depends(require_role(*WAREHOUSE))):
    from bulk_excel import build_bulk_template
    from server import xlsx_response
    kind = _kind(kind)
    items = await request.app.state.db.items.find({}, {"_id": 0}).sort("id", 1).to_list(500)
    content = build_bulk_template(kind, items, user["name"] or user["email"], INCIDENT_TYPES)
    name = "Barang_Masuk" if kind == "in" else "Penyaluran"
    return xlsx_response(content, f"SIPOSTLOG_Template_{name}_{datetime.now().strftime('%Y%m%d')}.xlsx")


@router.post("/excel/import/{kind}/preview")
async def bulk_preview(kind: str, request: Request, file: UploadFile = File(...), user=Depends(require_role(*WAREHOUSE))):
    preview = await build_preview(request.app.state.db, _kind(kind), await _read(file))
    preview.pop("_occurred_at")
    return {**preview, "filename": file.filename}


@router.post("/excel/import/{kind}/commit", status_code=201)
async def bulk_commit(kind: str, request: Request, file: UploadFile = File(...), snapshot: str = Form(...),
                      user=Depends(require_role(*WAREHOUSE))):
    db = request.app.state.db
    kind = _kind(kind)
    try:
        expected = json.loads(snapshot)
        if not isinstance(expected, dict) or not all(isinstance(v, int) for v in expected.values()):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Data pratinjau tidak valid. Unggah ulang berkas.")
    preview = await build_preview(db, kind, await _read(file))
    if not preview["valid"]:
        raise HTTPException(400, "Masih ada baris yang salah. Perbaiki berkas lalu unggah ulang.")
    if preview["snapshot"] != expected:
        raise HTTPException(409, "Stok berubah sejak pratinjau. Unggah ulang berkas untuk melihat angka terbaru.")
    batch = {"batch_id": f"batch_{uuid.uuid4().hex[:12]}", "batch_ref": preview["header"]["ref"], "batch_file": file.filename}
    saved = await write_batch(db, kind, preview, user, batch)
    return {**batch, "kind": kind, "count": len(saved), "transactions": saved,
            "saved_at": datetime.now(timezone.utc).isoformat()}
