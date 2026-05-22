"""Test-only endpoints for E2E fixture management.

Registered ONLY when TEST_MODE=1 at server boot. Otherwise the routes do
not exist (return 404).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/test", tags=["test-mode"])


def _reports_root() -> Path:
    return Path(os.getenv("REPORTS_DIR", "reports"))


_TRUNCATE_TARGETS = {
    "history.db": ["MovieHistory", "TorrentHistory"],
    "reports.db": ["ReportSessions", "ReportMovies", "ReportTorrents", "Stats"],
    "operations.db": ["RcloneInventory", "DedupRecords", "PikpakHistory", "system_state"],
}


@router.post("/reset")
def reset_state() -> dict[str, bool]:
    from apps.api.infra import auth as auth_infra

    root = _reports_root()
    for db_name, tables in _TRUNCATE_TARGETS.items():
        db_path = root / db_name
        if not db_path.exists():
            continue
        with sqlite3.connect(str(db_path)) as conn:
            for table in tables:
                # Use TRY to avoid hard-failing on a table that doesn't
                # exist yet (e.g. system_state on a pre-migration DB).
                try:
                    conn.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    with auth_infra._AUTH_LOCK:
        auth_infra.RATE_BUCKETS.clear()
        auth_infra.ACTIVE_TOKENS.clear()
        auth_infra.REVOKED_JTI.clear()

    return {"reset": True}


# ---------------------------------------------------------------------------
# Seed fixture endpoint
# ---------------------------------------------------------------------------

_SEED_SESSION_IDS = [
    "test-committed-001",
    "test-finalizing-002",
    "test-inprogress-003",
]

_SEED_TIMESTAMP = "2026-05-19T00:00:00Z"


def _ensure_schema(history_conn: sqlite3.Connection, reports_conn: sqlite3.Connection) -> None:
    """Create the minimum schema the seed endpoint writes into.

    Mirrors the live schema verbatim so a fresh REPORTS_DIR (e.g. a pytest
    tmp_path) gets usable tables. CREATE IF NOT EXISTS makes this a no-op on
    populated databases.
    """
    reports_conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS "ReportSessions" (
            Id TEXT PRIMARY KEY,
            ReportType TEXT NOT NULL,
            ReportDate TEXT NOT NULL,
            UrlType TEXT,
            DisplayName TEXT,
            Url TEXT,
            StartPage INTEGER,
            EndPage INTEGER,
            CsvFilename TEXT NOT NULL,
            DateTimeCreated TEXT NOT NULL,
            Status TEXT DEFAULT 'in_progress',
            RunId TEXT,
            RunAttempt INTEGER,
            FailureReason TEXT,
            WriteMode TEXT DEFAULT 'audit'
        );
        """
    )
    history_conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS "MovieHistory" (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            VideoCode TEXT NOT NULL,
            Href TEXT NOT NULL UNIQUE,
            ActorName TEXT,
            ActorGender TEXT,
            ActorLink TEXT,
            SupportingActors TEXT,
            DateTimeCreated TEXT,
            DateTimeUpdated TEXT,
            DateTimeVisited TEXT,
            PerfectMatchIndicator INTEGER,
            HiResIndicator INTEGER,
            SessionId TEXT
        );
        CREATE TABLE IF NOT EXISTS "TorrentHistory" (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            MovieHistoryId INTEGER NOT NULL REFERENCES MovieHistory(Id),
            MagnetUri TEXT,
            SubtitleIndicator INTEGER,
            CensorIndicator INTEGER,
            ResolutionType INTEGER,
            Size TEXT,
            FileCount INTEGER,
            DateTimeCreated TEXT,
            DateTimeUpdated TEXT,
            SessionId TEXT
        );
        CREATE TABLE IF NOT EXISTS "PendingMovieHistoryWrites" (
            Seq TEXT PRIMARY KEY NOT NULL,
            SessionId TEXT NOT NULL,
            RunId TEXT,
            RunAttempt INTEGER,
            Href TEXT NOT NULL,
            VideoCode TEXT,
            ActorName TEXT,
            ActorGender TEXT,
            ActorLink TEXT,
            SupportingActors TEXT,
            DateTimeVisited TEXT NOT NULL,
            CreatedAt TEXT NOT NULL,
            ApplyState TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS "PendingTorrentHistoryWrites" (
            Seq TEXT PRIMARY KEY NOT NULL,
            SessionId TEXT NOT NULL,
            RunId TEXT,
            RunAttempt INTEGER,
            Href TEXT NOT NULL,
            VideoCode TEXT,
            Category TEXT NOT NULL,
            SubtitleIndicator INTEGER NOT NULL,
            CensorIndicator INTEGER NOT NULL,
            MagnetUri TEXT,
            Size TEXT,
            FileCount INTEGER,
            ResolutionType INTEGER,
            DateTimeVisited TEXT NOT NULL,
            CreatedAt TEXT NOT NULL,
            ApplyState TEXT NOT NULL DEFAULT 'pending'
        );
        """
    )


def _purge_seed_rows(history_conn: sqlite3.Connection, reports_conn: sqlite3.Connection) -> None:
    """Delete any pre-existing rows for the seed session ids (idempotency)."""
    placeholders = ",".join("?" for _ in _SEED_SESSION_IDS)
    # Drop torrent rows first (FK on MovieHistory.Id).
    for table in (
        "TorrentHistory",
        "PendingMovieHistoryWrites",
        "MovieHistory",
    ):
        try:
            history_conn.execute(
                f"DELETE FROM {table} WHERE SessionId IN ({placeholders})",
                _SEED_SESSION_IDS,
            )
        except sqlite3.OperationalError:
            pass
    try:
        reports_conn.execute(
            f"DELETE FROM ReportSessions WHERE Id IN ({placeholders})",
            _SEED_SESSION_IDS,
        )
    except sqlite3.OperationalError:
        pass


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    status: str,
    write_mode: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ReportSessions
            (Id, ReportType, ReportDate, CsvFilename, DateTimeCreated, Status, WriteMode)
        VALUES (?, 'daily', '2026-05-19', ?, ?, ?, ?)
        """,
        (session_id, f"{session_id}.csv", _SEED_TIMESTAMP, status, write_mode),
    )


