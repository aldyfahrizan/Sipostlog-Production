"""SOP page content and downloadable PDF module.

Run: cd backend && python -m pytest tests/test_sop.py -q   (needs mongomock-motor)
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "sipostlog_test")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "$2b$12$" + "x" * 53)
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@sipostlog.local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

import server  # noqa: E402
from sop_content import SECTIONS  # noqa: E402

ROLES = ["admin", "petugas", "opname", "pending"]


@pytest.fixture()
def client():
    db = AsyncMongoMockClient()["sop_test"]
    server.db = db
    server.app.state.db = db
    server._init_done = True

    async def seed():
        now = datetime.now(timezone.utc)
        for role in ROLES:
            await db.users.insert_one({"user_id": f"u_{role}", "email": f"{role}@x.id", "name": role.title(), "role": role, "active": True})
            await db.user_sessions.insert_one({"session_token": f"tok_{role}", "auth_method": "google", "user_id": f"u_{role}",
                                               "expires_at": now + timedelta(hours=1)})

    with TestClient(server.app) as c:
        c.portal.call(seed)
        yield c


def get(c, path, role):
    return c.get(path, headers={"Authorization": f"Bearer tok_{role}"})


def test_sections_follow_role(client):
    ids = {role: [s["id"] for s in get(client, "/api/sop", role).json()["sections"]] for role in ("admin", "petugas", "opname")}
    assert ids["admin"] == [s["id"] for s in SECTIONS]
    assert "impor-massal" in ids["petugas"] and "pengguna" not in ids["petugas"]
    assert "impor-massal" not in ids["opname"] and "barang-masuk" not in ids["opname"] and "opname" in ids["opname"]


def test_pending_and_anonymous_cannot_read(client):
    assert get(client, "/api/sop", "pending").status_code == 403
    assert client.get("/api/sop").status_code == 401
    assert client.get("/api/sop/pdf").status_code == 401


def test_pdf_module_downloads(client):
    r = get(client, "/api/sop/pdf", "opname")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "Modul_SOP_SIPOSTLOG" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-") and len(r.content) > 10_000


def test_every_section_has_content(client):
    for s in SECTIONS:
        assert s["roles"], s["id"]
        assert any(s.get(k) for k in ("paragraphs", "steps", "rules", "access_table")), s["id"]


def test_word_module_downloads(client):
    r = get(client, "/api/sop/docx", "petugas")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")
    assert ".docx" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"  # docx is a zip container
    import io
    from docx import Document
    text = "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)
    assert all(s["title"] in text for s in SECTIONS)
