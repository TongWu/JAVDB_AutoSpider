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


def test_test_javdb_returns_result(admin_client, monkeypatch):
    monkeypatch.setenv("JAVDB_SESSION_COOKIE", "stub-cookie-value")
    r = admin_client.post("/api/onboarding/test", json={"component": "javdb"})
    assert r.status_code == 200
    body = r.json()
    assert body["component"] == "javdb"
    assert isinstance(body["ok"], bool)
    assert isinstance(body["message"], str)


def test_test_unknown_component_422(admin_client):
    r = admin_client.post("/api/onboarding/test", json={"component": "nonsense"})
    assert r.status_code == 422


def test_complete_marks_onboarded(admin_client):
    r = admin_client.post("/api/onboarding/complete")
    assert r.status_code == 200
    status = admin_client.get("/api/onboarding/status").json()
    assert status["completed"] is True


def test_complete_requires_admin(readonly_client):
    r = readonly_client.post("/api/onboarding/complete")
    assert r.status_code in (401, 403)


def test_dismiss_hint_persists(admin_client):
    admin_client.post("/api/onboarding/dismiss-hint", json={"hint_id": "smtp"})
    state = admin_client.get("/api/system/state", params={"key": "dismissed_hints"})
    assert "smtp" in state.json()["value"]


def test_dismiss_hint_idempotent(admin_client):
    admin_client.post("/api/onboarding/dismiss-hint", json={"hint_id": "pikpak"})
    admin_client.post("/api/onboarding/dismiss-hint", json={"hint_id": "pikpak"})
    state = admin_client.get("/api/system/state", params={"key": "dismissed_hints"})
    import json
    hints = json.loads(state.json()["value"])
    assert hints.count("pikpak") == 1
