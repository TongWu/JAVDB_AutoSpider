"""Smoke tests for the watchlist router (ADR-054 WS1)."""

import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from javdb.storage import db as _db
from apps.api.infra.auth import _require_auth


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_WATCH_INTENT_DDL = (
    _REPO_ROOT / "javdb/migrations/d1/2026_06_13_add_watch_intent.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_WATCH_INTENT_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", path)
    from apps.api.services.runtime import app
    # Standard FastAPI test seam: override the auth dependency so the smoke test
    # does not need a real JWT (auth itself is covered by the auth router tests).
    app.dependency_overrides[_require_auth] = lambda: {
        "username": "admin",
        "role": "admin",
    }
    # Supply a CSRF cookie + header so the app-level CSRF middleware lets
    # mutation requests (PUT, DELETE) through. The middleware runs before
    # dependency injection, so dependency_overrides alone is insufficient.
    # Pattern mirrors test_migrations_endpoints.py and test_diagnostics_endpoints.py.
    csrf = "test-csrf-token"
    try:
        c = TestClient(app, cookies={"csrf_token": csrf})
        c.headers.update({"X-CSRF-Token": csrf})
        yield c
    finally:
        app.dependency_overrides.pop(_require_auth, None)


def test_put_then_get_then_delete(client):
    put = client.put(
        "/api/watchlist/ABC-001",
        json={"href": "/v/abc001", "status": "want"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["status"] == "want"

    got = client.get("/api/watchlist/ABC-001")
    assert got.status_code == 200
    assert got.json()["video_code"] == "ABC-001"

    listed = client.get("/api/watchlist", params={"status": "want"})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    bad = client.put(
        "/api/watchlist/ABC-001", json={"href": "/v/abc001", "status": "nope"}
    )
    assert bad.status_code == 422

    deleted = client.delete("/api/watchlist/ABC-001")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/watchlist/ABC-001").status_code == 404


def test_readonly_cannot_mutate(client):
    """A readonly (non-admin) account is rejected on PUT/DELETE (admin-only)."""
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = lambda: {
        "username": "readonly",
        "role": "readonly",
    }
    put = client.put(
        "/api/watchlist/RO-1", json={"href": "/v/ro1", "status": "want"}
    )
    assert put.status_code == 403
    deleted = client.delete("/api/watchlist/RO-1")
    assert deleted.status_code == 403
    # Reads stay allowed for a readonly account.
    assert client.get("/api/watchlist/RO-1").status_code == 404


def test_empty_href_is_rejected(client):
    res = client.put(
        "/api/watchlist/EMPTY-1",
        json={"href": "", "status": "want"},
    )
    assert res.status_code == 422


def test_openapi_publishes_watch_status_enum():
    from apps.api.services.runtime import app

    app.openapi_schema = None
    schema = app.openapi()
    status_schema = schema["components"]["schemas"]["WatchIntentResponse"][
        "properties"
    ]["status"]
    assert status_schema["enum"] == ["want", "viewed"]

    delete_schema = schema["paths"]["/api/watchlist/{video_code}"]["delete"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    assert delete_schema == {"$ref": "#/components/schemas/WatchIntentDeleteResponse"}
    assert schema["components"]["schemas"]["WatchIntentDeleteResponse"][
        "properties"
    ]["deleted"]["type"] == "boolean"
