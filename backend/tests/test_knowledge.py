"""Word reference documents + Tanya AI (OpenRouter is mocked; no network).

Run: cd backend && python -m pytest tests/test_knowledge.py -q   (needs mongomock-motor)
"""
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from docx import Document

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "sipostlog_test")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "$2b$12$" + "x" * 53)
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@sipostlog.local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

import knowledge  # noqa: E402
import server  # noqa: E402

ROLES = ["admin", "petugas", "opname"]


def make_docx(paragraphs, table=None) -> bytes:
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    if table:
        t = doc.add_table(rows=0, cols=len(table[0]))
        for row in table:
            cells = t.add_row().cells
            for c, v in zip(cells, row):
                c.text = v
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    db = AsyncMongoMockClient()["kb_test"]
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


def h(role):
    return {"Authorization": f"Bearer tok_{role}"}


def upload(c, role, name, content):
    return c.post("/api/sop/documents", headers=h(role), files={"file": (name, content, "application/octet-stream")})


def test_docx_text_includes_paragraphs_and_tables():
    text = knowledge.docx_to_text(make_docx(["Juknis Gudang", "Barang rusak dicatat sebagai koreksi."],
                                            table=[["Barang", "Satuan"], ["Beras", "KG"]]))
    assert "Juknis Gudang" in text and "Barang rusak" in text and "Beras | KG" in text


def test_only_admin_uploads_and_only_docx(client):
    content = make_docx(["Perbup tentang logistik kebencanaan, pasal penyaluran bantuan."])
    assert upload(client, "petugas", "perbup.docx", content).status_code == 403
    assert upload(client, "admin", "perbup.pdf", content).status_code == 400
    r = upload(client, "admin", "perbup.docx", content)
    assert r.status_code == 201 and r.json()["filename"] == "perbup.docx"
    docs = client.get("/api/sop/documents", headers=h("opname")).json()
    assert [d["filename"] for d in docs["documents"]] == ["perbup.docx"] and docs["ai_enabled"] is True
    assert "text" not in docs["documents"][0]
    assert client.delete(f"/api/sop/documents/{r.json()['doc_id']}", headers=h("admin")).status_code == 200


def test_ask_uses_relevant_uploaded_passage(client, monkeypatch):
    upload(client, "admin", "juknis-gudang.docx",
           make_docx(["Masa simpan beras di gudang maksimal enam bulan sebelum dirotasi.", "Lain-lain tidak relevan."]))
    seen = {}

    async def fake(messages):
        seen["messages"] = messages
        return "Beras disimpan maksimal enam bulan. (Sumber: juknis-gudang.docx)", "fake/model:free"

    monkeypatch.setattr(knowledge, "ask_openrouter", fake)
    r = client.post("/api/sop/ask", headers=h("petugas"), json={"question": "Berapa lama masa simpan beras di gudang?"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "enam bulan" in data["answer"] and "juknis-gudang.docx" in data["sources"]
    prompt = seen["messages"][-1]["content"]
    assert "masa simpan beras" in prompt.lower() and seen["messages"][0]["role"] == "system"


def test_ask_without_key_gives_clear_message(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    r = client.post("/api/sop/ask", headers=h("petugas"), json={"question": "Bagaimana mencatat penyaluran?"})
    assert r.status_code == 503 and "OPENROUTER_API_KEY" in r.json()["detail"]
    assert client.get("/api/health").json()["ai"] == "off"


def test_rate_limit(client, monkeypatch):
    async def fake(messages):
        return "ok", "fake/model:free"
    monkeypatch.setattr(knowledge, "ask_openrouter", fake)
    monkeypatch.setattr(knowledge, "QUESTIONS_PER_HOUR", 2)
    codes = [client.post("/api/sop/ask", headers=h("opname"), json={"question": "cara opname?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_select_context_prefers_matching_chunk():
    sources = [("A", "Penyaluran harus mencantumkan jumlah KK dan jiwa."), ("B", "Gula pasir disimpan di rak kering.")]
    picked = knowledge.select_context("jumlah KK penyaluran", sources)
    assert picked[0][0] == "A"


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def _fake_http(monkeypatch, responder):
    calls = []

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            calls.append({"url": url, "headers": headers, "json": json})
            return responder(url, json)
        async def get(self, url): raise knowledge.httpx.ConnectError("no network in tests")
    monkeypatch.setattr(knowledge.httpx, "AsyncClient", Client)
    return calls


def test_custom_openai_compatible_provider_is_used(client, monkeypatch):
    monkeypatch.setenv("AI_BASE_URL", "https://api.penyedia.example/v1/")
    monkeypatch.setenv("AI_API_KEY", "kunci-rahasia")
    monkeypatch.setenv("AI_MODEL", "model-teks")
    monkeypatch.setenv("AI_VISION_MODEL", "model-gambar")
    calls = _fake_http(monkeypatch, lambda url, body: _Resp(200, {"choices": [{"message": {"content": "siap"}}]}))
    r = client.post("/api/sop/ai-test", headers=h("admin")).json()
    assert r["provider"] == "custom" and r["ok"] is True and r["vision"]["model"] == "model-gambar"
    assert calls[0]["url"] == "https://api.penyedia.example/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer kunci-rahasia" and calls[0]["json"]["model"] == "model-teks"
    assert "kunci-rahasia" not in str(r)
    assert client.get("/api/health").json()["ai"] == "custom"
    assert client.post("/api/sop/ai-test", headers=h("petugas")).status_code == 403


def test_ai_test_reports_upstream_reason(client, monkeypatch):
    monkeypatch.setenv("AI_BASE_URL", "https://api.penyedia.example/v1")
    monkeypatch.setenv("AI_API_KEY", "salah")
    monkeypatch.setenv("AI_MODEL", "model-teks")
    _fake_http(monkeypatch, lambda url, body: _Resp(401, {"error": {"message": "Invalid API key"}}))
    r = client.post("/api/sop/ai-test", headers=h("admin")).json()
    assert r["ok"] is False and "Invalid API key" in r["text"]["detail"]
