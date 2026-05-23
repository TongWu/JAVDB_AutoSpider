"""Unit tests for /api/stats/* endpoints.

Tests cover:
- GET /api/stats/summary — aggregate counts, graceful degradation
- GET /api/stats/trend — time-series data, invalid params → 422
- Auth: any authenticated user can access (not admin-only)
- Anon → 401
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf"
    c = TestClient(app, cookies={"csrf_token": csrf})
    c.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return c


@pytest.fixture
def readonly_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf"
    c = TestClient(app, cookies={"csrf_token": csrf})
    c.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return c


@pytest.fixture
def anon_client():
    from apps.api.services.runtime import app

    return TestClient(app)


def _make_in_memory_db(ddl: str, inserts: list[tuple[str, tuple]] | None = None) -> sqlite3.Connection:
    """Create an in-memory SQLite database with the given DDL and optional inserts."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for stmt in ddl.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    if inserts:
        for sql, params in inserts:
            conn.execute(sql, params)
    conn.commit()
    return conn


def _make_fake_get_db(db_map: Dict[str, sqlite3.Connection]):
    """Create a fake get_db context manager that returns in-memory connections."""
    import apps.api.routers.stats as stats_module

    @contextmanager
    def _fake_get_db(db_path=None):
        path = db_path or stats_module.HISTORY_DB_PATH
        conn = db_map.get(path)
        if conn is None:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.row_factory = sqlite3.Row
        yield conn

    return _fake_get_db


