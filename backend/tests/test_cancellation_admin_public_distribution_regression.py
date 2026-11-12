"""Regression tests for cancellation, admin deletion controls, and public distribution payload rules.

Modules/features covered in this file:
- /api/transactions/{id}/cancel and DELETE alias with exactly-once stock reversal behavior
- /api/items and /api/users deletion permissions/reason/primary-self protections
- /api/public/distribution allowlist and cancelled OUT exclusion
"""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pymongo
import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "frontend" / ".env")
load_dotenv(ROOT / "backend" / ".env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL is required", allow_module_level=True)
if not MONGO_URL or not DB_NAME:
    pytest.skip("MONGO_URL and DB_NAME are required", allow_module_level=True)

API = f"{BASE_URL.rstrip('/')}/api"
PREFIX = f"T1ISO_{uuid.uuid4().hex[:6]}"


@pytest.fixture(scope="module")
def db():
    client = pymongo.MongoClient(MONGO_URL)
    database = client[DB_NAME]
    yield database
    client.close()


@pytest.fixture(scope="module")
def tracker():
    return {"item_ids": set(), "user_ids": set(), "tx_ids": set(), "item_names": set()}


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    res = s.post(f"{API}/auth/admin/login", json={"username": "admin", "password": "admin"}, timeout=20)
    assert res.status_code == 200, res.text
    return s


def _login(username: str, password: str = "Pass1234"):
    s = requests.Session()
    res = s.post(f"{API}/auth/admin/login", json={"username": username, "password": password}, timeout=20)
    return s, res


def _capacity(admin_s: requests.Session):
    res = admin_s.get(f"{API}/users/capacity", timeout=20)
    assert res.status_code == 200, res.text
    return res.json()


def _create_user(admin_s: requests.Session, tracker, role: str, suffix: str):
    payload = {
        "name": f"{PREFIX} {role} {suffix}",
        "username": f"{PREFIX.lower()}_{role}_{suffix}_{uuid.uuid4().hex[:4]}",
        "password": "Pass1234",
        "role": role,
    }
    res = admin_s.post(f"{API}/users", json=payload, timeout=20)
    assert res.status_code == 201, res.text
    user = res.json()
    tracker["user_ids"].add(user["user_id"])
    return user


def _create_item(admin_s: requests.Session, tracker, stock: int = 0):
    name = f"{PREFIX}_Item_{uuid.uuid4().hex[:6]}"
    payload = {
        "name": name,
        "category": "Logistik T1",
        "unit": "pak",
        "currentStock": stock,
        "minThreshold": 0,
        "description": "isolated regression item",
    }
    res = admin_s.post(f"{API}/items", json=payload, timeout=20)
    assert res.status_code == 201, res.text
    item = res.json()
    tracker["item_ids"].add(item["id"])
    tracker["item_names"].add(item["name"])
    return item


def _stock(admin_s: requests.Session, item_id: str):
    res = admin_s.get(f"{API}/items", timeout=20)
    assert res.status_code == 200, res.text
    row = next(i for i in res.json() if i["id"] == item_id)
    return row["currentStock"]


def _tx_by_id(admin_s: requests.Session, tx_id: str, status: str = "all"):
    res = admin_s.get(f"{API}/transactions", params={"status": status, "limit": 1000}, timeout=20)
    assert res.status_code == 200, res.text
    for tx in res.json():
        if tx["transaction_id"] == tx_id:
            return tx
    return None


def _item_txs(admin_s: requests.Session, item_id: str, status: str = "all"):
    res = admin_s.get(f"{API}/transactions", params={"item_id": item_id, "status": status, "limit": 1000}, timeout=20)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture(scope="module", autouse=True)
def cleanup(db, tracker):
    yield
    if tracker["user_ids"]:
        db.user_sessions.delete_many({"user_id": {"$in": list(tracker["user_ids"])}})
        db.users.delete_many({"user_id": {"$in": list(tracker["user_ids"])}})
    if tracker["item_ids"]:
        db.items.delete_many({"id": {"$in": list(tracker["item_ids"])}})
        db.transactions.delete_many({"item_id": {"$in": list(tracker["item_ids"])}})
        db.catalog_audit.delete_many({"$or": [{"entity_id": {"$in": list(tracker["item_ids"])}}, {"item_id": {"$in": list(tracker["item_ids"])}}]})
        db.admin_audit.delete_many({"$or": [{"entity_id": {"$in": list(tracker["item_ids"])}}, {"label": {"$in": list(tracker["item_names"])}}]})


