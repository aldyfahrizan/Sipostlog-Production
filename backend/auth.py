import os
import hashlib
import uuid
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import HTTPException, Request, Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from account_limits import insert_user_with_limit

SESSION_DAYS = 7
EMERGENT_SESSION_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"

ROLE_ADMIN = "admin"
ROLE_PETUGAS = "petugas"
ROLE_OPNAME = "opname"
ROLE_PENDING = "pending"


def _now():
    return datetime.now(timezone.utc)


def is_primary_admin(doc: dict) -> bool:
    """The configured administrator (ADMIN_USERNAME / LOCAL_ADMIN_EMAIL): the account that must always keep access."""
    username = os.environ["ADMIN_USERNAME"].strip().lower()
    email = os.environ.get("LOCAL_ADMIN_EMAIL", "").strip().lower()
    return doc.get("username") == username or bool(email and str(doc.get("email", "")).strip().lower() == email)


def public_user(doc: dict) -> dict:
    return {
        "user_id": doc["user_id"],
        "email": doc["email"],
        "username": doc.get("username", ""),
        "is_primary": doc.get("username") == os.environ["ADMIN_USERNAME"].strip().lower(),
        "name": doc.get("name", ""),
        "picture": doc.get("picture", ""),
        "role": doc.get("role", ROLE_PENDING),
        "active": doc.get("active", True),
        "created_at": doc.get("created_at"),
        "last_login": doc.get("last_login"),
    }


async def exchange_session(db: AsyncIOMotorDatabase, session_id: str, response: Response) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(EMERGENT_SESSION_URL, headers={"X-Session-ID": session_id})
        if r.status_code == 200:
            data = r.json()
            if not isinstance(data, dict):
                raise ValueError("session data is not an object")
            email, token = str(data["email"]).lower(), data["session_token"]
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(status_code=503, detail="Layanan login Google sedang tidak bisa dihubungi. Coba lagi sebentar.")
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Sesi Google tidak valid")
    admin_email = os.environ.get("ADMIN_EMAIL", "").lower()

    user = await db.users.find_one({"email": email}, {"_id": 0})
    now = _now()
    if user is None:
        if await db.deleted_users.find_one({"email": email}, {"_id": 0, "email": 1}):
            raise HTTPException(status_code=403, detail="Akun telah dihapus oleh admin. Hubungi administrator.")
        user = {
            "user_id": f"user_{uuid.uuid4().hex[:12]}",
            "email": email,
            "name": data.get("name", ""),
            "picture": data.get("picture", ""),
            "role": ROLE_ADMIN if email == admin_email else ROLE_PENDING,
            "active": True,
            "created_at": now,
            "last_login": now,
        }
        await insert_user_with_limit(db, user)
    else:
        update = {"name": data.get("name", user.get("name")), "picture": data.get("picture", user.get("picture")), "last_login": now}
        if email == admin_email and user.get("role") != ROLE_ADMIN:
            update["role"] = ROLE_ADMIN
        await db.users.update_one({"user_id": user["user_id"]}, {"$set": update})
        user.update(update)

    await db.user_sessions.insert_one({
        "user_id": user["user_id"],
        "session_token": token,
        "expires_at": now + timedelta(days=SESSION_DAYS),
        "created_at": now,
    })
    response.set_cookie(
        key="session_token", value=token, httponly=True, secure=True,
        samesite="none", path="/", max_age=SESSION_DAYS * 24 * 3600,
    )
    return public_user(user)


def _token_from_request(request: Request):
    token = request.cookies.get("session_token")
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def session_query(token):
    return {"$or": [
        {"session_token": hashlib.sha256(token.encode()).hexdigest(), "auth_method": "password"},
        {"session_token": token, "auth_method": {"$ne": "password"}},
    ]}


async def get_current_user(request: Request) -> dict:
    db: AsyncIOMotorDatabase = request.app.state.db
    token = _token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Belum masuk")
    session = await db.user_sessions.find_one(session_query(token), {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Sesi tidak ditemukan")
    expires_at = session["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < _now():
        raise HTTPException(status_code=401, detail="Sesi berakhir")
    user = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Pengguna tidak ditemukan")
    if not user.get("active", True):
        raise HTTPException(status_code=403, detail="Akun dinonaktifkan")
    return public_user(user)


def require_role(*roles):
    async def dep(request: Request):
        user = await get_current_user(request)
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Akses ditolak untuk peran ini")
        return user
    return dep


async def logout(request: Request, response: Response):
    db: AsyncIOMotorDatabase = request.app.state.db
    token = _token_from_request(request)
    if token:
        await db.user_sessions.delete_one(session_query(token))
    response.delete_cookie("session_token", path="/", secure=True, samesite="none")
    return {"ok": True}