def _build_populated_db_map() -> Dict[str, sqlite3.Connection]:
    """Build in-memory databases with test data. Returns db_path → connection map.

    The _isolate_sqlite autouse fixture may set all three DB paths to the same
    temp file, so we use a single connection with all tables when paths collide.
    """
    import apps.api.routers.stats as stats_module

    REPORTS_DB_PATH = stats_module.REPORTS_DB_PATH
    HISTORY_DB_PATH = stats_module.HISTORY_DB_PATH
    OPERATIONS_DB_PATH = stats_module.OPERATIONS_DB_PATH

    unique_paths = {REPORTS_DB_PATH, HISTORY_DB_PATH, OPERATIONS_DB_PATH}

    if len(unique_paths) == 1:
        conn = _make_in_memory_db(
            "CREATE TABLE ReportSessions ("
            "Id TEXT PRIMARY KEY, ReportType TEXT NOT NULL, ReportDate TEXT NOT NULL, "
            "CsvFilename TEXT NOT NULL, DateTimeCreated TEXT NOT NULL, "
            "Status TEXT DEFAULT 'in_progress'"
            ");"
            "CREATE TABLE ReportMovies ("
            "Id INTEGER PRIMARY KEY AUTOINCREMENT, SessionId TEXT NOT NULL, "
            "Href TEXT, VideoCode TEXT, Page INTEGER, Actor TEXT, Rate REAL, CommentNumber INTEGER"
            ");"
            "CREATE TABLE ReportTorrents ("
            "Id INTEGER PRIMARY KEY AUTOINCREMENT, ReportMovieId INTEGER NOT NULL, "
            "VideoCode TEXT, MagnetUri TEXT, Size TEXT, FileCount INTEGER"
            ");"
            "CREATE TABLE MovieHistory ("
            "Id INTEGER PRIMARY KEY AUTOINCREMENT, VideoCode TEXT NOT NULL, "
            "Href TEXT NOT NULL UNIQUE, DateTimeCreated TEXT"
            ");"
            "CREATE TABLE PikpakHistory ("
            "Id INTEGER PRIMARY KEY AUTOINCREMENT, TorrentHash TEXT, "
            "TorrentName TEXT, DateTimeUploadedToPikpak TEXT"
            ");"
            "CREATE TABLE DedupRecords ("
            "Id INTEGER PRIMARY KEY AUTOINCREMENT, ExistingFolderSize INTEGER, "
            "DateTimeDetected TEXT, IsDeleted INTEGER DEFAULT 0"
            ")",
            [
                ("INSERT INTO ReportSessions VALUES (?, ?, ?, ?, ?, ?)",
                 ("s1", "daily", "2026-05-20", "f1.csv", "2026-05-20T10:00:00Z", "committed")),
                ("INSERT INTO ReportSessions VALUES (?, ?, ?, ?, ?, ?)",
                 ("s2", "daily", "2026-05-21", "f2.csv", "2026-05-21T10:00:00Z", "committed")),
                ("INSERT INTO ReportSessions VALUES (?, ?, ?, ?, ?, ?)",
                 ("s3", "daily", "2026-05-22", "f3.csv", "2026-05-22T10:00:00Z", "failed")),
                ("INSERT INTO ReportMovies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (1, "s1", "/v/abc", "ABC-001", 1, "Actor A", 4.5, 10)),
                ("INSERT INTO ReportMovies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (2, "s1", "/v/def", "DEF-002", 1, "Actor B", 3.8, 5)),
                ("INSERT INTO ReportMovies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (3, "s2", "/v/ghi", "GHI-003", 1, "Actor C", 4.0, 8)),
                ("INSERT INTO ReportTorrents VALUES (?, ?, ?, ?, ?, ?)",
                 (1, 1, "ABC-001", "magnet:?xt=1", "1.5GB", 3)),
                ("INSERT INTO ReportTorrents VALUES (?, ?, ?, ?, ?, ?)",
                 (2, 1, "ABC-001", "magnet:?xt=2", "2.0GB", 5)),
                ("INSERT INTO ReportTorrents VALUES (?, ?, ?, ?, ?, ?)",
                 (3, 2, "DEF-002", "magnet:?xt=3", "800MB", 2)),
                ("INSERT INTO ReportTorrents VALUES (?, ?, ?, ?, ?, ?)",
                 (4, 3, "GHI-003", "magnet:?xt=4", "1.2GB", 4)),
                ("INSERT INTO MovieHistory VALUES (?, ?, ?, ?)",
                 (1, "ABC-001", "/v/abc", "2026-05-20T10:00:00Z")),
                ("INSERT INTO MovieHistory VALUES (?, ?, ?, ?)",
                 (2, "DEF-002", "/v/def", "2026-05-21T10:00:00Z")),
                ("INSERT INTO MovieHistory VALUES (?, ?, ?, ?)",
                 (3, "GHI-003", "/v/ghi", "2026-05-22T10:00:00Z")),
                ("INSERT INTO PikpakHistory VALUES (?, ?, ?, ?)",
                 (1, "hash1", "torrent1", "2026-05-20T10:00:00Z")),
                ("INSERT INTO PikpakHistory VALUES (?, ?, ?, ?)",
                 (2, "hash2", "torrent2", "2026-05-21T10:00:00Z")),
                ("INSERT INTO DedupRecords VALUES (?, ?, ?, ?)",
                 (1, 1073741824, "2026-05-20T10:00:00Z", 1)),
                ("INSERT INTO DedupRecords VALUES (?, ?, ?, ?)",
                 (2, 536870912, "2026-05-21T10:00:00Z", 0)),
            ],
        )
        return {REPORTS_DB_PATH: conn}

    reports_conn = _make_in_memory_db(
        "CREATE TABLE ReportSessions ("
        "Id TEXT PRIMARY KEY, ReportType TEXT NOT NULL, ReportDate TEXT NOT NULL, "
        "CsvFilename TEXT NOT NULL, DateTimeCreated TEXT NOT NULL, "
        "Status TEXT DEFAULT 'in_progress'"
        ");"
        "CREATE TABLE ReportMovies ("
        "Id INTEGER PRIMARY KEY AUTOINCREMENT, SessionId TEXT NOT NULL, "
        "Href TEXT, VideoCode TEXT, Page INTEGER, Actor TEXT, Rate REAL, CommentNumber INTEGER"
        ");"
        "CREATE TABLE ReportTorrents ("
        "Id INTEGER PRIMARY KEY AUTOINCREMENT, ReportMovieId INTEGER NOT NULL, "
        "VideoCode TEXT, MagnetUri TEXT, Size TEXT, FileCount INTEGER"
        ")",
        [
            ("INSERT INTO ReportSessions VALUES (?, ?, ?, ?, ?, ?)",
             ("s1", "daily", "2026-05-20", "f1.csv", "2026-05-20T10:00:00Z", "committed")),
            ("INSERT INTO ReportSessions VALUES (?, ?, ?, ?, ?, ?)",
             ("s2", "daily", "2026-05-21", "f2.csv", "2026-05-21T10:00:00Z", "committed")),
            ("INSERT INTO ReportSessions VALUES (?, ?, ?, ?, ?, ?)",
             ("s3", "daily", "2026-05-22", "f3.csv", "2026-05-22T10:00:00Z", "failed")),
            ("INSERT INTO ReportMovies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
             (1, "s1", "/v/abc", "ABC-001", 1, "Actor A", 4.5, 10)),
            ("INSERT INTO ReportMovies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
             (2, "s1", "/v/def", "DEF-002", 1, "Actor B", 3.8, 5)),
            ("INSERT INTO ReportMovies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
             (3, "s2", "/v/ghi", "GHI-003", 1, "Actor C", 4.0, 8)),
            ("INSERT INTO ReportTorrents VALUES (?, ?, ?, ?, ?, ?)",
             (1, 1, "ABC-001", "magnet:?xt=1", "1.5GB", 3)),
            ("INSERT INTO ReportTorrents VALUES (?, ?, ?, ?, ?, ?)",
             (2, 1, "ABC-001", "magnet:?xt=2", "2.0GB", 5)),
            ("INSERT INTO ReportTorrents VALUES (?, ?, ?, ?, ?, ?)",
             (3, 2, "DEF-002", "magnet:?xt=3", "800MB", 2)),
            ("INSERT INTO ReportTorrents VALUES (?, ?, ?, ?, ?, ?)",
             (4, 3, "GHI-003", "magnet:?xt=4", "1.2GB", 4)),
        ],
    )

    history_conn = _make_in_memory_db(
        "CREATE TABLE MovieHistory ("
        "Id INTEGER PRIMARY KEY AUTOINCREMENT, VideoCode TEXT NOT NULL, "
        "Href TEXT NOT NULL UNIQUE, DateTimeCreated TEXT"
        ")",
        [
            ("INSERT INTO MovieHistory VALUES (?, ?, ?, ?)",
             (1, "ABC-001", "/v/abc", "2026-05-20T10:00:00Z")),
            ("INSERT INTO MovieHistory VALUES (?, ?, ?, ?)",
             (2, "DEF-002", "/v/def", "2026-05-21T10:00:00Z")),
            ("INSERT INTO MovieHistory VALUES (?, ?, ?, ?)",
             (3, "GHI-003", "/v/ghi", "2026-05-22T10:00:00Z")),
        ],
    )

    operations_conn = _make_in_memory_db(
        "CREATE TABLE PikpakHistory ("
        "Id INTEGER PRIMARY KEY AUTOINCREMENT, TorrentHash TEXT, "
        "TorrentName TEXT, DateTimeUploadedToPikpak TEXT"
        ");"
        "CREATE TABLE DedupRecords ("
        "Id INTEGER PRIMARY KEY AUTOINCREMENT, ExistingFolderSize INTEGER, "
        "DateTimeDetected TEXT, IsDeleted INTEGER DEFAULT 0"
        ")",
        [
            ("INSERT INTO PikpakHistory VALUES (?, ?, ?, ?)",
             (1, "hash1", "torrent1", "2026-05-20T10:00:00Z")),
            ("INSERT INTO PikpakHistory VALUES (?, ?, ?, ?)",
             (2, "hash2", "torrent2", "2026-05-21T10:00:00Z")),
            ("INSERT INTO DedupRecords VALUES (?, ?, ?, ?)",
             (1, 1073741824, "2026-05-20T10:00:00Z", 1)),
            ("INSERT INTO DedupRecords VALUES (?, ?, ?, ?)",
             (2, 536870912, "2026-05-21T10:00:00Z", 0)),
        ],
    )

    return {
        REPORTS_DB_PATH: reports_conn,
        HISTORY_DB_PATH: history_conn,
        OPERATIONS_DB_PATH: operations_conn,
    }


def _build_empty_db_map() -> Dict[str, sqlite3.Connection]:
    """Build in-memory databases with tables but no data.

    Uses a single connection when _isolate_sqlite collapses all paths.
    """
    import apps.api.routers.stats as stats_module

    REPORTS_DB_PATH = stats_module.REPORTS_DB_PATH
    HISTORY_DB_PATH = stats_module.HISTORY_DB_PATH
    OPERATIONS_DB_PATH = stats_module.OPERATIONS_DB_PATH

    all_tables_ddl = (
        "CREATE TABLE ReportSessions ("
        "Id TEXT PRIMARY KEY, ReportType TEXT NOT NULL, ReportDate TEXT NOT NULL, "
        "CsvFilename TEXT NOT NULL, DateTimeCreated TEXT NOT NULL, "
        "Status TEXT DEFAULT 'in_progress'"
        ");"
        "CREATE TABLE ReportMovies (Id INTEGER PRIMARY KEY AUTOINCREMENT, SessionId TEXT NOT NULL);"
        "CREATE TABLE ReportTorrents (Id INTEGER PRIMARY KEY AUTOINCREMENT, ReportMovieId INTEGER NOT NULL);"
        "CREATE TABLE MovieHistory ("
        "Id INTEGER PRIMARY KEY AUTOINCREMENT, VideoCode TEXT NOT NULL, "
        "Href TEXT NOT NULL UNIQUE, DateTimeCreated TEXT"
        ");"
        "CREATE TABLE PikpakHistory (Id INTEGER PRIMARY KEY AUTOINCREMENT, DateTimeUploadedToPikpak TEXT);"
        "CREATE TABLE DedupRecords (Id INTEGER PRIMARY KEY AUTOINCREMENT, ExistingFolderSize INTEGER, DateTimeDetected TEXT)"
    )

    unique_paths = {REPORTS_DB_PATH, HISTORY_DB_PATH, OPERATIONS_DB_PATH}
    if len(unique_paths) == 1:
        conn = _make_in_memory_db(all_tables_ddl)
        return {REPORTS_DB_PATH: conn}

    return {
        REPORTS_DB_PATH: _make_in_memory_db(
            "CREATE TABLE ReportSessions ("
            "Id TEXT PRIMARY KEY, ReportType TEXT NOT NULL, ReportDate TEXT NOT NULL, "
            "CsvFilename TEXT NOT NULL, DateTimeCreated TEXT NOT NULL, "
            "Status TEXT DEFAULT 'in_progress'"
            ");"
            "CREATE TABLE ReportMovies (Id INTEGER PRIMARY KEY AUTOINCREMENT, SessionId TEXT NOT NULL);"
            "CREATE TABLE ReportTorrents (Id INTEGER PRIMARY KEY AUTOINCREMENT, ReportMovieId INTEGER NOT NULL)"
        ),
        HISTORY_DB_PATH: _make_in_memory_db(
            "CREATE TABLE MovieHistory ("
            "Id INTEGER PRIMARY KEY AUTOINCREMENT, VideoCode TEXT NOT NULL, "
            "Href TEXT NOT NULL UNIQUE, DateTimeCreated TEXT"
            ")"
        ),
        OPERATIONS_DB_PATH: _make_in_memory_db(
            "CREATE TABLE PikpakHistory (Id INTEGER PRIMARY KEY AUTOINCREMENT, DateTimeUploadedToPikpak TEXT);"
            "CREATE TABLE DedupRecords (Id INTEGER PRIMARY KEY AUTOINCREMENT, ExistingFolderSize INTEGER, DateTimeDetected TEXT)"
        ),
    }


def _patch_stats_db(monkeypatch, db_map: Dict[str, sqlite3.Connection]) -> None:
    """Patch the stats module's get_db with in-memory connections."""
    import apps.api.routers.stats as stats_module

    monkeypatch.setattr(stats_module, "get_db", _make_fake_get_db(db_map))


# ---------------------------------------------------------------------------
# TestStatsSummary
# ---------------------------------------------------------------------------


class TestStatsSummary:
    def test_summary_returns_all_fields(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 3
        assert data["success_rate"] is not None
        assert abs(data["success_rate"] - 2.0 / 3.0) < 0.01
        assert data["avg_duration_seconds"] is None
        assert data["total_movies"] == 3
        assert data["total_torrents"] == 4
        assert data["total_pikpak"] == 2
        assert data["total_dedup_freed_bytes"] == 1073741824  # only IsDeleted=1 record counts
        assert data["proxy_bans_last_7d"] == 0

    def test_summary_with_proxy_bans(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module
        from datetime import datetime, timezone

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)

        now_iso = datetime.now(timezone.utc).isoformat()
        meta = {"job_id": "test-job", "kind": "daily", "created_at": now_iso}
        (tmp_path / "test-job.meta.json").write_text(json.dumps(meta))
        (tmp_path / "test-job.log").write_text(
            "proxy ban detected for 1.2.3.4\n"
            "normal line\n"
            "another BAN event\n"
        )
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/summary")

        assert resp.status_code == 200
        assert resp.json()["proxy_bans_last_7d"] == 2

    def test_summary_empty_tables_returns_zeroes(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_empty_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 0
        assert data["success_rate"] is None
        assert data["avg_duration_seconds"] is None
        assert data["total_movies"] == 0
        assert data["total_torrents"] == 0
        assert data["total_pikpak"] == 0
        assert data["total_dedup_freed_bytes"] == 0
        assert data["proxy_bans_last_7d"] == 0

    def test_summary_nonexistent_tables_graceful(self, admin_client, tmp_path, monkeypatch):
        """Tables don't exist at all — should return zeroes/nulls."""
        import apps.api.routers.stats as stats_module

        conn = _make_in_memory_db("SELECT 1")
        no_tables = {stats_module.REPORTS_DB_PATH: conn}
        if stats_module.HISTORY_DB_PATH != stats_module.REPORTS_DB_PATH:
            no_tables[stats_module.HISTORY_DB_PATH] = _make_in_memory_db("SELECT 1")
        if stats_module.OPERATIONS_DB_PATH not in no_tables:
            no_tables[stats_module.OPERATIONS_DB_PATH] = _make_in_memory_db("SELECT 1")
        _patch_stats_db(monkeypatch, no_tables)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_runs"] == 0
        assert data["success_rate"] is None
        assert data["total_movies"] == 0
        assert data["total_torrents"] == 0
        assert data["total_pikpak"] == 0
        assert data["total_dedup_freed_bytes"] == 0


# ---------------------------------------------------------------------------
# TestStatsTrend
# ---------------------------------------------------------------------------


class TestStatsTrend:
    def test_trend_success_rate(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "success_rate", "period": "30d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "success_rate"
        assert data["period"] == "30d"
        # Test data: s1→committed (2026-05-20), s2→committed (2026-05-21), s3→failed (2026-05-22)
        # Each day has one session; success_rate per day is 1.0 for s1/s2, 0.0 for s3.
        assert len(data["data_points"]) == 3
        by_date = {dp["date"]: dp["value"] for dp in data["data_points"]}
        assert abs(by_date["2026-05-20"] - 1.0) < 0.001
        assert abs(by_date["2026-05-21"] - 1.0) < 0.001
        assert abs(by_date["2026-05-22"] - 0.0) < 0.001

    def test_trend_movies(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "movies", "period": "30d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "movies"
        # Test data: s1 has 2 movies (2026-05-20), s2 has 1 movie (2026-05-21), s3 has 0 movies (2026-05-22).
        assert len(data["data_points"]) == 3
        by_date = {dp["date"]: dp["value"] for dp in data["data_points"]}
        assert by_date["2026-05-20"] == 2
        assert by_date["2026-05-21"] == 1
        assert by_date["2026-05-22"] == 0

    def test_trend_torrents(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "torrents", "period": "30d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "torrents"
        assert len(data["data_points"]) > 0

    def test_trend_history_growth(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "history_growth", "period": "30d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "history_growth"
        assert len(data["data_points"]) > 0

    def test_trend_pikpak(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "pikpak", "period": "30d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "pikpak"
        assert len(data["data_points"]) > 0

    def test_trend_dedup(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "dedup", "period": "30d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "dedup"
        assert len(data["data_points"]) > 0

    def test_trend_duration_returns_empty(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "duration", "period": "7d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "duration"
        assert data["data_points"] == []

    def test_trend_proxy_bans(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module
        from datetime import datetime, timezone

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)

        now_iso = datetime.now(timezone.utc).isoformat()
        meta = {"job_id": "test-job", "kind": "daily", "created_at": now_iso}
        (tmp_path / "test-job.meta.json").write_text(json.dumps(meta))
        (tmp_path / "test-job.log").write_text(
            "proxy ban detected\nnormal line\nBAN event\n"
        )
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "proxy_bans", "period": "7d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["metric"] == "proxy_bans"
        assert len(data["data_points"]) >= 1
        total_bans = sum(dp["value"] for dp in data["data_points"])
        assert total_bans == 2

    def test_trend_default_period_is_30d(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "success_rate"})

        assert resp.status_code == 200
        assert resp.json()["period"] == "30d"

    def test_trend_7d_period(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "success_rate", "period": "7d"})

        assert resp.status_code == 200
        assert resp.json()["period"] == "7d"

    def test_trend_90d_period(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "success_rate", "period": "90d"})

        assert resp.status_code == 200
        assert resp.json()["period"] == "90d"

    def test_trend_empty_tables(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_empty_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "movies", "period": "30d"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["data_points"] == []

    def test_trend_period_cutoff_excludes_old_data(self, admin_client, tmp_path, monkeypatch):
        """Data older than the requested period must not appear in trend results."""
        import apps.api.routers.stats as stats_module

        # Build a DB with one recent session and one session far in the past.
        db_map = _build_populated_db_map()
        # Insert an old session dated 2025-01-01 — well outside any period window.
        conn = db_map[stats_module.REPORTS_DB_PATH]
        conn.execute(
            "INSERT INTO ReportSessions VALUES (?, ?, ?, ?, ?, ?)",
            ("s_old", "daily", "2025-01-01", "old.csv", "2025-01-01T10:00:00Z", "committed"),
        )
        conn.commit()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "success_rate", "period": "7d"})

        assert resp.status_code == 200
        data = resp.json()
        # The 2025-01-01 session must not appear in the 7d window.
        dates = {dp["date"] for dp in data["data_points"]}
        assert "2025-01-01" not in dates

    def test_trend_dedup_only_counts_deleted_records(self, admin_client, tmp_path, monkeypatch):
        """The dedup trend must only sum ExistingFolderSize for IsDeleted=1 rows."""
        import apps.api.routers.stats as stats_module

        db_map = _build_populated_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "dedup", "period": "30d"})

        assert resp.status_code == 200
        data = resp.json()
        # Test data: record 1 has IsDeleted=1 (2026-05-20, 1073741824 bytes),
        #            record 2 has IsDeleted=0 (2026-05-21, 536870912 bytes — must be excluded).
        assert len(data["data_points"]) == 1
        assert data["data_points"][0]["date"] == "2026-05-20"
        assert data["data_points"][0]["value"] == 1073741824


# ---------------------------------------------------------------------------
# TestStatsTrendValidation
# ---------------------------------------------------------------------------


class TestStatsTrendValidation:
    def test_invalid_metric_returns_422(self, admin_client):
        resp = admin_client.get("/api/stats/trend", params={"metric": "invalid_metric", "period": "30d"})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"]["code"] == "stats.invalid_metric"

    def test_invalid_period_returns_422(self, admin_client):
        resp = admin_client.get("/api/stats/trend", params={"metric": "success_rate", "period": "1y"})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"]["code"] == "stats.invalid_period"

    def test_missing_metric_returns_422(self, admin_client):
        resp = admin_client.get("/api/stats/trend", params={"period": "30d"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# TestStatsAuth
# ---------------------------------------------------------------------------


class TestStatsAuth:
    def test_summary_admin_200(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_empty_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/summary")
        assert resp.status_code == 200

    def test_summary_readonly_200(self, readonly_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_empty_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = readonly_client.get("/api/stats/summary")
        assert resp.status_code == 200

    def test_summary_anon_401(self, anon_client):
        resp = anon_client.get("/api/stats/summary")
        assert resp.status_code == 401

    def test_trend_admin_200(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_empty_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = admin_client.get("/api/stats/trend", params={"metric": "success_rate"})
        assert resp.status_code == 200

    def test_trend_readonly_200(self, readonly_client, tmp_path, monkeypatch):
        import apps.api.routers.stats as stats_module

        db_map = _build_empty_db_map()
        _patch_stats_db(monkeypatch, db_map)
        monkeypatch.setattr(stats_module, "_LOGS_DIR", tmp_path)

        resp = readonly_client.get("/api/stats/trend", params={"metric": "success_rate"})
        assert resp.status_code == 200

    def test_trend_anon_401(self, anon_client):
        resp = anon_client.get("/api/stats/trend", params={"metric": "success_rate"})
        assert resp.status_code == 401