def test_cancel_requires_admin_reason_and_active_only(admin_session, tracker):
    if _capacity(admin_session)["remaining"] < 1:
        pytest.skip("No free slot to create petugas for RBAC check")

    petugas = _create_user(admin_session, tracker, role="petugas", suffix="rbac")
    petugas_s, login = _login(petugas["username"])
    assert login.status_code == 200, login.text

    item = _create_item(admin_session, tracker, stock=10)
    out_res = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 2,
            "destination": f"{PREFIX} Lokasi",
            "incident_type": "Banjir",
            "notes": "",
            "recipient_kk": None,
            "recipient_jiwa": None,
        },
        timeout=20,
    )
    assert out_res.status_code == 201, out_res.text
    tx = out_res.json()
    tracker["tx_ids"].add(tx["transaction_id"])

    denied = petugas_s.post(f"{API}/transactions/{tx['transaction_id']}/cancel", json={"reason": "uji akses"}, timeout=20)
    assert denied.status_code == 403

    invalid_reason = admin_session.post(f"{API}/transactions/{tx['transaction_id']}/cancel", json={"reason": "  a "}, timeout=20)
    assert invalid_reason.status_code == 422

    ok = admin_session.post(f"{API}/transactions/{tx['transaction_id']}/cancel", json={"reason": "  koreksi input  "}, timeout=20)
    assert ok.status_code == 200, ok.text

    repeated = admin_session.post(f"{API}/transactions/{tx['transaction_id']}/cancel", json={"reason": "ulang"}, timeout=20)
    assert repeated.status_code == 409

    active_rows = _item_txs(admin_session, item["id"], status="active")
    assert all(row["transaction_id"] != tx["transaction_id"] for row in active_rows)


def test_cancel_inverse_logic_and_negative_guard(admin_session, tracker):
    item = _create_item(admin_session, tracker, stock=5)

    all_txs = _item_txs(admin_session, item["id"], status="all")
    opening_in = next(t for t in all_txs if t["type"] == "IN")

    out = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 4,
            "destination": f"{PREFIX} Posko A",
            "incident_type": "Banjir",
            "notes": "",
            "recipient_kk": 3,
            "recipient_jiwa": 10,
        },
        timeout=20,
    )
    assert out.status_code == 201, out.text
    out_tx = out.json()

    before_cancel_out = _stock(admin_session, item["id"])
    cancel_out = admin_session.post(f"{API}/transactions/{out_tx['transaction_id']}/cancel", json={"reason": "uji inverse out"}, timeout=20)
    assert cancel_out.status_code == 200, cancel_out.text
    after_cancel_out = _stock(admin_session, item["id"])
    assert after_cancel_out == before_cancel_out + 4

    adjust = admin_session.post(
        f"{API}/transactions/adjust",
        json={"item_id": item["id"], "new_quantity": 2, "reason": "uji adjust", "notes": ""},
        timeout=20,
    )
    assert adjust.status_code == 201, adjust.text
    adjust_tx = adjust.json()

    after_adjust = _stock(admin_session, item["id"])
    assert after_adjust == 2

    later_out = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 1,
            "destination": f"{PREFIX} Posko B",
            "incident_type": "Banjir",
            "notes": "",
            "recipient_kk": 1,
            "recipient_jiwa": 2,
        },
        timeout=20,
    )
    assert later_out.status_code == 201, later_out.text

    cancel_adjust = admin_session.post(f"{API}/transactions/{adjust_tx['transaction_id']}/cancel", json={"reason": "uji preserve movement"}, timeout=20)
    assert cancel_adjust.status_code == 200, cancel_adjust.text

    # ADJUST from 5 -> 2 has delta -3, cancelling should add 3 to current stock (after later movements).
    assert _stock(admin_session, item["id"]) == 4

    # Opening stock IN of +5 cannot be cancelled now because 4 - 5 would be negative.
    neg = admin_session.delete(f"{API}/transactions/{opening_in['transaction_id']}", json={"reason": "uji negatif"}, timeout=20)
    assert neg.status_code == 409
    assert _stock(admin_session, item["id"]) == 4


