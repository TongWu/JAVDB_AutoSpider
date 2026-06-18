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

from javdb.storage.contract import fragments as _contract

# ReportSessions full-row projection — single source of truth is the ADR-055
# registry (fragments.py CONSTANTS). The ASSEMBLED query is pinned byte-for-byte
# by the ADR-018 query Contract Golden, so changing the registry value will
# (correctly) red that golden.
_SESSION_COLUMNS = ", ".join(_contract.REPORT_SESSION_COLUMNS.values)


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


def _encode_cursor(session_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"sid": session_id}).encode()).decode()


def _decode_cursor(cursor: str) -> str:
    return json.loads(base64.urlsafe_b64decode(cursor.encode())).get("sid")


def _build_session_query(
    *,
    state: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[str, list]:
    """Assemble the cursor-paginated ReportSessions list query.

    Pure (no DB access): returns the exact SQL string and params list that
    ``SessionsRepo.list`` executes, including the ``limit + 1`` over-fetch used
    for pagination. Pinned by ADR-018 golden fixtures so the Python and TS
    backends cannot silently drift.
    """
    sql = f"SELECT {_SESSION_COLUMNS} FROM ReportSessions"
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
    return sql, params


def _row_to_session(r: sqlite3.Row) -> SessionRow:
    return SessionRow(
        session_id=r["Id"],
        state=r["Status"] or "in_progress",
        # ADR-047: legacy NULL WriteMode rows map to the only live mode.
        write_mode=r["WriteMode"] or "pending",
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
        sql, params = _build_session_query(state=state, cursor=cursor, limit=limit)

        rows = self._conn.execute(sql, params).fetchall()
        items = [_row_to_session(r) for r in rows[:limit]]
        next_cursor = _encode_cursor(items[-1].session_id) if len(rows) > limit else None
        return SessionList(items=items, next_cursor=next_cursor)

    def get(self, session_id: str) -> SessionRow | None:
        row = self._conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM ReportSessions WHERE Id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_session(row)

    def get_status(self, session_id: str) -> str | None:
        """Return ``ReportSessions.Status`` for *session_id* or ``None``.

        Used by safety-sensitive maintenance tools that need to verify a
        session lifecycle state without hydrating the full API row shape.
        """
        row = self._conn.execute(
            "SELECT Status FROM ReportSessions WHERE Id = ?",
            [session_id],
        ).fetchone()
        if row is None:
            return None
        return row["Status"]

    def get_committed_sessions_since(self, created_at: str) -> list[dict]:
        """Return committed ReportSessions rows created at or after *created_at*."""
        rows = self._conn.execute(
            "SELECT Id, Status, DateTimeCreated FROM ReportSessions "
            "WHERE Status = 'committed' AND DateTimeCreated >= ?",
            [created_at],
        ).fetchall()
        return [dict(row) for row in rows]

    def get_cleanup_meta(self, session_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT Id, ReportType, ReportDate, DisplayName, Status, "
            "DateTimeCreated, RunId, RunAttempt FROM ReportSessions "
            "WHERE Id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}

    def get_writes(self, session_id: str) -> tuple[list[dict], list[dict]]:
        """Return (movies, torrents) for a session.

        ReportMovies is linked to sessions via SessionId.
        ReportTorrents is linked via ReportMovieId → ReportMovies.
        """
        movies = [
            dict(row) for row in self._conn.execute(
                "SELECT * FROM ReportMovies WHERE SessionId = ?", (session_id,)
            ).fetchall()
        ]
        # Torrents join through movies for this session.
        torrents = [
            dict(row) for row in self._conn.execute(
                "SELECT t.* FROM ReportTorrents t "
                "JOIN ReportMovies m ON m.Id = t.ReportMovieId "
                "WHERE m.SessionId = ?",
                (session_id,),
            ).fetchall()
        ]
        return movies, torrents

    # ── Thin db_* read delegates (ADR-046 Phase 4 Task 0) ─────────────
    #
    # 1:1 wrappers over the read-family helpers in
    # ``javdb/storage/db/_db_reports.py`` so the public ``db_*`` facade can
    # be privatized. Unlike this repo's conn-owned reads above, these target
    # ``db_*`` functions open their own connection from ``db_path`` (auto-
    # routed by STORAGE_BACKEND), so callers thread ``db_path`` explicitly
    # rather than reusing ``self._conn``. ``db_path=None`` defaults to
    # REPORTS_DB_PATH, mirroring the underlying functions.

    def get_session_status(
        self,
        session_id: str,
        *,
        db_path: str | None = None,
    ) -> tuple[str, str] | None:
        """Return ``(WriteMode, Status)`` for *session_id*, or ``None``."""
        from javdb.storage.db._db_reports import db_get_session_status

        return db_get_session_status(session_id, db_path=db_path)

    def get_report_rows(
        self,
        session_id: str,
        *,
        db_path: str | None = None,
    ) -> list[dict]:
        """Return all rows for a session as flat (legacy-format) dicts."""
        from javdb.storage.db._db_reports import db_get_report_rows

        return db_get_report_rows(session_id, db_path)

    def get_latest_session(
        self,
        report_type: str | None = None,
        *,
        db_path: str | None = None,
    ) -> dict | None:
        """Return the latest session (by Id DESC), optionally by report type."""
        from javdb.storage.db._db_reports import db_get_latest_session

        return db_get_latest_session(report_type, db_path)

    def get_sessions_by_date(
        self,
        report_date: str,
        report_type: str | None = None,
        *,
        db_path: str | None = None,
    ) -> list[dict]:
        """Return all sessions for *report_date* (optionally by report type)."""
        from javdb.storage.db._db_reports import db_get_sessions_by_date

        return db_get_sessions_by_date(report_date, report_type, db_path)
