"""Regression checks for username + client-address lockout behind forwarded headers.

Modules/features covered:
- /api/auth/admin/login throttle sequence for one X-Forwarded-For address
- successful admin login clears only its own counter and preserves other identifiers
"""

import hashlib
import os
import uuid

import pymongo
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "frontend" / ".env")
load_dotenv(ROOT / "backend" / ".env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL not set", allow_module_level=True)
if not MONGO_URL or not DB_NAME:
    pytest.skip("MONGO_URL or DB_NAME not set", allow_module_level=True)

API = f"{BASE_URL.rstrip('/')}/api"


@pytest.fixture(scope="session")
def mongo_db():
    client = pymongo.MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    client.close()


def test_nonexistent_user_lockout_enforced_with_varying_xff_and_admin_counter_isolated(mongo_db):
    session = requests.Session()

    # Isolated nonexistent username for lockout sequence
    username = f"ghostxff_{uuid.uuid4().hex[:8]}"
    identifier = hashlib.sha256(f"username:{username}".encode()).hexdigest()

    # Additional non-admin identifier to ensure successful admin login does not clear all counters
    preserved_user = f"preserve_{uuid.uuid4().hex[:8]}"
    preserved_identifier = hashlib.sha256(f"username:{preserved_user}".encode()).hexdigest()

    # Admin identifier to confirm successful login clears only own counter
    admin_identifier = hashlib.sha256("username:admin".encode()).hexdigest()

    try:
        statuses = []
        # The strict limit is per username + client address, so the lockout sequence uses one address.
        forwarded_values = ["203.0.113.10"] * 5

        for ip in forwarded_values:
            resp = session.post(
                f"{API}/auth/admin/login",
                headers={"X-Forwarded-For": ip},
                json={"username": username, "password": "wrongpass"},
            )
            statuses.append(resp.status_code)

        sixth = session.post(
            f"{API}/auth/admin/login",
            headers={"X-Forwarded-For": "203.0.113.10"},
            json={"username": username, "password": "wrongpass"},
        )

        assert statuses == [401, 401, 401, 401, 401]
        assert sixth.status_code == 429

        # Create a different account counter that must survive successful admin login
        preserve_attempt = session.post(
            f"{API}/auth/admin/login",
            json={"username": preserved_user, "password": "wrongpass"},
        )
        assert preserve_attempt.status_code == 401

        # Create admin own failed attempt, then ensure successful login clears admin counter only
        admin_wrong = session.post(
            f"{API}/auth/admin/login",
            json={"username": "admin", "password": "wrong-admin"},
        )
        assert admin_wrong.status_code == 401

        admin_ok = session.post(
            f"{API}/auth/admin/login",
            json={"username": "admin", "password": "admin"},
        )
        assert admin_ok.status_code == 200

        admin_counter = mongo_db.login_attempts.find_one({"identifier": admin_identifier}, {"_id": 0, "count": 1})
        preserved_counter = mongo_db.login_attempts.find_one({"identifier": preserved_identifier}, {"_id": 0, "count": 1})
        lockout_counter = mongo_db.login_attempts.find_one({"identifier": identifier}, {"_id": 0, "count": 1})

        assert admin_counter is None
        assert preserved_counter is not None and preserved_counter.get("count", 0) >= 1
        # A blocked address is not counted against the per-username ceiling.
        assert lockout_counter is not None and lockout_counter.get("count", 0) >= 5

    finally:
        # Targeted cleanup only for identifiers created in this test
        mongo_db.login_attempts.delete_one({"identifier": identifier})
        mongo_db.login_attempts.delete_one({"identifier": preserved_identifier})
        mongo_db.login_attempts.delete_one({"identifier": admin_identifier})
