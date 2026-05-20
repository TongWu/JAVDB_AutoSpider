"""Integration tests for sessions endpoints.

Tasks 12-15: GET /api/sessions, GET /api/sessions/{id},
POST /api/sessions/{id}/rollback, POST /api/sessions/{id}/commit.
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from javdb.storage.db.db import init_db
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
    from javdb.storage.db.db_connection import REPORTS_DB_PATH
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


# ── Task 15: POST /api/sessions/{id}/commit ──────────────────────────────────

def test_commit_force_works_on_in_progress(admin_client):
    """Seed an in_progress session and force-commit it."""
    import sqlite3 as _sqlite3
    from javdb.storage.db.db_connection import REPORTS_DB_PATH
    fin_sid = "20260516T130000.000000Z-TEST-FIN1"
    with _sqlite3.connect(str(REPORTS_DB_PATH)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ReportSessions "
            "(Id, ReportType, ReportDate, CsvFilename, DateTimeCreated, Status, WriteMode, RunId, RunAttempt) "
            "VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)",
            (fin_sid, "test", "2026-05-16", "test.csv", "in_progress", "audit", "test-run", 1),
        )
    try:
        r = admin_client.post(
            f"/api/sessions/{fin_sid}/commit",
            json={"force": True, "drop_pending": False},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == fin_sid
        assert body["new_state"] == "committed"
    finally:
        with _sqlite3.connect(str(REPORTS_DB_PATH)) as conn:
            conn.execute("DELETE FROM ReportSessions WHERE Id = ?", (fin_sid,))


def test_commit_endpoint_emits_metrics_by_default_on_pending(admin_client, tmp_path, monkeypatch):
    """Defaults for fanout_claims/emit_metrics on the HTTP endpoint must be True.

    Seeds a pending-mode finalizing session, calls commit with no flags,
    and asserts the pending_session_verify JSONL line was appended — the
    proof that emit_metrics defaulted to True (CLI parity).
    """
    import json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from javdb.storage.db.db_connection import REPORTS_DB_PATH

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    sid = "20260516T140000.000000Z-TEST-PEND"
    with _sqlite3.connect(str(REPORTS_DB_PATH)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ReportSessions "
            "(Id, ReportType, ReportDate, CsvFilename, DateTimeCreated, Status, WriteMode, RunId, RunAttempt) "
            "VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)",
            (sid, "test", "2026-05-16", "test.csv", "finalizing", "pending", "test-run", 1),
        )
    try:
        r = admin_client.post(
            f"/api/sessions/{sid}/commit",
            json={"force": True},  # omit fanout_claims/emit_metrics → must default to True
        )
        assert r.status_code == 200, r.text
        assert r.json()["new_state"] == "committed"

        # emit_metrics=True on a pending session writes one line to d1_drift.jsonl.
        drift_path = Path(tmp_path) / "D1" / "d1_drift.jsonl"
        assert drift_path.exists(), "d1_drift.jsonl missing — emit_metrics didn't default to True"
        lines = [ln for ln in drift_path.read_text().splitlines() if ln.strip()]
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["kind"] == "pending_session_verify"
        assert record["session_id"] == sid
        assert record["write_mode"] == "pending"
    finally:
        with _sqlite3.connect(str(REPORTS_DB_PATH)) as conn:
            conn.execute("DELETE FROM ReportSessions WHERE Id = ?", (sid,))


def test_commit_endpoint_opts_out_of_metrics(admin_client, tmp_path, monkeypatch):
    """Explicit emit_metrics=False suppresses the JSONL record."""
    import sqlite3 as _sqlite3
    from pathlib import Path
    from javdb.storage.db.db_connection import REPORTS_DB_PATH

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    sid = "20260516T140500.000000Z-TEST-OPTOUT"
    with _sqlite3.connect(str(REPORTS_DB_PATH)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ReportSessions "
            "(Id, ReportType, ReportDate, CsvFilename, DateTimeCreated, Status, WriteMode, RunId, RunAttempt) "
            "VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)",
            (sid, "test", "2026-05-16", "test.csv", "finalizing", "pending", "test-run", 1),
        )
    try:
        r = admin_client.post(
            f"/api/sessions/{sid}/commit",
            json={"force": True, "fanout_claims": False, "emit_metrics": False},
        )
        assert r.status_code == 200, r.text
        drift_path = Path(tmp_path) / "D1" / "d1_drift.jsonl"
        assert not drift_path.exists(), "emit_metrics=False should suppress JSONL emission"
    finally:
        with _sqlite3.connect(str(REPORTS_DB_PATH)) as conn:
            conn.execute("DELETE FROM ReportSessions WHERE Id = ?", (sid,))


def test_commit_requires_admin(readonly_client):
    r = readonly_client.post(
        "/api/sessions/any-id/commit",
        json={"force": True, "drop_pending": False},
    )
    assert r.status_code in (401, 403)


def test_commit_unknown_session_404(admin_client):
    r = admin_client.post(
        "/api/sessions/nonexistent/commit",
        json={"force": False, "drop_pending": False},
    )
    assert r.status_code == 404


# ── Task 14: POST /api/sessions/{id}/rollback ────────────────────────────────

def test_rollback_dry_run_returns_plan(admin_client, seeded_session_id):
    r = admin_client.post(
        f"/api/sessions/{seeded_session_id}/rollback",
        json={"dry_run": True, "include_pending": True, "restore_from_audit": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert "actions" in body
    assert isinstance(body["actions"], list)


def test_rollback_requires_admin(readonly_client):
    r = readonly_client.post(
        "/api/sessions/any-id/rollback",
        json={"dry_run": True, "include_pending": False, "restore_from_audit": True},
    )
    assert r.status_code in (401, 403)


def test_rollback_unknown_session_404(admin_client):
    r = admin_client.post(
        "/api/sessions/nonexistent/rollback",
        json={"dry_run": True, "include_pending": False, "restore_from_audit": True},
    )
    assert r.status_code == 404
