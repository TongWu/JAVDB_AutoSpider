"""ADR-046 Phase 4 Task 0: the last 7 unwrapped session facade fns.

Each of the 7 ``db_*`` functions in ``javdb.storage.db._db_reports`` previously
had no repo equivalent, blocking their privatization. Task 0 adds a thin
delegating wrapper for each:

  SessionsRepo (read)          SessionLifecycleRepo (lifecycle)
  ─────────────────────        ────────────────────────────────
  get_session_status           count_in_progress_sessions_for_run
  get_report_rows              begin_finalize_session
  get_latest_session           finish_commit_session
  get_sessions_by_date

These tests seed a real temp reports.db, call the repo method, and assert the
result is identical to calling the underlying ``db_*`` directly against the same
DB. That pins both the *delegation* (same return value) and the *db_path
threading* (if db_path were dropped, the underlying fn would read the default
REPORTS_DB_PATH instead of the temp DB and the equality would break).
"""

from __future__ import annotations

import sqlite3

import pytest

from javdb.storage.db._db_reports import (
    db_begin_finalize_session,
    db_count_in_progress_sessions_for_run,
    db_finish_commit_session,
    db_get_latest_session,
    db_get_report_rows,
    db_get_session_status,
    db_get_sessions_by_date,
)
from javdb.storage.repos.session_lifecycle_repo import SessionLifecycleRepo
from javdb.storage.repos.sessions_repo import SessionsRepo


# ── schema + seeding helpers ──────────────────────────────────────────────


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE ReportSessions ("
        "  Id TEXT PRIMARY KEY,"
        "  Status TEXT,"
        "  WriteMode TEXT,"
        "  RunId TEXT,"
        "  RunAttempt INTEGER,"
        "  DateTimeCreated TEXT,"
        "  ReportType TEXT,"
        "  ReportDate TEXT,"
        "  DisplayName TEXT,"
        "  Url TEXT,"
        "  UrlType TEXT,"
        "  StartPage INTEGER,"
        "  EndPage INTEGER,"
        "  CsvFilename TEXT,"
        "  FailureReason TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE ReportMovies ("
        "  Id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  SessionId TEXT,"
        "  Href TEXT,"
        "  VideoCode TEXT,"
        "  Page INTEGER,"
        "  Actor TEXT,"
        "  Rate TEXT,"
        "  CommentNumber INTEGER"
        ")"
    )
    conn.execute(
        "CREATE TABLE ReportTorrents ("
        "  Id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  ReportMovieId INTEGER,"
        "  MagnetUri TEXT,"
        "  Size TEXT,"
        "  FileCount INTEGER,"
        "  ResolutionType TEXT,"
        "  SubtitleIndicator INTEGER,"
        "  CensorIndicator INTEGER"
        ")"
    )


def _insert_session(conn, *, sid, status, write_mode=None,
                    run_id=None, run_attempt=None, report_type=None,
                    report_date=None, created="2026-06-03T00:00:00Z"):
    conn.execute(
        "INSERT INTO ReportSessions "
        "(Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, "
        " ReportType, ReportDate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, status, write_mode, run_id, run_attempt, created,
         report_type, report_date),
    )


@pytest.fixture
def reports_db(tmp_path):
    """A populated temp reports.db; returns (path_str, conn)."""
    path = tmp_path / "reports.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _create_schema(conn)
    _insert_session(conn, sid="sess-A", status="committed", write_mode="pending",
                    run_id="run-1", run_attempt=1, report_type="daily",
                    report_date="2026-06-03")
    _insert_session(conn, sid="sess-B", status="in_progress", write_mode="pending",
                    run_id="run-1", run_attempt=1, report_type="daily",
                    report_date="2026-06-03")
    _insert_session(conn, sid="sess-C", status="finalizing", write_mode="pending",
                    run_id="run-2", run_attempt=1, report_type="adhoc",
                    report_date="2026-06-02")
    # A movie + torrent under sess-A for the report-rows wrapper.
    conn.execute(
        "INSERT INTO ReportMovies "
        "(SessionId, Href, VideoCode, Page, Actor, Rate, CommentNumber) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess-A", "/v/abc", "ABC-123", 1, "Someone", "4.5", 10),
    )
    movie_id = conn.execute("SELECT Id FROM ReportMovies").fetchone()["Id"]
    conn.execute(
        "INSERT INTO ReportTorrents "
        "(ReportMovieId, MagnetUri, Size, FileCount, ResolutionType, "
        " SubtitleIndicator, CensorIndicator) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (movie_id, "magnet:?xt=1", "4.2GB", 3, "1080p", 1, 1),
    )
    conn.commit()
    return str(path), conn


# ── SessionsRepo read wrappers ────────────────────────────────────────────


def test_get_session_status_matches_underlying(reports_db):
    path, conn = reports_db
    repo = SessionsRepo(conn)
    assert repo.get_session_status("sess-A", db_path=path) == \
        db_get_session_status("sess-A", db_path=path)
    # Returns the full (WriteMode, Status) tuple, not just Status.
    assert repo.get_session_status("sess-A", db_path=path) == ("pending", "committed")


