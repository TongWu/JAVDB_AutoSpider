"""Statistics data management for JAVDB AutoSpider.

Handles SpiderStats, UploaderStats, and PikpakStats tables in reports.db.

Each stats table uses SessionId as the primary key and supports idempotent
writes via ON CONFLICT DO UPDATE, allowing retries without creating duplicates.

In STORAGE_BACKEND=dual mode, the regular db_get_* functions read from D1
(proving D1 can serve reads), while the *_local variants always read from
SQLite (canonical source for observability tools like email notifications).
"""

import json
import sqlite3
from typing import Optional

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_get_db = None
_get_local_sqlite_db = None
_REPORTS_DB_PATH = None


def _ensure_imports():
    """Lazy import to avoid circular dependency with db_connection."""
    global _get_db, _get_local_sqlite_db, _REPORTS_DB_PATH
    if _get_db is None:
        from packages.python.javdb_platform.db_connection import (
            get_db,
            get_local_sqlite_db,
            REPORTS_DB_PATH,
        )
        _get_db = get_db
        _get_local_sqlite_db = get_local_sqlite_db
        _REPORTS_DB_PATH = REPORTS_DB_PATH


# ── Save Stats ───────────────────────────────────────────────────────────


def db_save_spider_stats(session_id: str, stats: dict, db_path: Optional[str] = None) -> int:
    """Save spider statistics for a session.

    Idempotent via ON CONFLICT(SessionId) DO UPDATE so a re-run
    (e.g. retry after timeout, manual operator re-execution) replaces
    the row instead of duplicating it.

    Args:
        session_id: Session identifier
        stats: Dictionary containing spider statistics:
            - phase1_discovered, phase1_processed, phase1_skipped, phase1_no_new, phase1_failed
            - phase2_discovered, phase2_processed, phase2_skipped, phase2_no_new, phase2_failed
            - total_discovered, total_processed, total_skipped, total_no_new, total_failed
            - failed_movies: List of failed movie hrefs (optional)
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Last row ID
    """
    _ensure_imports()
    failed_movies_json = json.dumps(stats.get('failed_movies', []), ensure_ascii=False) if stats.get('failed_movies') else ''
    with _get_db(db_path or _REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO SpiderStats
               (SessionId,
                Phase1Discovered, Phase1Processed, Phase1Skipped,
                Phase1NoNew, Phase1Failed,
                Phase2Discovered, Phase2Processed, Phase2Skipped,
                Phase2NoNew, Phase2Failed,
                TotalDiscovered, TotalProcessed, TotalSkipped,
                TotalNoNew, TotalFailed, FailedMovies)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(SessionId) DO UPDATE SET
                   Phase1Discovered=excluded.Phase1Discovered,
                   Phase1Processed=excluded.Phase1Processed,
                   Phase1Skipped=excluded.Phase1Skipped,
                   Phase1NoNew=excluded.Phase1NoNew,
                   Phase1Failed=excluded.Phase1Failed,
                   Phase2Discovered=excluded.Phase2Discovered,
                   Phase2Processed=excluded.Phase2Processed,
                   Phase2Skipped=excluded.Phase2Skipped,
                   Phase2NoNew=excluded.Phase2NoNew,
                   Phase2Failed=excluded.Phase2Failed,
                   TotalDiscovered=excluded.TotalDiscovered,
                   TotalProcessed=excluded.TotalProcessed,
                   TotalSkipped=excluded.TotalSkipped,
                   TotalNoNew=excluded.TotalNoNew,
                   TotalFailed=excluded.TotalFailed,
                   FailedMovies=excluded.FailedMovies""",
            (session_id,
             stats.get('phase1_discovered', 0), stats.get('phase1_processed', 0),
             stats.get('phase1_skipped', 0), stats.get('phase1_no_new', 0),
             stats.get('phase1_failed', 0),
             stats.get('phase2_discovered', 0), stats.get('phase2_processed', 0),
             stats.get('phase2_skipped', 0), stats.get('phase2_no_new', 0),
             stats.get('phase2_failed', 0),
             stats.get('total_discovered', 0), stats.get('total_processed', 0),
             stats.get('total_skipped', 0), stats.get('total_no_new', 0),
             stats.get('total_failed', 0), failed_movies_json),
        )
        return cur.lastrowid


def db_save_uploader_stats(session_id: str, stats: dict, db_path: Optional[str] = None) -> int:
    """Save uploader statistics for a session.

    Idempotent via ON CONFLICT(SessionId) DO UPDATE.

    Args:
        session_id: Session identifier
        stats: Dictionary containing uploader statistics:
            - total_torrents, duplicate_count, attempted
            - successfully_added, failed_count
            - hacked_sub, hacked_nosub
            - subtitle_count, no_subtitle_count
            - success_rate
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Last row ID
    """
    _ensure_imports()
    with _get_db(db_path or _REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO UploaderStats
               (SessionId, TotalTorrents, DuplicateCount, Attempted,
                SuccessfullyAdded, FailedCount, HackedSub, HackedNosub,
                SubtitleCount, NoSubtitleCount, SuccessRate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(SessionId) DO UPDATE SET
                   TotalTorrents=excluded.TotalTorrents,
                   DuplicateCount=excluded.DuplicateCount,
                   Attempted=excluded.Attempted,
                   SuccessfullyAdded=excluded.SuccessfullyAdded,
                   FailedCount=excluded.FailedCount,
                   HackedSub=excluded.HackedSub,
                   HackedNosub=excluded.HackedNosub,
                   SubtitleCount=excluded.SubtitleCount,
                   NoSubtitleCount=excluded.NoSubtitleCount,
                   SuccessRate=excluded.SuccessRate""",
            (session_id,
             stats.get('total_torrents', 0), stats.get('duplicate_count', 0),
             stats.get('attempted', 0), stats.get('successfully_added', 0),
             stats.get('failed_count', 0), stats.get('hacked_sub', 0),
             stats.get('hacked_nosub', 0), stats.get('subtitle_count', 0),
             stats.get('no_subtitle_count', 0), stats.get('success_rate', 0.0)),
        )
        return cur.lastrowid


