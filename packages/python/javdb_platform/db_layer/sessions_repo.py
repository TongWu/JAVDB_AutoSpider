"""Repository for ReportSessions table (reports.db).

Provides cursor-paginated listing and per-session detail queries.
The physical column names in ReportSessions are:
  Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, ReportType,
  ReportDate, UrlType, DisplayName, Url, StartPage, EndPage, CsvFilename,
  FailureReason.

The public dataclasses expose Python-friendly names (session_id, state, etc.)
that match the plan's JSON field names so the API response shapes stay stable
regardless of the underlying column names.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass


@dataclass
class SessionRow:
    session_id: str
    state: str
    write_mode: str
    run_id: str | None
    run_attempt: int | None
    created_at: str
    # Optional extra columns available on full rows
    report_type: str | None = None
    report_date: str | None = None
    failure_reason: str | None = None


@dataclass
class SessionList:
    items: list[SessionRow]
    next_cursor: str | None
    total_estimate: int | None = None


def _encode_cursor(session_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"sid": session_id}).encode()).decode()


def _decode_cursor(cursor: str) -> str:
    return json.loads(base64.urlsafe_b64decode(cursor.encode())).get("sid")


def _row_to_session(r: sqlite3.Row) -> SessionRow:
    return SessionRow(
        session_id=r["Id"],
        state=r["Status"] or "in_progress",
        write_mode=r["WriteMode"] or "audit",
        run_id=r["RunId"],
        run_attempt=r["RunAttempt"],
        created_at=r["DateTimeCreated"],
        report_type=r["ReportType"] if "ReportType" in r.keys() else None,
        report_date=r["ReportDate"] if "ReportDate" in r.keys() else None,
        failure_reason=r["FailureReason"] if "FailureReason" in r.keys() else None,
    )


class SessionsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def list(
        self,
        *,
        state: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> SessionList:
        sql = (
            "SELECT Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, "
            "ReportType, ReportDate, FailureReason "
            "FROM ReportSessions"
        )
        params: list = []
        clauses: list[str] = []
        if state:
            clauses.append("Status = ?")
            params.append(state)
        if cursor:
            last_sid = _decode_cursor(cursor)
            clauses.append("Id < ?")
            params.append(last_sid)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY Id DESC LIMIT ?"
        params.append(limit + 1)

        rows = self._conn.execute(sql, params).fetchall()
        items = [_row_to_session(r) for r in rows[:limit]]
        next_cursor = _encode_cursor(items[-1].session_id) if len(rows) > limit else None
        return SessionList(items=items, next_cursor=next_cursor)

    def get(self, session_id: str) -> SessionRow | None:
        row = self._conn.execute(
            "SELECT Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated, "
            "ReportType, ReportDate, FailureReason "
            "FROM ReportSessions WHERE Id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_session(row)

    def get_writes(self, session_id: str) -> tuple[list[dict], list[dict]]:
        """Return (movies, torrents) for a session — from ReportMovies/ReportTorrents."""
        movies = [
            dict(row) for row in self._conn.execute(
                "SELECT * FROM ReportMovies WHERE SessionId = ?", (session_id,)
            ).fetchall()
        ]
        torrents = [
            dict(row) for row in self._conn.execute(
                "SELECT * FROM ReportTorrents WHERE SessionId = ?", (session_id,)
            ).fetchall()
        ]
        return movies, torrents
