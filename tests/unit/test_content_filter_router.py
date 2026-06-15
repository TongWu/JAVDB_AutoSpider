"""Smoke tests for the content-filter router (ADR-040 Phase 4 / WS4a)."""

import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from javdb.storage import db as _db
from apps.api.infra.auth import _require_auth


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONTENT_FILTER_DDL = (
    _REPO_ROOT / "javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "reports.db")  # NOTE: REPORTS_DB, not HISTORY_DB.
    conn = sqlite3.connect(path)
    conn.executescript(_CONTENT_FILTER_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "REPORTS_DB_PATH", path)
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = lambda: {"username": "test", "role": "admin"}
    # Supply a CSRF cookie + header so the app-level CSRF middleware lets
    # mutation requests (POST, PUT, DELETE) through. The middleware runs before
    # dependency injection, so dependency_overrides alone is insufficient.
    # Pattern mirrors test_watchlist_router.py and test_migrations_endpoints.py.
    csrf = "test-csrf-token"
    try:
        c = TestClient(app, cookies={"csrf_token": csrf})
        c.headers.update({"X-CSRF-Token": csrf})
        yield c
    finally:
        app.dependency_overrides.pop(_require_auth, None)


def test_add_list_toggle_delete(client):
    created = client.post(
        "/api/content-filter",
        json={"dimension": "tag", "mode": "exclude", "value": "censored"},
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    assert created.json()["enabled"] is True

    listed = client.get("/api/content-filter")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["dimension"] == "tag"

    disabled = client.put(f"/api/content-filter/{rule_id}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    deleted = client.delete(f"/api/content-filter/{rule_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/content-filter").json()["total"] == 0


def test_rejects_invalid_dimension_mode_pair(client):
    bad = client.post(
        "/api/content-filter",
        json={"dimension": "actor", "mode": "require_lead", "value": "x"},
    )
    assert bad.status_code == 422  # (actor, require_lead) not in VALID_RULE_MODES


def test_value_required_pair_rejects_empty(client):
    bad = client.post(
        "/api/content-filter",
        json={"dimension": "tag", "mode": "exclude", "value": "  "},
    )
    assert bad.status_code == 422  # (tag, exclude) requires a value


def test_toggle_missing_rule_404(client):
    assert client.put("/api/content-filter/999", json={"enabled": True}).status_code == 404


def test_accepts_regex_and_release_date_rules(client):
    ok_regex = client.post(
        "/api/content-filter",
        json={"dimension": "tag", "mode": "regex_exclude", "value": r"(?i)\bvr\b"},
    )
    assert ok_regex.status_code == 201, ok_regex.text
    ok_date = client.post(
        "/api/content-filter",
        json={"dimension": "release_date", "mode": "before", "value": "2020-01-01"},
    )
    assert ok_date.status_code == 201, ok_date.text
    assert ok_date.json()["value"] == "2020-01-01"


def test_accepts_unverified_regex_value_engine_fails_open(client):
    # The web boundary deliberately does NOT compile-check regex (JS `new RegExp`
    # and Python `re` dialects diverge, so a shared check is impossible — see
    # router._validate_value). A malformed pattern is stored; the Python ingestion
    # engine fail-opens on it (never drops, never raises).
    ok = client.post(
        "/api/content-filter",
        json={"dimension": "tag", "mode": "regex_exclude", "value": "(unclosed"},
    )
    assert ok.status_code == 201, ok.text


def test_rejects_bad_release_date_value(client):
    bad = client.post(
        "/api/content-filter",
        json={"dimension": "release_date", "mode": "before", "value": "2020/01/01"},
    )
    assert bad.status_code == 422


def test_toggle_rejects_non_boolean_enabled(client):
    # Cross-backend parity: the TS route rejects a non-boolean `enabled` with 422,
    # so the Python schema uses StrictBool rather than coercing 1 -> True.
    created = client.post(
        "/api/content-filter",
        json={"dimension": "tag", "mode": "exclude", "value": "x"},
    )
    rule_id = created.json()["id"]
    bad = client.put(f"/api/content-filter/{rule_id}", json={"enabled": 1})
    assert bad.status_code == 422


def test_rejects_invalid_gender_value(client):
    # The web boundary now reuses the CLI's per-mode value validation: a gender
    # typo like "femail" is rejected rather than silently filtering every movie.
    bad = client.post(
        "/api/content-filter",
        json={"dimension": "gender", "mode": "require_lead", "value": "femail"},
    )
    assert bad.status_code == 422


def test_rejects_negative_age_value(client):
    bad = client.post(
        "/api/content-filter",
        json={"dimension": "age", "mode": "max_age", "value": "-1"},
    )
    assert bad.status_code == 422


def test_rejects_catastrophic_regex(client):
    # ReDoS write-boundary guard: a nested-quantifier pattern is rejected (422).
    bad = client.post(
        "/api/content-filter",
        json={"dimension": "tag", "mode": "regex_exclude", "value": "(a+)+"},
    )
    assert bad.status_code == 422


def test_normalizes_gender_value(client):
    ok = client.post(
        "/api/content-filter",
        json={"dimension": "gender", "mode": "require_lead", "value": "Female"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["value"] == "female"