def db_save_pikpak_stats(session_id: str, stats: dict, db_path: Optional[str] = None) -> int:
    """Save PikPak bridge statistics for a session.

    Idempotent via ON CONFLICT(SessionId) DO UPDATE.

    Args:
        session_id: Session identifier
        stats: Dictionary containing PikPak statistics:
            - threshold_days, total_torrents
            - filtered_old, successful_count, failed_count
            - uploaded_count, delete_failed_count
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Last row ID
    """
    _ensure_imports()
    with _get_db(db_path or _REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO PikpakStats
               (SessionId, ThresholdDays, TotalTorrents,
                FilteredOld, SuccessfulCount, FailedCount,
                UploadedCount, DeleteFailedCount)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(SessionId) DO UPDATE SET
                   ThresholdDays=excluded.ThresholdDays,
                   TotalTorrents=excluded.TotalTorrents,
                   FilteredOld=excluded.FilteredOld,
                   SuccessfulCount=excluded.SuccessfulCount,
                   FailedCount=excluded.FailedCount,
                   UploadedCount=excluded.UploadedCount,
                   DeleteFailedCount=excluded.DeleteFailedCount""",
            (session_id,
             stats.get('threshold_days', 3), stats.get('total_torrents', 0),
             stats.get('filtered_old', 0), stats.get('successful_count', 0),
             stats.get('failed_count', 0),
             stats.get('uploaded_count', stats.get('successful_count', 0)),
             stats.get('delete_failed_count', 0)),
        )
        return cur.lastrowid


# ── Get Stats (D1-aware in dual mode) ───────────────────────────────────


def db_get_spider_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get spider stats for a session.

    In STORAGE_BACKEND=dual mode, reads from D1 (proving D1 can serve reads).
    For canonical SQLite reads, use db_get_spider_stats_local().

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Dictionary of stats, or None if not found
    """
    _ensure_imports()
    with _get_db(db_path or _REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM SpiderStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_uploader_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get uploader stats for a session.

    In STORAGE_BACKEND=dual mode, reads from D1.
    For canonical SQLite reads, use db_get_uploader_stats_local().

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Dictionary of stats, or None if not found
    """
    _ensure_imports()
    with _get_db(db_path or _REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM UploaderStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_pikpak_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get PikPak stats for a session.

    In STORAGE_BACKEND=dual mode, reads from D1.
    For canonical SQLite reads, use db_get_pikpak_stats_local().

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Dictionary of stats, or None if not found
    """
    _ensure_imports()
    with _get_db(db_path or _REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM PikpakStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Get Stats (SQLite-only, for observability) ──────────────────────────


def db_get_spider_stats_local(
    session_id: str, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to db_get_spider_stats().

    Always reads from local SQLite, even in STORAGE_BACKEND=dual mode.
    Use this for observability tools (email notifications, drift advisories)
    that need the canonical local state, not D1's potentially-lagging view.

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Dictionary of stats, or None if not found
    """
    _ensure_imports()
    with _get_local_sqlite_db(db_path or _REPORTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM SpiderStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_uploader_stats_local(
    session_id: str, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to db_get_uploader_stats().

    Always reads from local SQLite, even in STORAGE_BACKEND=dual mode.

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Dictionary of stats, or None if not found
    """
    _ensure_imports()
    with _get_local_sqlite_db(db_path or _REPORTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM UploaderStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_pikpak_stats_local(
    session_id: str, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to db_get_pikpak_stats().

    Always reads from local SQLite, even in STORAGE_BACKEND=dual mode.

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to REPORTS_DB_PATH)

    Returns:
        Dictionary of stats, or None if not found
    """
    _ensure_imports()
    with _get_local_sqlite_db(db_path or _REPORTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM PikpakStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