def test_cancel_exactly_once_sequential_and_concurrent_same_tx(admin_session, tracker):
    item = _create_item(admin_session, tracker, stock=10)
    out = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 2,
            "destination": f"{PREFIX} Gudang A",
            "incident_type": "Banjir",
            "notes": "",
            "recipient_kk": 2,
            "recipient_jiwa": 7,
        },
        timeout=20,
    )
    assert out.status_code == 201, out.text
    tx_id = out.json()["transaction_id"]

    first = admin_session.post(f"{API}/transactions/{tx_id}/cancel", json={"reason": "first cancel"}, timeout=20)
    assert first.status_code == 200, first.text
    second = admin_session.post(f"{API}/transactions/{tx_id}/cancel", json={"reason": "second cancel"}, timeout=20)
    assert second.status_code == 409

    tx_doc = _tx_by_id(admin_session, tx_id, status="all")
    assert tx_doc is not None and tx_doc.get("cancelled") is True

    all_rows = _item_txs(admin_session, item["id"], status="all")
    reversal_rows = [r for r in all_rows if r.get("reversal_of") == tx_id]
    assert len(reversal_rows) == 1

    # Concurrent same transaction: exactly one success.
    out2 = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 2,
            "destination": f"{PREFIX} Gudang B",
            "incident_type": "Banjir",
            "notes": "",
            "recipient_kk": 2,
            "recipient_jiwa": 7,
        },
        timeout=20,
    )
    assert out2.status_code == 201, out2.text
    tx2 = out2.json()["transaction_id"]
    stock_before = _stock(admin_session, item["id"])

    cookie = admin_session.cookies.get("session_token")
    assert cookie

    def attempt_cancel():
        s = requests.Session()
        return s.post(
            f"{API}/transactions/{tx2}/cancel",
            json={"reason": "concurrent once"},
            headers={"Cookie": f"session_token={cookie}"},
            timeout=20,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: attempt_cancel(), [1, 2]))

    assert sorted(statuses) == [200, 409]
    assert _stock(admin_session, item["id"]) == stock_before + 2
    rows2 = _item_txs(admin_session, item["id"], status="all")
    assert len([r for r in rows2 if r.get("reversal_of") == tx2]) == 1


def test_concurrent_distinct_cancellations_do_not_make_negative(admin_session, tracker):
    item = _create_item(admin_session, tracker, stock=0)

    in_a = admin_session.post(
        f"{API}/transactions/in",
        json={"item_id": item["id"], "quantity": 4, "source": "uji-a", "notes": ""},
        timeout=20,
    )
    in_b = admin_session.post(
        f"{API}/transactions/in",
        json={"item_id": item["id"], "quantity": 4, "source": "uji-b", "notes": ""},
        timeout=20,
    )
    assert in_a.status_code == 201 and in_b.status_code == 201

    out = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 6,
            "destination": f"{PREFIX} Posko Neg",
            "incident_type": "Banjir",
            "notes": "",
            "recipient_kk": 1,
            "recipient_jiwa": 3,
        },
        timeout=20,
    )
    assert out.status_code == 201
    assert _stock(admin_session, item["id"]) == 2

    cookie = admin_session.cookies.get("session_token")
    tx_ids = [in_a.json()["transaction_id"], in_b.json()["transaction_id"]]

    def attempt(tx_id):
        s = requests.Session()
        return s.post(
            f"{API}/transactions/{tx_id}/cancel",
            json={"reason": "uji tidak negatif"},
            headers={"Cookie": f"session_token={cookie}"},
            timeout=20,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, tx_ids))

    assert statuses.count(409) == 2
    assert _stock(admin_session, item["id"]) == 2


