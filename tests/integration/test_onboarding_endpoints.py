"""Integration tests for GET /api/onboarding/status, POST /api/onboarding/test,
POST /api/onboarding/complete, and POST /api/onboarding/dismiss-hint."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_operations_schema():
    """Ensure the operations.db schema (including system_state) is initialised."""
    from packages.python.javdb_platform.db import init_db
    init_db()


def test_status_default_returns_required_missing(admin_client, monkeypatch):
    monkeypatch.delenv("JAVDB_USERNAME", raising=False)
    monkeypatch.delenv("JAVDB_SESSION_COOKIE", raising=False)
    monkeypatch.delenv("QB_URL", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_SERVER", raising=False)
    r = admin_client.get("/api/onboarding/status")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["completed"], bool)
    assert isinstance(body["required_missing"], list)
    assert isinstance(body["skippable_missing"], list)
    assert "javdb_session" in body["required_missing"] or "qb" in body["required_missing"]
