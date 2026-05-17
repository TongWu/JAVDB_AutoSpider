"""Database migration helpers for JAVDB AutoSpider.

Handles schema initialization, version detection, and migrations between
schema versions. Manages the split from single-DB to three-DB layout.

Schema versions:
- v5: Single database (javdb_autospider.db)
- v6: Split into history.db, reports.db, operations.db
- v7: Added Actor columns to MovieHistory
- v8: Added Rollback columns (SessionId, WriteMode)
- v13: Current version (includes Pending tables)
"""

import os
import sqlite3
import threading
from typing import Optional

from javdb.infra.logging import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_get_db = None
_get_local_sqlite_db = None
_HISTORY_DB_PATH = None
_REPORTS_DB_PATH = None
_OPERATIONS_DB_PATH = None
_DB_PATH = None
_SCHEMA_VERSION = None


def _ensure_imports():
    """Lazy import to avoid circular dependency."""
    global _get_db, _get_local_sqlite_db
    global _HISTORY_DB_PATH, _REPORTS_DB_PATH, _OPERATIONS_DB_PATH
    global _DB_PATH, _SCHEMA_VERSION
    if _get_db is None:
        from javdb.storage.db.db_connection import (
            get_db,
            get_local_sqlite_db,
            HISTORY_DB_PATH,
            REPORTS_DB_PATH,
            OPERATIONS_DB_PATH,
            DB_PATH,
            SCHEMA_VERSION,
        )
        _get_db = get_db
        _get_local_sqlite_db = get_local_sqlite_db
        _HISTORY_DB_PATH = HISTORY_DB_PATH
        _REPORTS_DB_PATH = REPORTS_DB_PATH
        _OPERATIONS_DB_PATH = OPERATIONS_DB_PATH
        _DB_PATH = DB_PATH
        _SCHEMA_VERSION = SCHEMA_VERSION


# Serializes the dual-backend init window
_init_lock = threading.Lock()


# ── Public API ───────────────────────────────────────────────────────────


def init_db(db_path: Optional[str] = None, *, force: bool = False) -> None:
    """Initialize all databases with latest schema.

    Creates tables if they don't exist, runs migrations if needed.
    Under STORAGE_BACKEND=dual, temporarily downgrades to sqlite-only
    for DDL operations.

    Args:
        db_path: Optional specific database path to initialize
        force: Force re-initialization even if already initialized
    """
    _ensure_imports()

    # Delegate to db.py's init_db (will be refactored later)
    from javdb.storage.db.db import init_db as _init_db_impl
    _init_db_impl(db_path, force=force)


def detect_schema_version(db_path: str) -> int:
    """Detect the current schema version of a database.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Schema version number (0 if no SchemaVersion table exists)
    """
    _ensure_imports()

    # Delegate to db.py's _detect_version
    from javdb.storage.db.db import _detect_version

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return _detect_version(conn)
    finally:
        conn.close()


def migrate_single_to_split() -> None:
    """Migrate from single javdb_autospider.db to split databases.

    Splits the legacy single database into:
    - history.db (MovieHistory, TorrentHistory)
    - reports.db (ReportSessions, ReportMovies, ReportTorrents, Stats)
    - operations.db (RcloneInventory, DedupRecords, PikpakHistory)
    """
    _ensure_imports()

    # Delegate to db.py's _migrate_single_to_split
    from javdb.storage.db.db import _migrate_single_to_split
    _migrate_single_to_split()


def ensure_moviehistory_actor_columns(db_path: Optional[str] = None) -> None:
    """Ensure MovieHistory has Actor columns (ActorName, ActorGender, ActorLink, SupportingActors).

    Args:
        db_path: Database path (defaults to HISTORY_DB_PATH)
    """
    _ensure_imports()

    from javdb.storage.db.db import _ensure_moviehistory_actor_columns

    conn = sqlite3.connect(db_path or _HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_moviehistory_actor_columns(conn)
        conn.commit()
    finally:
        conn.close()


def ensure_rollback_columns(db_path: Optional[str] = None) -> None:
    """Ensure tables have rollback columns (SessionId, WriteMode).

    Args:
        db_path: Database path (defaults to HISTORY_DB_PATH)
    """
    _ensure_imports()

    from javdb.storage.db.db import _ensure_rollback_columns

    conn = sqlite3.connect(db_path or _HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_rollback_columns(conn)
        conn.commit()
    finally:
        conn.close()


def moviehistory_actor_layout_ok(db_path: Optional[str] = None) -> bool:
    """Check if MovieHistory actor columns are in the correct order.

    Args:
        db_path: Database path (defaults to HISTORY_DB_PATH)

    Returns:
        True if columns are in correct order (Name, Gender, Link, Supporting)
    """
    _ensure_imports()

    from javdb.storage.db.db import moviehistory_actor_layout_ok as _check

    conn = sqlite3.connect(db_path or _HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return _check(conn)
    finally:
        conn.close()
