"""Public distribution events: explicit allowlist, never stock amounts or private names."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from distribution_utils import WITA
from seed_data import INCIDENT_TYPES

router = APIRouter(prefix="/api/public")


class PublicDistributionEvent(BaseModel):
    event_id: str
    occurred_at: datetime
    location: str
    item_names: list[str]
    incident_type: str
    recipient_kk: int | None = Field(default=None, ge=0)
    recipient_jiwa: int | None = Field(default=None, ge=0)


class PublicIncidentSummary(BaseModel):
    incident_type: str
    events: int
    locations: int


class PublicDistributionResponse(BaseModel):
    days: int
    start_date: str
    end_date: str
    timezone: str
    incident_types: list[PublicIncidentSummary]
    events: list[PublicDistributionEvent]


@router.get("/distribution", response_model=PublicDistributionResponse)
async def public_distribution(request: Request, response: Response, days: int = Query(default=7)):
    if days not in (7, 14, 30):
        raise HTTPException(422, "Pilih rentang 7, 14, atau 30 hari.")
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(timezone.utc)
    local_today = now.astimezone(WITA).replace(hour=0, minute=0, second=0, microsecond=0)
    start = local_today - timedelta(days=days - 1)
    query = {"type": "OUT", "cancelled": {"$ne": True}, "user_id": {"$ne": "system"}, "occurred_at": {"$gte": start.astimezone(timezone.utc), "$lte": now}}
    projection = {"_id": 0, "transaction_id": 1, "occurred_at": 1, "destination": 1,
                  "item_name": 1, "recipient_kk": 1, "recipient_jiwa": 1, "batch_id": 1, "incident_type": 1}
    events, by_batch = [], {}
    async for doc in request.app.state.db.transactions.find(query, projection).sort([("occurred_at", -1), ("transaction_id", 1)]):
        batch_id = doc.get("batch_id")
        if batch_id and batch_id in by_batch:
            # One imported BAST = one event; KK/jiwa belong to the batch and are counted once.
            event = by_batch[batch_id]
            if doc["item_name"] not in event["item_names"]:
                event["item_names"].append(doc["item_name"])
            continue
        occurred = doc["occurred_at"]
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        event = {"event_id": batch_id or doc["transaction_id"], "occurred_at": occurred,
                 "location": doc.get("destination") or "Lokasi belum dicatat",
                 "item_names": [doc["item_name"]],
                 "incident_type": doc.get("incident_type") if doc.get("incident_type") in INCIDENT_TYPES else "Lainnya",
                 "recipient_kk": doc.get("recipient_kk"),
                 "recipient_jiwa": doc.get("recipient_jiwa")}
        events.append(event)
        if batch_id:
            by_batch[batch_id] = event
    # Every incident type is always listed, including those with no distribution in the period.
    summary = [{"incident_type": t, "events": sum(1 for e in events if e["incident_type"] == t),
                "locations": len({e["location"] for e in events if e["incident_type"] == t})} for t in INCIDENT_TYPES]
    return {"days": days, "start_date": start.date().isoformat(), "end_date": local_today.date().isoformat(),
            "incident_types": summary,
            "timezone": "Asia/Makassar", "events": events}