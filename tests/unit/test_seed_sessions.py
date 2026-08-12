"""Unit tests for POST /api/test/seed-sessions (IMP-009 Task 1).

Verifies the seed endpoint produces a deterministic 3-session fixture and
is idempotent across repeated calls.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _reload_app():
    """Reload runtime so the TEST_MODE-gated router is wired afresh."""
    import apps.api.services.runtime as runtime_mod
    importlib.reload(runtime_mod)
    return runtime_mod.app


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("TEST_MODE", "1")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    app = _reload_app()
    return TestClient(app)


def _connect(tmp_path: Path, db_name: str) -> sqlite3.Connection:
    return sqlite3.connect(str(tmp_path / db_name))


def test_seed_sessions_returns_expected_shape(client: TestClient) -> None:
    resp = client.post("/api/test/seed-sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["seeded"] == 3
    assert body["session_ids"] == [
        "test-committed-001",
        "test-finalizing-002",
        "test-inprogress-003",
    ]


def test_seed_sessions_writes_expected_rows(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/test/seed-sessions")
    assert resp.status_code == 200

    reports = _connect(tmp_path, "reports.db")
    history = _connect(tmp_path, "history.db")
    try:
        sessions = dict(
            reports.execute(
                "SELECT Id, Status || '|' || WriteMode FROM ReportSessions ORDER BY Id"
            ).fetchall()
        )
        assert sessions == {
            "test-committed-001": "committed|pending",
            "test-finalizing-002": "finalizing|pending",
            "test-inprogress-003": "in_progress|pending",
        }

        # Committed session: 2 movies + 3 torrents.
        cnt = lambda sql, *params: history.execute(sql, params).fetchone()[0]
        committed = "test-committed-001"
        assert cnt("SELECT COUNT(*) FROM MovieHistory WHERE SessionId = ?", committed) == 2
        assert cnt("SELECT COUNT(*) FROM TorrentHistory WHERE SessionId = ?", committed) == 3

        # Finalizing session: 3 pending movies, no committed history.
        finalizing = "test-finalizing-002"
        assert cnt("SELECT COUNT(*) FROM PendingMovieHistoryWrites WHERE SessionId = ?", finalizing) == 3
        assert cnt("SELECT COUNT(*) FROM MovieHistory WHERE SessionId = ?", finalizing) == 0

        # In-progress session: 1 committed movie + 2 pending.
        in_progress = "test-inprogress-003"
        assert cnt("SELECT COUNT(*) FROM MovieHistory WHERE SessionId = ?", in_progress) == 1
        assert cnt("SELECT COUNT(*) FROM PendingMovieHistoryWrites WHERE SessionId = ?", in_progress) == 2
    finally:
        reports.close()
        history.close()


def test_seed_sessions_creates_pending_torrent_table(client: TestClient, tmp_path: Path) -> None:
    """A fresh REPORTS_DIR must get PendingTorrentHistoryWrites too.

    commit/rollback flows unconditionally DELETE from this table
    (e.g. db_commit_session_history), so omitting it leaves seeded
    pending sessions crashing with "no such table".
    """
    resp = client.post("/api/test/seed-sessions")
    assert resp.status_code == 200

    history = _connect(tmp_path, "history.db")
    try:
        tables = {
            row[0]
            for row in history.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "PendingTorrentHistoryWrites" in tables
        # The table must be queryable, not just present in name.
        assert history.execute(
            "SELECT COUNT(*) FROM PendingTorrentHistoryWrites"
        ).fetchone()[0] == 0
    finally:
        history.close()


def test_seed_rows_carry_application_generated_ids(client: TestClient, tmp_path: Path) -> None:
    """Seeded MovieHistory / TorrentHistory rows must have explicit Ids.

    Both tables are guarded in
    ``javdb.storage.dual_connection.APPLICATION_GENERATED_ID_PK_COLUMN``:
    every writer supplies ``Id`` from ``generate_integer_id()`` because the
    SQLite and D1 AUTOINCREMENT counters are independent, so an
    AUTOINCREMENT-derived id reused as ``TorrentHistory.MovieHistoryId``
    addresses a different movie on the other backend. The seeder used to
    rely on AUTOINCREMENT + ``cur.lastrowid``, which baked ids 1/2/3 —
    values production never mints — into the E2E fixtures.
    """
    assert client.post("/api/test/seed-sessions").status_code == 200

    history = _connect(tmp_path, "history.db")
    try:
        movie_ids = [
            r[0] for r in history.execute("SELECT Id FROM MovieHistory").fetchall()
        ]
        torrents = history.execute(
            "SELECT Id, MovieHistoryId FROM TorrentHistory"
        ).fetchall()

        assert len(movie_ids) == 3
        assert len(set(movie_ids)) == 3
        assert len(torrents) == 3

        # Application snowflake: orders of magnitude above any AUTOINCREMENT
        # rowid a fresh database hands out, and below D1's 2**53 JSON
        # (IEEE-754 double) transport ceiling.
        for row_id in movie_ids + [t[0] for t in torrents]:
            assert 2 ** 32 < row_id < 2 ** 53, f"not an application id: {row_id}"

        # The FK must still resolve to a seeded movie on this backend.
        for _t_id, movie_fk in torrents:
            assert movie_fk in movie_ids
    finally:
        history.close()


def test_seed_sessions_is_idempotent(client: TestClient, tmp_path: Path) -> None:
    first = client.post("/api/test/seed-sessions")
    second = client.post("/api/test/seed-sessions")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    history = _connect(tmp_path, "history.db")
    reports = _connect(tmp_path, "reports.db")
    try:
        # Counts after the second call should match the single-call expectation.
        assert reports.execute("SELECT COUNT(*) FROM ReportSessions").fetchone()[0] == 3
        # 2 (committed) + 0 (finalizing) + 1 (in_progress) = 3 MovieHistory rows.
        assert history.execute("SELECT COUNT(*) FROM MovieHistory").fetchone()[0] == 3
        # 3 (committed) + 0 + 0 = 3 TorrentHistory rows.
        assert history.execute("SELECT COUNT(*) FROM TorrentHistory").fetchone()[0] == 3
        # 0 (committed) + 3 (finalizing) + 2 (in_progress) = 5 pending rows.
        assert history.execute("SELECT COUNT(*) FROM PendingMovieHistoryWrites").fetchone()[0] == 5
    finally:
        history.close()
        reports.close()


def test_seed_sessions_returns_404_when_test_mode_off(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TEST_MODE", raising=False)
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    app = _reload_app()
    client = TestClient(app)
    resp = client.post("/api/test/seed-sessions")
    assert resp.status_code == 404
