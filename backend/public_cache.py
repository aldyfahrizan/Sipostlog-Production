"""Small in-process cache for the anonymous public endpoints (bounds their database load)."""
import asyncio
import time

TTL_SECONDS = 60
_now = time.monotonic
_entries = {}  # key -> (db, expires_at, value); a handful of keys, so no eviction needed
_locks = {}


def _fresh(db, key):
    entry = _entries.get(key)
    # The entry keeps its database: a different database (tests, reconnect) never sees stale data.
    return entry if entry and entry[0] is db and entry[1] > _now() else None


async def cached(db, key, load):
    """Return load()'s result for key, reusing it for TTL_SECONDS; concurrent callers load once."""
    entry = _fresh(db, key)
    if entry:
        return entry[2]
    async with _locks.setdefault(key, asyncio.Lock()):
        entry = _fresh(db, key)
        if entry:
            return entry[2]
        value = await load()
        _entries[key] = (db, _now() + TTL_SECONDS, value)
        return value