def _insert_movie(
    conn: sqlite3.Connection,
    session_id: str,
    video_code: str,
    href: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO MovieHistory
            (VideoCode, Href, ActorName, DateTimeCreated, DateTimeUpdated,
             DateTimeVisited, PerfectMatchIndicator, HiResIndicator, SessionId)
        VALUES (?, ?, 'Seed Actor', ?, ?, ?, 1, 0, ?)
        """,
        (video_code, href, _SEED_TIMESTAMP, _SEED_TIMESTAMP, _SEED_TIMESTAMP, session_id),
    )
    return int(cur.lastrowid)


def _insert_torrent(
    conn: sqlite3.Connection,
    session_id: str,
    movie_id: int,
    subtitle: int,
    censor: int,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO TorrentHistory
            (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
             ResolutionType, Size, FileCount, DateTimeCreated, DateTimeUpdated, SessionId)
        VALUES (?, ?, ?, ?, 1, '1.5GB', 1, ?, ?, ?)
        """,
        (
            movie_id,
            f"magnet:?xt=urn:btih:seed-{movie_id}-{subtitle}-{censor}",
            subtitle,
            censor,
            _SEED_TIMESTAMP,
            _SEED_TIMESTAMP,
            session_id,
        ),
    )
    return int(cur.lastrowid)


def _insert_pending_movie(
    conn: sqlite3.Connection,
    session_id: str,
    seq_suffix: str,
    href: str,
    video_code: str,
) -> None:
    conn.execute(
        """
        INSERT INTO PendingMovieHistoryWrites
            (Seq, SessionId, Href, VideoCode, ActorName, DateTimeVisited, CreatedAt, ApplyState)
        VALUES (?, ?, ?, ?, 'Seed Actor', ?, ?, 'pending')
        """,
        (
            f"{session_id}-{seq_suffix}",
            session_id,
            href,
            video_code,
            _SEED_TIMESTAMP,
            _SEED_TIMESTAMP,
        ),
    )


@router.post("/seed-sessions")
def seed_sessions() -> dict[str, object]:
    """Seed three deterministic sessions for E2E rollback tests.

    Idempotent: prior rows for the same session ids are removed first, so
    every call leaves the database in the same state. See IMP-009 Task 1.
    """
    root = _reports_root()
    root.mkdir(parents=True, exist_ok=True)
    history_path = root / "history.db"
    reports_path = root / "reports.db"

    history_conn = sqlite3.connect(str(history_path))
    reports_conn = sqlite3.connect(str(reports_path))
    try:
        _ensure_schema(history_conn, reports_conn)
        _purge_seed_rows(history_conn, reports_conn)

        # 1. committed / pending — 2 movies + 3 torrents.
        committed = _SEED_SESSION_IDS[0]
        _insert_session(reports_conn, committed, "committed", "pending")
        m1 = _insert_movie(history_conn, committed, "ABC-001", "https://javdb.com/v/seed-committed-1")
        m2 = _insert_movie(history_conn, committed, "ABC-002", "https://javdb.com/v/seed-committed-2")
        # 3 torrents distributed across the 2 movies, each with a unique
        # (MovieHistoryId, SubtitleIndicator, CensorIndicator) tuple.
        t1 = _insert_torrent(history_conn, committed, m1, 1, 1)
        t2 = _insert_torrent(history_conn, committed, m1, 0, 1)
        t3 = _insert_torrent(history_conn, committed, m2, 1, 1)

        # 2. finalizing / pending — 3 pending movies only.
        finalizing = _SEED_SESSION_IDS[1]
        _insert_session(reports_conn, finalizing, "finalizing", "pending")
        for idx in range(3):
            _insert_pending_movie(
                history_conn,
                finalizing,
                f"p{idx}",
                f"https://javdb.com/v/seed-finalizing-{idx}",
                f"FIN-00{idx}",
            )

        # 3. in_progress / pending — 1 committed movie + 2 pending.
        in_progress = _SEED_SESSION_IDS[2]
        _insert_session(reports_conn, in_progress, "in_progress", "pending")
        m3 = _insert_movie(history_conn, in_progress, "INP-001", "https://javdb.com/v/seed-inprog-1")
        for idx in range(2):
            _insert_pending_movie(
                history_conn,
                in_progress,
                f"p{idx}",
                f"https://javdb.com/v/seed-inprog-pending-{idx}",
                f"INP-10{idx}",
            )

        history_conn.commit()
        reports_conn.commit()
    finally:
        history_conn.close()
        reports_conn.close()

    return {"seeded": len(_SEED_SESSION_IDS), "session_ids": list(_SEED_SESSION_IDS)}
