"""Integration tests for GET /api/onboarding/status, POST /api/onboarding/test,
POST /api/onboarding/complete, and POST /api/onboarding/dismiss-hint."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_operations_schema():
    """Ensure the operations.db schema (including system_state) is initialised."""
    from javdb.storage.db.db import init_db
    init_db()


def test_status_default_returns_required_missing(admin_client, monkeypatch):
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {},
    )
    r = admin_client.get("/api/onboarding/status")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["completed"], bool)
    assert isinstance(body["required_missing"], list)
    assert isinstance(body["skippable_missing"], list)
    assert "javdb_session" in body["required_missing"] or "qb" in body["required_missing"]


def test_test_javdb_returns_result(admin_client, monkeypatch):
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {"JAVDB_SESSION_COOKIE": "stub-cookie-value"},
    )
    r = admin_client.post("/api/onboarding/test", json={"component": "javdb"})
    assert r.status_code == 200
    body = r.json()
    assert body["component"] == "javdb"
    assert body["ok"] is True
    assert isinstance(body["message"], str)


def test_test_javdb_not_configured(admin_client, monkeypatch):
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: {},
    )
    r = admin_client.post("/api/onboarding/test", json={"component": "javdb"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "not set" in body["message"]


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


def test_test_qb_reads_from_persisted_store(admin_client, monkeypatch):
    """Regression: ensure /api/onboarding/test reads from the same source as
    GET /api/config (the merged config including the override store),
    not os.getenv. Without this, the wizard reports 'QB_URL not set'
    after the user just saved it via PUT /api/config."""
    fake_cfg = {
        "QB_URL": "http://unreachable.invalid:9999",
        "QB_USERNAME": "tedwu",
        "QB_PASSWORD": "secret",
        "QB_VERIFY_TLS": False,
    }
    monkeypatch.setattr(
        "apps.api.services.config_service.load_runtime_config",
        lambda: fake_cfg,
    )
    r = admin_client.post("/api/onboarding/test", json={"component": "qb"})
    assert r.status_code == 200
    body = r.json()
    # Should fail because URL is unreachable, NOT because QB_URL is not set
    assert body["ok"] is False
    assert "not set" not in (body["message"] or "")
