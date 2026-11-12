"""Database probe over a brand-new connection: GET /api/health/db.

/api/health reuses pooled connections, so it keeps answering while MongoDB refuses
every new connection. This probe opens its own client, pings, and closes it. The
result (success or failure) is cached, so callers cannot make the server open more
than one connection per minute.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pymongo import MongoClient

router = APIRouter(prefix="/api/health")
logger = logging.getLogger("sipostlog.db_probe")

CACHE_SECONDS = 60
TIMEOUT_MS = 3000

_lock = asyncio.Lock()
_cache = None  # (ok, checked_at ISO string, clock value at check time)
_clock = time.monotonic


def _ping_new_connection():
    client = MongoClient(
        os.environ["MONGO_URL"], appname="sipostlog-db-probe", serverSelectionTimeoutMS=TIMEOUT_MS,
        connectTimeoutMS=TIMEOUT_MS, socketTimeoutMS=TIMEOUT_MS,
    )
    try:
        client.admin.command("ping")
    finally:
        client.close()


@router.get("/db")
async def health_db():
    global _cache
    async with _lock:
        if _cache is None or _clock() - _cache[2] >= CACHE_SECONDS:
            try:
                await asyncio.to_thread(_ping_new_connection)
                ok = True
            except Exception as exc:
                ok = False
                # Class name only: the message can carry the connection string.
                logger.warning("Probe koneksi baru ke database gagal: %s", type(exc).__name__)
            _cache = (ok, datetime.now(timezone.utc).isoformat(), _clock())
        ok, checked_at, checked_clock = _cache
        age = max(0, int(_clock() - checked_clock))
    return JSONResponse(
        {"status": "ok" if ok else "fail", "checked_at": checked_at, "age_seconds": age},
        status_code=200 if ok else 503, headers={"Cache-Control": "no-store"},
    )
