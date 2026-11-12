"""Public catalogue: deliberately excludes stock levels and availability signals."""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from public_cache import cached

router = APIRouter(prefix="/api/public")


class PublicItem(BaseModel):
    id: str
    name: str
    category: str
    unit: str


class PublicCategory(BaseModel):
    category: str
    count: int


class PublicSummary(BaseModel):
    total_items: int
    categories: list[PublicCategory]


@router.get("/items", response_model=list[PublicItem])
async def public_items(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    db = request.app.state.db
    projection = {"_id": 0, "id": 1, "name": 1, "category": 1, "unit": 1}
    return await cached(db, "items", lambda: db.items.find({}, projection).sort("name", 1).to_list(length=None))


@router.get("/summary", response_model=PublicSummary)
async def public_summary(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    db = request.app.state.db
    return await cached(db, "summary", lambda: _load_summary(db))


async def _load_summary(db):
    categories = {}
    async for item in db.items.find({}, {"_id": 0, "category": 1}):
        category = item["category"]
        categories[category] = categories.get(category, 0) + 1
    return {
        "total_items": sum(categories.values()),
        "categories": [{"category": category, "count": count} for category, count in sorted(categories.items())],
    }