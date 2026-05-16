"""Integration tests for sessions endpoints.

Tasks 12-15: GET /api/sessions, GET /api/sessions/{id},
POST /api/sessions/{id}/rollback, POST /api/sessions/{id}/commit.
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from packages.python.javdb_platform.db import init_db
    init_db()


@pytest.fixture
def seeded_session_id():
    """Insert a test session and return its ID. Cleanup after.

    Function-scoped so it runs AFTER _isolate_sqlite patches REPORTS_DB_PATH
    to a per-test temp DB.  The dynamic import inside the body captures the
    patched (temp) path, which is also the path the router uses, so the
    cross-thread insert is visible via WAL mode.
    """
    import sqlite3 as _sqlite3
    # Import dynamically so we get the patched temp path set by _isolate_sqlite.
    from packages.python.javdb_platform.db_connection import REPORTS_DB_PATH
    sid = "20260516T120000.000000Z-TEST-0001"
    with _sqlite3.connect(str(REPORTS_DB_PATH)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ReportSessions "
            "(Id, ReportType, ReportDate, CsvFilename, DateTimeCreated, Status, WriteMode, RunId, RunAttempt) "
            "VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)",
            (sid, "test", "2026-05-16", "test.csv", "committed", "audit", "test-run", 1),
        )
    yield sid
    with _sqlite3.connect(str(REPORTS_DB_PATH)) as conn:
        conn.execute("DELETE FROM ReportSessions WHERE Id = ?", (sid,))


# ── Task 12: GET /api/sessions ───────────────────────────────────────────────

def test_list_returns_items_and_cursor(admin_client, seeded_session_id):  # noqa: F811
    r = admin_client.get("/api/sessions", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert isinstance(body["items"], list)


def test_list_filter_by_state(admin_client, seeded_session_id):
    r = admin_client.get("/api/sessions", params={"state": "committed", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    for item in body["items"]:
        assert item["state"] == "committed"


# ── Task 13: GET /api/sessions/{id} detail ───────────────────────────────────

def test_detail_returns_404_for_missing(admin_client):
    r = admin_client.get("/api/sessions/this-does-not-exist")
    assert r.status_code == 404


def test_detail_returns_shape_for_known_id(admin_client, seeded_session_id):
    r = admin_client.get(f"/api/sessions/{seeded_session_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["session_id"] == seeded_session_id
    assert "movies" in body
    assert "torrents" in body
