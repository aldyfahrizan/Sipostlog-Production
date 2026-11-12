"""Local administrator access using the application's existing session store."""
import hashlib
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from starlette.concurrency import run_in_threadpool

from auth import SESSION_DAYS, public_user
from account_limits import ensure_account_slots, insert_user_with_limit


logger = logging.getLogger("sipostlog")

# Full bcrypt hash: $2a/$2b/$2y$, cost, then 53 chars of salt+digest.
BCRYPT_HASH_RE = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")
# Valid hash of a random string, used for unknown usernames so timing stays the same.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(rounds=12))
CONFIG_HINT = ("ADMIN_PASSWORD_HASH tidak valid (kemungkinan terpotong karena tanda $). "
               "Di Coolify centang 'Is Literal' pada variabel ini, tempel ulang hash lengkap, lalu redeploy.")


def bcrypt_hash_is_valid(value) -> bool:
    return isinstance(value, str) and bool(BCRYPT_HASH_RE.match(value))


def admin_password_config_ok() -> bool:
    return bcrypt_hash_is_valid(os.environ.get("ADMIN_PASSWORD_HASH", ""))


class AdminLoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=72)


class UserResponse(BaseModel):
    user_id: str
    email: str
    username: str = ""
    is_primary: bool = False
    name: str
    picture: str
    role: str
    active: bool
    created_at: datetime | None = None
    last_login: datetime | None = None


async def seed_local_admin(db):
    username = os.environ["ADMIN_USERNAME"].strip().lower()
    password_hash = os.environ["ADMIN_PASSWORD_HASH"].strip()
    if not username:
        raise RuntimeError("Konfigurasi akun admin tidak valid")
    hash_ok = bcrypt_hash_is_valid(password_hash)
    if not hash_ok:
        # Keep the app (and the admin's previous password) working instead of storing a broken hash.
        logger.error(CONFIG_HINT)
    await db.users.create_index("username", unique=True, sparse=True)
    await db.login_attempts.create_index("identifier", unique=True)
    await db.login_attempts.create_index("expires_at", expireAfterSeconds=0)
    await db.user_sessions.create_index("expires_at", expireAfterSeconds=0)
    await ensure_account_slots(db)
    existing = await db.users.find_one({"username": username}, {"_id": 0})
    if not existing and hash_ok:
        try:
            await insert_user_with_limit(db, {
                "user_id": f"user_{uuid.uuid4().hex[:12]}",
                "email": os.environ["LOCAL_ADMIN_EMAIL"],
                "username": username, "name": "Administrator SIPOSTLOG", "picture": "",
                "auth_method": "password", "role": "admin", "active": True,
                "password_hash": password_hash, "created_at": datetime.now(timezone.utc),
                "last_login": None,
            })
        except HTTPException:
            # Concurrent startup may have created the same administrator already.
            if not await db.users.find_one({"username": username}, {"_id": 0, "user_id": 1}):
                raise
    if existing and hash_ok and existing.get("password_hash") != password_hash:
        await db.users.update_one({"user_id": existing["user_id"]}, {"$set": {"password_hash": password_hash}})
        await db.user_sessions.delete_many({"user_id": existing["user_id"]})


IP_ATTEMPT_LIMIT = 5         # per username + client address
USERNAME_ATTEMPT_LIMIT = 10  # per username, all addresses together
ATTEMPT_WINDOW = timedelta(minutes=15)


def client_ip(request: Request) -> str:
    """Client address as resolved by the server (uvicorn applies X-Forwarded-For, see serve.py).

    The header itself is never read here. It is only as trustworthy as the reverse proxy in front,
    which is why the per-username ceiling does not depend on it.
    """
    return request.client.host if request.client else "unknown"


async def _count_attempt(db, identifier: str, now: datetime) -> int:
    await db.login_attempts.delete_many({"identifier": identifier, "expires_at": {"$lte": now}})
    attempt = await db.login_attempts.find_one_and_update(
        {"identifier": identifier},
        {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": now + ATTEMPT_WINDOW}},
        upsert=True, projection={"_id": 0}, return_document=ReturnDocument.AFTER,
    )
    return attempt["count"]


def _too_many_attempts() -> HTTPException:
    return HTTPException(429, "Terlalu banyak percobaan masuk. Coba lagi dalam 15 menit.", headers={"Retry-After": "900"})


async def login_admin(body: AdminLoginBody, request: Request, response: Response):
    db = request.app.state.db
    username = body.username.strip().lower()
    now = datetime.now(timezone.utc)
    # Strict limit per username + client address, so wrong guesses from elsewhere cannot lock the real owner out.
    # The looser per-username ceiling still bounds guessing spread over many (or forged) addresses.
    ip_identifier = hashlib.sha256(f"username:{username}|ip:{client_ip(request)}".encode()).hexdigest()
    identifier = hashlib.sha256(f"username:{username}".encode()).hexdigest()
    if await _count_attempt(db, ip_identifier, now) > IP_ATTEMPT_LIMIT:
        # Not counted against the username: one blocked address must not use up the shared ceiling.
        raise _too_many_attempts()
    if await _count_attempt(db, identifier, now) > USERNAME_ATTEMPT_LIMIT:
        # A username lock must not use up this address's own allowance.
        await db.login_attempts.update_one({"identifier": ip_identifier}, {"$inc": {"count": -1}})
        raise _too_many_attempts()
    user = await db.users.find_one({"username": username, "auth_method": "password"}, {"_id": 0})
    password_hash = user.get("password_hash", "") if user else _DUMMY_HASH.decode()
    if user and not bcrypt_hash_is_valid(password_hash):
        logger.error("Hash kata sandi tersimpan untuk '%s' rusak. %s", username, CONFIG_HINT)
        raise HTTPException(503, "Konfigurasi kata sandi admin di server rusak. Hubungi pengelola server (lihat log aplikasi).")
    password_bytes = body.password.encode("utf-8")
    valid = len(password_bytes) <= 72 and await run_in_threadpool(bcrypt.checkpw, password_bytes, password_hash.encode())
    if not valid or not user:
        raise HTTPException(401, "Username atau kata sandi salah.")
    if not user.get("active") or user.get("role") not in ("admin", "petugas", "opname"):
        raise HTTPException(403, "Akun nonaktif atau belum memiliki hak akses.")
    await db.login_attempts.delete_many({"identifier": {"$in": [identifier, ip_identifier]}})
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"last_login": now}})
    user["last_login"] = now
    token = secrets.token_urlsafe(32)
    await db.user_sessions.insert_one({
        "user_id": user["user_id"], "session_token": hashlib.sha256(token.encode()).hexdigest(),
        "auth_method": "password", "created_at": now, "expires_at": now + timedelta(days=SESSION_DAYS),
    })
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie("session_token", token, httponly=True, secure=True, samesite="none", path="/", max_age=SESSION_DAYS * 86400)
    return public_user(user)