def test_admin_audit_and_transactions_status_access(admin_session, tracker):
    if _capacity(admin_session)["remaining"] < 2:
        pytest.skip("No free slots for petugas/opname access checks")

    petugas = _create_user(admin_session, tracker, role="petugas", suffix="status")
    opname = _create_user(admin_session, tracker, role="opname", suffix="status")
    petugas_s, p_login = _login(petugas["username"])
    opname_s, o_login = _login(opname["username"])
    assert p_login.status_code == 200 and o_login.status_code == 200

    denied1 = petugas_s.get(f"{API}/transactions", params={"status": "cancelled"}, timeout=20)
    denied2 = opname_s.get(f"{API}/transactions", params={"status": "all"}, timeout=20)
    assert denied1.status_code == 403
    assert denied2.status_code == 403

    admin_ok = admin_session.get(f"{API}/transactions", params={"status": "all"}, timeout=20)
    assert admin_ok.status_code == 200

    anon_audit = requests.get(f"{API}/admin/audit", timeout=20)
    assert anon_audit.status_code == 401
    admin_audit = admin_session.get(f"{API}/admin/audit", timeout=20)
    assert admin_audit.status_code == 200
    payload = admin_audit.text.lower()
    assert "password_hash" not in payload
    assert "session_token" not in payload


def test_all_admins_can_cancel_and_delete_with_protections(admin_session, tracker):
    if _capacity(admin_session)["remaining"] < 3:
        pytest.skip("No free slots for multi-admin permission checks")

    secondary = _create_user(admin_session, tracker, role="admin", suffix="secondary")
    tertiary = _create_user(admin_session, tracker, role="admin", suffix="tertiary")
    ordinary = _create_user(admin_session, tracker, role="petugas", suffix="ordinary")

    second_s, login_second = _login(secondary["username"])
    assert login_second.status_code == 200, login_second.text

    item = _create_item(admin_session, tracker, stock=4)
    tx = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 1,
            "destination": f"{PREFIX} MultiAdmin",
            "incident_type": "Banjir",
            "notes": "",
            "recipient_kk": 1,
            "recipient_jiwa": 1,
        },
        timeout=20,
    )
    assert tx.status_code == 201, tx.text

    cancel_by_other_admin = second_s.post(
        f"{API}/transactions/{tx.json()['transaction_id']}/cancel",
        json={"reason": "admin lain membatalkan"},
        timeout=20,
    )
    assert cancel_by_other_admin.status_code == 200, cancel_by_other_admin.text

    # Must provide reason on delete.
    missing_reason = second_s.delete(f"{API}/items/{item['id']}", timeout=20)
    assert missing_reason.status_code == 422

    # Set stock to zero then delete succeeds.
    adjust_zero = admin_session.post(
        f"{API}/transactions/adjust",
        json={"item_id": item["id"], "new_quantity": 0, "reason": "siapkan hapus", "notes": ""},
        timeout=20,
    )
    assert adjust_zero.status_code == 201, adjust_zero.text
    deleted_item = second_s.delete(f"{API}/items/{item['id']}", json={"reason": "bersih data uji"}, timeout=20)
    assert deleted_item.status_code == 200, deleted_item.text

    delete_ordinary = second_s.delete(f"{API}/users/{ordinary['user_id']}", json={"reason": "hapus akun uji"}, timeout=20)
    assert delete_ordinary.status_code == 200, delete_ordinary.text
    tracker["user_ids"].discard(ordinary["user_id"])

    delete_other_admin = second_s.delete(f"{API}/users/{tertiary['user_id']}", json={"reason": "hapus admin uji"}, timeout=20)
    assert delete_other_admin.status_code == 200, delete_other_admin.text
    tracker["user_ids"].discard(tertiary["user_id"])

    self_block = second_s.delete(f"{API}/users/{secondary['user_id']}", json={"reason": "uji self"}, timeout=20)
    assert self_block.status_code == 409

    users = admin_session.get(f"{API}/users", timeout=20)
    assert users.status_code == 200
    primary = next(u for u in users.json() if u.get("username") == "admin")
    primary_block = second_s.delete(f"{API}/users/{primary['user_id']}", json={"reason": "uji admin utama"}, timeout=20)
    assert primary_block.status_code == 409


