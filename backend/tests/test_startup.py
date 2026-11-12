"""Start-up behaviour when the database is slow to accept connections (no live server needed).

Run: cd backend && python -m pytest tests/test_startup.py -q
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "sipostlog_test")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "$2b$12$" + "x" * 53)
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@sipostlog.local")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


@pytest.fixture()
def naps(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    return slept


def run_startup(monkeypatch, outcomes, attempts=8):
    calls = []

    async def fake_init():
        calls.append(1)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(server, "ensure_initialized", fake_init)
    monkeypatch.setattr(server, "STARTUP_DB_ATTEMPTS", attempts)
    asyncio.run(server.startup())
    return len(calls)


def test_startup_retries_with_backoff_until_database_answers(monkeypatch, naps):
    down = ServerSelectionTimeoutError("db:27017: connection closed")
    assert run_startup(monkeypatch, [down, down, None]) == 3
    assert naps == [2, 4]


def test_startup_gives_up_after_the_configured_attempts(monkeypatch, naps):
    down = ServerSelectionTimeoutError("db:27017: connection closed")
    with pytest.raises(ServerSelectionTimeoutError):
        run_startup(monkeypatch, [down], attempts=3)
    assert naps == [2, 4]


def test_startup_does_not_retry_errors_that_waiting_cannot_fix(monkeypatch, naps):
    with pytest.raises(OperationFailure):
        run_startup(monkeypatch, [OperationFailure("Authentication failed")])
    assert naps == []


@pytest.mark.parametrize("raw, expected", [("", 8), ("abc", 8), ("0", 1), ("3", 3)])
def test_attempt_setting_tolerates_bad_values(monkeypatch, raw, expected):
    monkeypatch.setenv("STARTUP_DB_ATTEMPTS", raw)
    assert server._startup_attempts() == expected
