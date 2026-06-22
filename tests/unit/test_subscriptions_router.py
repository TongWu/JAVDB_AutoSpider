"""Smoke tests for the subscriptions + new-works routers (ADR-054 WS2)."""

import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from apps.api.infra.auth import _require_auth
from javdb.storage import db as _db


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DDL = (
    _REPO_ROOT
    / "javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.execute(
        "INSERT INTO NewWorks (video_code, href, actor_href) VALUES (?, ?, ?)",
        ("NW-001", "/v/nw001", "/actors/EvkJ"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", path)
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = lambda: {
        "username": "admin",
        "role": "admin",
    }
    csrf = "test-csrf-token"
    try:
        c = TestClient(app, cookies={"csrf_token": csrf})
        c.headers.update({"X-CSRF-Token": csrf})
        yield c
    finally:
        app.dependency_overrides.pop(_require_auth, None)


def test_follow_list_get_delete(client):
    put = client.put(
        "/api/subscriptions/actors/EvkJ", json={"actor_name": "Some Name"}
    )
    assert put.status_code == 200, put.text
    assert put.json()["actor_href"] == "/actors/EvkJ"
    assert put.json()["active"] is True

    listed = client.get("/api/subscriptions", params={"active_only": True})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    got = client.get("/api/subscriptions/actors/EvkJ")
    assert got.status_code == 200
    assert got.json()["actor_href"] == "/actors/EvkJ"

    deleted = client.delete("/api/subscriptions/actors/EvkJ")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/subscriptions/actors/EvkJ").status_code == 404


def test_readonly_cannot_mutate_subscription(client):
    from apps.api.services.runtime import app

    app.dependency_overrides[_require_auth] = lambda: {
        "username": "readonly",
        "role": "readonly",
    }
    put = client.put(
        "/api/subscriptions/actors/RO", json={"actor_name": "Readonly"}
    )
    assert put.status_code == 403
    deleted = client.delete("/api/subscriptions/actors/RO")
    assert deleted.status_code == 403
    assert client.get("/api/subscriptions/actors/RO").status_code == 404


def test_subscription_rejects_malformed_actor_href(client):
    response = client.put(
        "/api/subscriptions/actors/EvkJ/extra",
        json={"actor_name": "Malformed"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "subscriptions.invalid_actor_href"


def test_new_works_feed_and_dismiss(client):
    feed = client.get("/api/new-works")
    assert feed.status_code == 200, feed.text
    assert feed.json()["total"] == 1
    assert feed.json()["items"][0]["video_code"] == "NW-001"

    dismissed = client.post("/api/new-works/NW-001/dismiss")
    assert dismissed.status_code == 200
    assert dismissed.json()["dismissed"] is True

    assert client.get("/api/new-works").json()["total"] == 0
    assert (
        client.get("/api/new-works", params={"include_dismissed": True}).json()[
            "total"
        ]
        == 1
    )
    assert client.post("/api/new-works/NOPE-999/dismiss").status_code == 404


def test_dismiss_scoped_to_actor_via_query_param(client):
    # Seed the same release under a second followed actor (composite PK #223).
    from javdb.storage.repos.subscription_repo import NewWorksRepo

    NewWorksRepo(db_path=_db.HISTORY_DB_PATH).add(
        video_code="NW-001", href="/v/nw001", actor_href="/actors/Other"
    )

    dismissed = client.post(
        "/api/new-works/NW-001/dismiss", params={"actor_href": "/actors/EvkJ"}
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["dismissed"] is True

    # Only the targeted actor's feed row is hidden (issue #229).
    evkj = client.get("/api/new-works", params={"actor_href": "/actors/EvkJ"})
    assert evkj.json()["total"] == 0
    other = client.get("/api/new-works", params={"actor_href": "/actors/Other"})
    assert other.json()["total"] == 1


def test_openapi_publishes_subscription_responses():
    from apps.api.services.runtime import app

    app.openapi_schema = None
    schema = app.openapi()
    assert "/api/subscriptions" in schema["paths"]
    assert "/api/new-works" in schema["paths"]
    assert (
        schema["paths"]["/api/subscriptions/{actor_href}"]["delete"]["responses"][
            "200"
        ]["content"]["application/json"]["schema"]
        == {"$ref": "#/components/schemas/ActorSubscriptionDeleteResponse"}
    )
    assert (
        schema["components"]["schemas"]["ActorSubscriptionListResponse"]["properties"][
            "items"
        ]["maxItems"]
        == 500
    )
    assert (
        schema["components"]["schemas"]["NewWorkListResponse"]["properties"]["items"][
            "maxItems"
        ]
        == 200
    )
