"""Integration tests for GET /api/system/state and PUT /api/system/state."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_operations_schema():
    """Ensure the operations.db schema (including system_state) is initialised.

    The test-suite root conftest uses a single tmp-file DB for unit tests, but
    integration tests run against the real reports/operations.db.  Call
    init_db() here so the system_state table is guaranteed to exist before any
    request is made.
    """
    from javdb.storage.db.db import init_db
    init_db()


def test_get_missing_returns_null(admin_client):
    r = admin_client.get("/api/system/state", params={"key": "never-set"})
    assert r.status_code == 200
    assert r.json() == {"key": "never-set", "value": None}


def test_put_then_get_roundtrip(admin_client):
    admin_client.put("/api/system/state", json={"key": "test-key-1", "value": "hello"})
    r = admin_client.get("/api/system/state", params={"key": "test-key-1"})
    assert r.status_code == 200
    assert r.json() == {"key": "test-key-1", "value": "hello"}


def test_put_requires_admin(readonly_client):
    r = readonly_client.put(
        "/api/system/state", json={"key": "x", "value": "y"}
    )
    assert r.status_code in (401, 403)


def test_get_allowed_for_readonly(readonly_client):
    r = readonly_client.get("/api/system/state", params={"key": "x"})
    assert r.status_code == 200