def test_item_delete_zero_stock_and_public_private_visibility(admin_session, tracker):
    item = _create_item(admin_session, tracker, stock=1)
    blocked = admin_session.delete(f"{API}/items/{item['id']}", json={"reason": "stok belum nol"}, timeout=20)
    assert blocked.status_code == 409

    zero = admin_session.post(
        f"{API}/transactions/adjust",
        json={"item_id": item["id"], "new_quantity": 0, "reason": "nolkan untuk hapus", "notes": ""},
        timeout=20,
    )
    assert zero.status_code == 201, zero.text
    gone = admin_session.delete(f"{API}/items/{item['id']}", json={"reason": "hapus katalog uji"}, timeout=20)
    assert gone.status_code == 200, gone.text

    private_items = admin_session.get(f"{API}/items", timeout=20)
    assert private_items.status_code == 200
    assert all(i["id"] != item["id"] for i in private_items.json())

    public_items = requests.get(f"{API}/public/items", timeout=20)
    assert public_items.status_code == 200
    assert all(i["id"] != item["id"] for i in public_items.json())


def test_public_distribution_allowlist_cancelled_exclusion_and_validation(admin_session, tracker):
    item = _create_item(admin_session, tracker, stock=8)
    before = _stock(admin_session, item["id"])

    future = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=1)
    future_res = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 2,
            "destination": f"{PREFIX} Future",
            "incident_type": "Banjir",
            "date": future.strftime("%Y-%m-%d"),
            "time": future.strftime("%H:%M"),
            "recipient_kk": 1,
            "recipient_jiwa": 1,
            "notes": "",
        },
        timeout=20,
    )
    assert future_res.status_code == 400
    assert _stock(admin_session, item["id"]) == before

    bad_negative = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 1,
            "destination": f"{PREFIX} Invalid",
            "incident_type": "Banjir",
            "recipient_kk": -1,
            "recipient_jiwa": 0,
            "notes": "",
        },
        timeout=20,
    )
    assert bad_negative.status_code == 422
    assert _stock(admin_session, item["id"]) == before

    bad_decimal = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 1,
            "destination": f"{PREFIX} Invalid2",
            "incident_type": "Banjir",
            "recipient_kk": 1.5,
            "recipient_jiwa": 2,
            "notes": "",
        },
        timeout=20,
    )
    assert bad_decimal.status_code == 422
    assert _stock(admin_session, item["id"]) == before

    ok = admin_session.post(
        f"{API}/transactions/out",
        json={
            "item_id": item["id"],
            "quantity": 2,
            "destination": f"{PREFIX} Public Spot",
            "incident_type": "Banjir",
            "recipient_kk": None,
            "recipient_jiwa": 0,
            "notes": "catatan internal tidak boleh tampil publik",
        },
        timeout=20,
    )
    assert ok.status_code == 201, ok.text
    tx = ok.json()

    dist7 = requests.get(f"{API}/public/distribution", params={"days": 7}, timeout=20)
    dist14 = requests.get(f"{API}/public/distribution", params={"days": 14}, timeout=20)
    dist30 = requests.get(f"{API}/public/distribution", params={"days": 30}, timeout=20)
    assert dist7.status_code == 200 and dist14.status_code == 200 and dist30.status_code == 200
    assert dist7.json()["timezone"] == "Asia/Makassar"

    event = next((e for e in dist30.json()["events"] if e["event_id"] == tx["transaction_id"]), None)
    assert event is not None
    assert set(event.keys()) == {"event_id", "occurred_at", "location", "item_names", "incident_type", "recipient_kk", "recipient_jiwa"}
    assert event["recipient_kk"] is None
    assert event["recipient_jiwa"] == 0
    for banned in ["quantity", "change_quantity", "unit", "notes", "user_name", "beneficiary_name", "stock"]:
        assert banned not in str(event)

    cancel = admin_session.post(f"{API}/transactions/{tx['transaction_id']}/cancel", json={"reason": "sembunyikan publik"}, timeout=20)
    assert cancel.status_code == 200, cancel.text
    dist_after = requests.get(f"{API}/public/distribution", params={"days": 30}, timeout=20)
    assert dist_after.status_code == 200
    assert all(e["event_id"] != tx["transaction_id"] for e in dist_after.json()["events"])

    invalid_days = requests.get(f"{API}/public/distribution", params={"days": 9}, timeout=20)
    assert invalid_days.status_code == 422
