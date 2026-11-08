"""Reserve stock rows while confirming a multi-row operation on standalone MongoDB."""
import uuid
from contextlib import asynccontextmanager
from fastapi import HTTPException


@asynccontextmanager
async def reserve_stock_with_token(db, rows):
    """Lock every listed item; yields (token, {item_id: doc}). Writers must match `_stock_lock: token`."""
    token = uuid.uuid4().hex
    acquired = []
    try:
        for row in sorted(rows, key=lambda r: r["item_id"]):
            query = {"id": row["item_id"], "_stock_lock": {"$exists": False}}
            if "previous_quantity" in row:
                query["currentStock"] = row["previous_quantity"]
            if "last_updated" in row:
                query["lastUpdated"] = row["last_updated"]
            doc = await db.items.find_one_and_update(query, {"$set": {"_stock_lock": token}}, projection={"_id": 0})
            if not doc:
                raise HTTPException(409, "Stok berubah, barang dihapus, atau sedang diproses. Muat ulang pratinjau.")
            acquired.append(doc)
        yield token, {i["id"]: i for i in acquired}
    finally:
        await db.items.update_many({"_stock_lock": token}, {"$unset": {"_stock_lock": ""}})


@asynccontextmanager
async def reserve_stock(db, rows):
    async with reserve_stock_with_token(db, rows) as (_, reserved):
        yield reserved
