"""Operations table management for JAVDB AutoSpider.

Handles RcloneInventory, DedupRecords, and PikpakHistory tables in operations.db.

These tables support auxiliary operations:
- RcloneInventory: Remote file inventory for deduplication
- DedupRecords: Deduplication tracking
- PikpakHistory: PikPak sync history
"""

from typing import List, Optional

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_get_db = None
_OPERATIONS_DB_PATH = None
_replace_rclone_inventory = None
_open_rclone_staging = None
_append_rclone_staging = None
_swap_rclone_inventory = None
_merge_rclone_inventory_from_stage = None
_drop_rclone_staging = None


def _ensure_imports():
    """Lazy import to avoid circular dependency."""
    global _get_db, _OPERATIONS_DB_PATH
    global _replace_rclone_inventory, _open_rclone_staging, _append_rclone_staging
    global _swap_rclone_inventory, _merge_rclone_inventory_from_stage, _drop_rclone_staging
    if _get_db is None:
        try:
            from packages.python.javdb_platform.db_connection import (
                get_db,
                OPERATIONS_DB_PATH,
            )
            from packages.python.javdb_platform.db_layer.operations_repo import (
                replace_rclone_inventory,
                open_rclone_staging,
                append_rclone_staging,
                swap_rclone_inventory,
                merge_rclone_inventory_from_stage,
                drop_rclone_staging,
            )
            _get_db = get_db
            _OPERATIONS_DB_PATH = OPERATIONS_DB_PATH
            _replace_rclone_inventory = replace_rclone_inventory
            _open_rclone_staging = open_rclone_staging
            _append_rclone_staging = append_rclone_staging
            _swap_rclone_inventory = swap_rclone_inventory
            _merge_rclone_inventory_from_stage = merge_rclone_inventory_from_stage
            _drop_rclone_staging = drop_rclone_staging
        except ImportError:
            # Fallback to db.py during Phase 1
            from packages.python.javdb_platform.db import (
                get_db,
                OPERATIONS_DB_PATH,
                _replace_rclone_inventory as replace_inv,
                _open_rclone_staging as open_stage,
                _append_rclone_staging as append_stage,
                _swap_rclone_inventory as swap_inv,
                _merge_rclone_inventory_from_stage as merge_stage,
                _drop_rclone_staging as drop_stage,
            )
            _get_db = get_db
            _OPERATIONS_DB_PATH = OPERATIONS_DB_PATH
            _replace_rclone_inventory = replace_inv
            _open_rclone_staging = open_stage
            _append_rclone_staging = append_stage
            _swap_rclone_inventory = swap_inv
            _merge_rclone_inventory_from_stage = merge_stage
            _drop_rclone_staging = drop_stage


# ── RcloneInventory ──────────────────────────────────────────────────────


def db_load_rclone_inventory(
    db_path: Optional[str] = None,
) -> List[dict]:
    """Load all rows from RcloneInventory.

    Args:
        db_path: Database path (defaults to OPERATIONS_DB_PATH)

    Returns:
        List of inventory dicts
    """
    _ensure_imports()

    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM RcloneInventory").fetchall()
    return [dict(r) for r in rows]


def db_replace_rclone_inventory(
    rows: List[dict],
    db_path: Optional[str] = None,
) -> int:
    """Replace RcloneInventory with new rows (via staging table).

    Args:
        rows: List of inventory dicts
        db_path: Database path (defaults to OPERATIONS_DB_PATH)

    Returns:
        Number of rows inserted
    """
    _ensure_imports()

    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        return _replace_rclone_inventory(conn, rows)


def db_swap_rclone_inventory(
    db_path: Optional[str] = None,
) -> int:
    """Swap RcloneInventoryStaging into RcloneInventory.

    Args:
        db_path: Database path (defaults to OPERATIONS_DB_PATH)

    Returns:
        Number of rows swapped
    """
    _ensure_imports()

    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        return _swap_rclone_inventory(conn)


# ── DedupRecords ─────────────────────────────────────────────────────────


def db_load_dedup_records(
    db_path: Optional[str] = None,
) -> List[dict]:
    """Load all rows from DedupRecords.

    Args:
        db_path: Database path (defaults to OPERATIONS_DB_PATH)

    Returns:
        List of dedup record dicts
    """
    _ensure_imports()

    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM DedupRecords").fetchall()
    return [dict(r) for r in rows]


def db_save_dedup_records(
    records: List[dict],
    session_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Save dedup records.

    Args:
        records: List of dedup record dicts
        session_id: Session identifier (optional)
        db_path: Database path (defaults to OPERATIONS_DB_PATH)

    Returns:
        Number of records inserted
    """
    _ensure_imports()

    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        for rec in records:
            conn.execute(
                """INSERT INTO DedupRecords
                   (Href, Reason, SessionId, CreatedAt)
                   VALUES (?, ?, ?, ?)""",
                (
                    rec.get('href') or rec.get('Href'),
                    rec.get('reason') or rec.get('Reason'),
                    session_id,
                    rec.get('created_at') or rec.get('CreatedAt'),
                ),
            )
    return len(records)


# ── PikpakHistory ────────────────────────────────────────────────────────


def db_append_pikpak_history(
    entry: dict,
    session_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Append an entry to PikpakHistory.

    Args:
        entry: PikPak history entry dict
        session_id: Session identifier (optional)
        db_path: Database path (defaults to OPERATIONS_DB_PATH)

    Returns:
        Last row ID
    """
    _ensure_imports()

    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO PikpakHistory
               (TorrentHash, FileName, SessionId, SyncedAt)
               VALUES (?, ?, ?, ?)""",
            (
                entry.get('torrent_hash') or entry.get('TorrentHash'),
                entry.get('file_name') or entry.get('FileName'),
                session_id,
                entry.get('synced_at') or entry.get('SyncedAt'),
            ),
        )
    return cur.lastrowid


# ── Rollback interface ───────────────────────────────────────────────────


def rollback_operations_for_session(
    session_id: str,
    db_path: Optional[str] = None,
) -> int:
    """Rollback operations for a session.

    Deletes rows from DedupRecords and PikpakHistory for the given session.
    RcloneInventory is not rolled back (it's a snapshot, not session-specific).

    Args:
        session_id: Session identifier
        db_path: Database path (defaults to OPERATIONS_DB_PATH)

    Returns:
        Number of rows deleted
    """
    _ensure_imports()

    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        dedup_deleted = conn.execute(
            "DELETE FROM DedupRecords WHERE SessionId = ?",
            (session_id,),
        ).rowcount or 0

        pikpak_deleted = conn.execute(
            "DELETE FROM PikpakHistory WHERE SessionId = ?",
            (session_id,),
        ).rowcount or 0

    return dedup_deleted + pikpak_deleted
