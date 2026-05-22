"""Unit tests for SessionsRepo.get_cleanup_meta."""

from __future__ import annotations

import sqlite3

import pytest

from javdb.storage.repos.sessions_repo import SessionsRepo


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "reports.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ReportSessions ("
        "  Id TEXT PRIMARY KEY,"
        "  ReportType TEXT,"
        "  ReportDate TEXT,"
        "  DisplayName TEXT,"
        "  Status TEXT,"
        "  DateTimeCreated TEXT,"
        "  RunId TEXT,"
        "  RunAttempt INTEGER"
        ")"
    )
    conn.commit()
    return SessionsRepo(conn)


def test_get_cleanup_meta_returns_dict_for_existing_session(repo):
    repo._conn.execute(
        "INSERT INTO ReportSessions "
        "(Id, ReportType, ReportDate, DisplayName, Status, DateTimeCreated, RunId, RunAttempt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sess-1", "daily", "2026-05-22", "Daily Run", "committed", "2026-05-22T10:00:00Z", "run-1", 1),
    )
    repo._conn.commit()

    result = repo.get_cleanup_meta("sess-1")

    assert result is not None
    assert result["Id"] == "sess-1"
    assert result["ReportType"] == "daily"
    assert result["ReportDate"] == "2026-05-22"
    assert result["DisplayName"] == "Daily Run"
    assert result["Status"] == "committed"
    assert result["DateTimeCreated"] == "2026-05-22T10:00:00Z"
    assert result["RunId"] == "run-1"
    assert result["RunAttempt"] == 1
    assert set(result.keys()) == {
        "Id", "ReportType", "ReportDate", "DisplayName",
        "Status", "DateTimeCreated", "RunId", "RunAttempt",
    }


def test_get_cleanup_meta_returns_none_for_missing_session(repo):
    result = repo.get_cleanup_meta("nonexistent")
    assert result is None