def test_get_session_status_missing_returns_none(reports_db):
    path, conn = reports_db
    repo = SessionsRepo(conn)
    assert repo.get_session_status("nope", db_path=path) is None
    assert repo.get_session_status("nope", db_path=path) == \
        db_get_session_status("nope", db_path=path)


def test_get_report_rows_matches_underlying(reports_db):
    path, conn = reports_db
    repo = SessionsRepo(conn)
    assert repo.get_report_rows("sess-A", db_path=path) == \
        db_get_report_rows("sess-A", db_path=path)
    rows = repo.get_report_rows("sess-A", db_path=path)
    assert len(rows) == 1
    assert rows[0]["video_code"] == "ABC-123"
    # Torrent (sub=1, cen=1) maps to the 'subtitle' category.
    assert rows[0]["subtitle"] == "magnet:?xt=1"


def test_get_latest_session_matches_underlying(reports_db):
    path, conn = reports_db
    repo = SessionsRepo(conn)
    assert repo.get_latest_session(db_path=path) == \
        db_get_latest_session(db_path=path)
    # Latest by Id DESC -> sess-C (last inserted).
    assert repo.get_latest_session(db_path=path)["Id"] == "sess-C"


def test_get_latest_session_with_report_type_matches_underlying(reports_db):
    path, conn = reports_db
    repo = SessionsRepo(conn)
    assert repo.get_latest_session(report_type="daily", db_path=path) == \
        db_get_latest_session("daily", db_path=path)
    assert repo.get_latest_session(report_type="daily", db_path=path)["Id"] == "sess-B"


def test_get_sessions_by_date_matches_underlying(reports_db):
    path, conn = reports_db
    repo = SessionsRepo(conn)
    assert repo.get_sessions_by_date("2026-06-03", db_path=path) == \
        db_get_sessions_by_date("2026-06-03", db_path=path)
    ids = {r["Id"] for r in repo.get_sessions_by_date("2026-06-03", db_path=path)}
    assert ids == {"sess-A", "sess-B"}


def test_get_sessions_by_date_with_report_type_matches_underlying(reports_db):
    path, conn = reports_db
    repo = SessionsRepo(conn)
    assert repo.get_sessions_by_date("2026-06-03", report_type="daily", db_path=path) == \
        db_get_sessions_by_date("2026-06-03", "daily", db_path=path)


# ── SessionLifecycleRepo lifecycle wrappers ───────────────────────────────


def test_count_in_progress_sessions_for_run_matches_underlying(reports_db):
    path, _ = reports_db
    repo = SessionLifecycleRepo(db_path=path)
    assert repo.count_in_progress_sessions_for_run("run-1", 1) == \
        db_count_in_progress_sessions_for_run("run-1", 1, db_path=path)
    # sess-B is the only in_progress row for (run-1, attempt 1).
    assert repo.count_in_progress_sessions_for_run("run-1", 1) == 1


def test_count_in_progress_sessions_for_run_without_attempt(reports_db):
    path, _ = reports_db
    repo = SessionLifecycleRepo(db_path=path)
    assert repo.count_in_progress_sessions_for_run("run-1") == \
        db_count_in_progress_sessions_for_run("run-1", db_path=path)


def test_begin_finalize_session_matches_underlying(reports_db):
    path, _ = reports_db
    repo = SessionLifecycleRepo(db_path=path)
    # sess-B is in_progress -> the repo flips it to finalizing (rowcount 1).
    assert repo.begin_finalize_session("sess-B") == 1
    conn2 = sqlite3.connect(path)
    conn2.row_factory = sqlite3.Row
    status = conn2.execute(
        "SELECT Status FROM ReportSessions WHERE Id=?", ("sess-B",)
    ).fetchone()["Status"]
    assert status == "finalizing"


def test_begin_finalize_session_threads_db_path_no_op_on_wrong_state(reports_db):
    """A committed session can't be finalized -> rowcount 0, same as direct."""
    path, _ = reports_db
    repo = SessionLifecycleRepo(db_path=path)
    assert repo.begin_finalize_session("sess-A") == \
        db_begin_finalize_session("sess-A", db_path=path)
    assert repo.begin_finalize_session("sess-A") == 0


def test_finish_commit_session_matches_underlying(reports_db):
    path, _ = reports_db
    repo = SessionLifecycleRepo(db_path=path)
    # sess-C is finalizing -> the repo flips it to committed (rowcount 1).
    assert repo.finish_commit_session("sess-C") == 1
    conn2 = sqlite3.connect(path)
    conn2.row_factory = sqlite3.Row
    status = conn2.execute(
        "SELECT Status FROM ReportSessions WHERE Id=?", ("sess-C",)
    ).fetchone()["Status"]
    assert status == "committed"


def test_finish_commit_session_no_op_on_wrong_state(reports_db):
    """An in_progress session isn't finalizing -> rowcount 0, same as direct."""
    path, _ = reports_db
    repo = SessionLifecycleRepo(db_path=path)
    assert repo.finish_commit_session("sess-B") == \
        db_finish_commit_session("sess-B", db_path=path)
    assert repo.finish_commit_session("sess-B") == 0
