"""Operations table management for JAVDB AutoSpider.

Handles RcloneInventory, DedupRecords, and PikpakHistory tables in operations.db.

These tables support auxiliary operations:
- RcloneInventory: Remote file inventory for deduplication
- DedupRecords: Deduplication tracking
- PikpakHistory: PikPak sync history
"""

import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from javdb.infra.logging import get_logger
from javdb.storage.db._db_session import (
    _SESSION_ID_SENTINEL,
    _resolve_session_id,
)

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
        from javdb.storage.db._db_connection import (
            get_db,
            OPERATIONS_DB_PATH,
        )
        from javdb.storage.repos.operations_repo import (
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


# ── RcloneInventory ──────────────────────────────────────────────────────


def db_load_rclone_inventory(
    db_path: Optional[str] = None,
) -> Dict[str, list]:
    """Load inventory grouped by VideoCode.

    Args:
        db_path: Database path (defaults to OPERATIONS_DB_PATH)

    Returns:
        Dict mapping VideoCode -> list of inventory row dicts
    """
    _ensure_imports()

    inventory: Dict[str, list] = {}
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM RcloneInventory").fetchall()
    for row in rows:
        r = dict(row)
        code = r['VideoCode'].strip().upper()
        if not code:
            continue
        inventory.setdefault(code, []).append(r)
    return inventory


def db_replace_rclone_inventory(
    entries: List[dict],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Replace the entire RcloneInventory table (full scan refresh).

    When *session_id* is provided the staging-then-swap pattern is used:
    rows go to ``RcloneInventoryStaging_<session_id>`` first and only
    swap into the live table once everything has been written. A failed
    or stalled run leaves the main table untouched.
    """
    sid = _resolve_session_id(session_id)
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        return _replace_rclone_inventory(conn, entries, session_id=sid)


def db_swap_rclone_inventory(
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> int:
    """Atomically swap this session's staging into the live RcloneInventory."""
    sid = _resolve_session_id(session_id)
    if sid is None:
        raise ValueError(
            "db_swap_rclone_inventory requires an active session_id "
            "(set via set_active_session_id or pass explicitly)."
        )
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        return _swap_rclone_inventory(conn, sid)


def db_clear_rclone_inventory(db_path: Optional[str] = None) -> None:
    """Delete all rows from RcloneInventory."""
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        conn.execute("DELETE FROM RcloneInventory")


def db_append_rclone_inventory(
    entries: List[dict], db_path: Optional[str] = None,
) -> int:
    """Append rows to RcloneInventory using executemany for speed."""
    if not entries:
        return 0
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        conn.executemany(
            """INSERT INTO RcloneInventory
               (VideoCode, SensorCategory, SubtitleCategory,
                FolderPath, FolderSize, FileCount, DateTimeScanned)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (e.get('VideoCode', e.get('video_code', '')),
                 e.get('SensorCategory', e.get('sensor_category')),
                 e.get('SubtitleCategory', e.get('subtitle_category')),
                 e.get('FolderPath', e.get('folder_path')),
                 int(e.get('FolderSize', e.get('folder_size', 0)) or 0),
                 int(e.get('FileCount', e.get('file_count', 0)) or 0),
                 e.get('DateTimeScanned', e.get('scan_datetime')))
                for e in entries
            ],
        )
        return len(entries)


# ── DedupRecords ─────────────────────────────────────────────────────────


def _session_id_to_identifier_suffix(session_id: Any) -> str:
    """Sanitize a session id for safe use as a SQL identifier suffix."""
    return re.sub(r'[^0-9A-Za-z_]', '_', str(session_id))


def _dedup_rollback_table(session_id: str) -> str:
    return f"DedupRecordsRollback_{_session_id_to_identifier_suffix(session_id)}"


def _ensure_dedup_rollback_table(conn, session_id: str) -> str:
    table = _dedup_rollback_table(session_id)
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {table} (
            DedupRecordId INTEGER PRIMARY KEY,
            OldRowJson TEXT NOT NULL
        )"""
    )
    return table


def _snapshot_dedup_rows_for_rollback(conn, session_id: Optional[str], rows) -> None:
    if session_id is None or not rows:
        return
    table = _ensure_dedup_rollback_table(conn, session_id)
    conn.executemany(
        f"INSERT OR IGNORE INTO {table} (DedupRecordId, OldRowJson) VALUES (?, ?)",
        [
            (row['Id'], json.dumps(dict(row), ensure_ascii=False))
            for row in rows
        ],
    )


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


def db_save_dedup_records(rows: List[dict], db_path: Optional[str] = None) -> None:
    """Overwrite all dedup records (deprecated)."""
    logger.warning(
        "db_save_dedup_records is deprecated — use db_mark_records_deleted "
        "for targeted updates instead"
    )
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        conn.execute("DELETE FROM DedupRecords")
        for r in rows:
            conn.execute(
                """INSERT INTO DedupRecords
                   (VideoCode, ExistingSensor, ExistingSubtitle,
                    ExistingGdrivePath, ExistingFolderSize,
                    NewTorrentCategory, DeletionReason,
                    DateTimeDetected, IsDeleted, DateTimeDeleted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.get('VideoCode', r.get('video_code')),
                 r.get('ExistingSensor', r.get('existing_sensor')),
                 r.get('ExistingSubtitle', r.get('existing_subtitle')),
                 r.get('ExistingGdrivePath', r.get('existing_gdrive_path')),
                 int(r.get('ExistingFolderSize', r.get('existing_folder_size', 0)) or 0),
                 r.get('NewTorrentCategory', r.get('new_torrent_category')),
                 r.get('DeletionReason', r.get('deletion_reason')),
                 r.get('DateTimeDetected', r.get('detect_datetime')),
                 1 if str(r.get('IsDeleted', r.get('is_deleted', 'False'))).lower() in ('true', '1') else 0,
                 r.get('DateTimeDeleted', r.get('delete_datetime'))),
            )


# ── PikpakHistory ────────────────────────────────────────────────────────


def db_append_pikpak_history(
    record: dict,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Append a PikPak transfer record.

    *session_id*: tags the row for X3 rollback; defaults to
    :func:`get_active_session_id`.
    """
    sid = _resolve_session_id(session_id)
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO PikpakHistory
               (TorrentHash, TorrentName, Category, MagnetUri,
                DateTimeAddedToQb, DateTimeDeletedFromQb,
                DateTimeUploadedToPikpak, TransferStatus, ErrorMessage,
                SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.get('TorrentHash', record.get('torrent_hash')),
             record.get('TorrentName', record.get('torrent_name')),
             record.get('Category', record.get('category')),
             record.get('MagnetUri', record.get('magnet_uri')),
             record.get('DateTimeAddedToQb', record.get('added_to_qb_date')),
             record.get('DateTimeDeletedFromQb', record.get('deleted_from_qb_date')),
             record.get('DateTimeUploadedToPikpak', record.get('uploaded_to_pikpak_date')),
             record.get('TransferStatus', record.get('transfer_status')),
             record.get('ErrorMessage', record.get('error_message')),
             sid),
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


# ── Delegating wrappers (pending full migration) ────────────────────────


def db_append_dedup_record(
    record: dict,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Append a single dedup record. Returns the new row id, or -1 if duplicate."""
    _ensure_imports()
    sid = _resolve_session_id(session_id)
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO DedupRecords
               (VideoCode, ExistingSensor, ExistingSubtitle,
                ExistingGdrivePath, ExistingFolderSize,
                NewTorrentCategory, DeletionReason,
                DateTimeDetected, IsDeleted, DateTimeDeleted, SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.get('VideoCode', record.get('video_code')),
             record.get('ExistingSensor', record.get('existing_sensor')),
             record.get('ExistingSubtitle', record.get('existing_subtitle')),
             record.get('ExistingGdrivePath', record.get('existing_gdrive_path')),
             int(record.get('ExistingFolderSize', record.get('existing_folder_size', 0)) or 0),
             record.get('NewTorrentCategory', record.get('new_torrent_category')),
             record.get('DeletionReason', record.get('deletion_reason')),
             record.get('DateTimeDetected', record.get('detect_datetime')),
             1 if str(record.get('IsDeleted', record.get('is_deleted', 'False'))).lower() in ('true', '1') else 0,
             record.get('DateTimeDeleted', record.get('delete_datetime')),
             sid),
        )
        if cur.rowcount == 0:
            return -1
        return cur.lastrowid


def db_mark_records_deleted(
    path_datetime_pairs: List[Tuple[str, str]],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Mark specific dedup records as deleted by gdrive path."""
    if not path_datetime_pairs:
        return 0
    grouped: Dict[str, List[str]] = {}
    for path, dt in path_datetime_pairs:
        if not path:
            continue
        grouped.setdefault(dt, []).append(path)
    if not grouped:
        return 0
    _ensure_imports()
    sid = _resolve_session_id(session_id)
    CHUNK = 90
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        updated = 0
        for dt, paths in grouped.items():
            for i in range(0, len(paths), CHUNK):
                chunk = paths[i:i + CHUNK]
                placeholders = ','.join('?' for _ in chunk)
                if sid is not None:
                    rows = conn.execute(
                        f"SELECT * FROM DedupRecords "
                        f"WHERE ExistingGdrivePath IN ({placeholders}) AND IsDeleted=0",
                        chunk,
                    ).fetchall()
                    _snapshot_dedup_rows_for_rollback(conn, sid, rows)
                    cur = conn.execute(
                        f"UPDATE DedupRecords "
                        f"SET IsDeleted=1, DateTimeDeleted=?, SessionId=? "
                        f"WHERE ExistingGdrivePath IN ({placeholders}) AND IsDeleted=0",
                        [dt, sid] + chunk,
                    )
                else:
                    cur = conn.execute(
                        f"UPDATE DedupRecords SET IsDeleted=1, DateTimeDeleted=? "
                        f"WHERE ExistingGdrivePath IN ({placeholders}) AND IsDeleted=0",
                        [dt] + chunk,
                    )
                updated += cur.rowcount or 0
        return updated


def db_cleanup_deleted_records(
    older_than_days: int = 30,
    db_path: Optional[str] = None,
) -> int:
    """Remove dedup records that were deleted more than *older_than_days* ago."""
    _ensure_imports()
    cutoff = (datetime.now() - timedelta(days=older_than_days)).strftime('%Y-%m-%d %H:%M:%S')
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            "DELETE FROM DedupRecords "
            "WHERE IsDeleted=1 AND DateTimeDeleted IS NOT NULL AND DateTimeDeleted < ?",
            (cutoff,),
        )
        return cur.rowcount


def db_mark_orphan_records(
    paths: Iterable[str],
    reason_suffix: str,
    when: str,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Mark dedup pending rows as deleted with custom reason suffix appended."""
    path_list = [p for p in paths if p]
    if not path_list:
        return 0
    _ensure_imports()
    sid = _resolve_session_id(session_id)
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        updated = 0
        for path in path_list:
            if sid is not None:
                rows = conn.execute(
                    "SELECT * FROM DedupRecords "
                    "WHERE ExistingGdrivePath = ? AND IsDeleted = 0",
                    (path,),
                ).fetchall()
                _snapshot_dedup_rows_for_rollback(conn, sid, rows)
                cur = conn.execute(
                    """UPDATE DedupRecords
                       SET IsDeleted = 1,
                           DateTimeDeleted = ?,
                           DeletionReason = TRIM(
                             COALESCE(DeletionReason, '') || ' ' || ?
                           ),
                           SessionId = ?
                       WHERE ExistingGdrivePath = ? AND IsDeleted = 0""",
                    (when, reason_suffix, sid, path),
                )
            else:
                cur = conn.execute(
                    """UPDATE DedupRecords
                       SET IsDeleted = 1,
                           DateTimeDeleted = ?,
                           DeletionReason = TRIM(
                             COALESCE(DeletionReason, '') || ' ' || ?
                           )
                       WHERE ExistingGdrivePath = ? AND IsDeleted = 0""",
                    (when, reason_suffix, path),
                )
            updated += cur.rowcount
        return updated


def db_open_rclone_staging(
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Initialise this session's RcloneInventory staging table.

    Returns the staging table name, or None when no session_id is
    available — callers in that case should keep using the legacy
    clear+append flow.
    """
    sid = _resolve_session_id(session_id)
    if sid is None:
        return None
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        return _open_rclone_staging(conn, sid)


def db_append_rclone_staging(
    entries: List[dict],
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> int:
    """Append rows to this session's RcloneInventory staging table."""
    if not entries:
        return 0
    sid = _resolve_session_id(session_id)
    _ensure_imports()
    if sid is None:
        with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
            conn.executemany(
                """INSERT INTO RcloneInventory
                   (VideoCode, SensorCategory, SubtitleCategory,
                    FolderPath, FolderSize, FileCount, DateTimeScanned)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (e.get('VideoCode', e.get('video_code', '')),
                     e.get('SensorCategory', e.get('sensor_category')),
                     e.get('SubtitleCategory', e.get('subtitle_category')),
                     e.get('FolderPath', e.get('folder_path')),
                     int(e.get('FolderSize', e.get('folder_size', 0)) or 0),
                     int(e.get('FileCount', e.get('file_count', 0)) or 0),
                     e.get('DateTimeScanned', e.get('scan_datetime')))
                    for e in entries
                ],
            )
            return len(entries)
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        return _append_rclone_staging(conn, entries, sid)


def db_merge_rclone_inventory_from_stage(
    session_id: Any = _SESSION_ID_SENTINEL,
    years: Optional[Iterable[str]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Merge this session's staging rows into selected RcloneInventory years."""
    sid = _resolve_session_id(session_id)
    if sid is None:
        raise ValueError(
            "db_merge_rclone_inventory_from_stage requires an active "
            "session_id (set via set_active_session_id or pass explicitly)."
        )
    if years is None:
        raise ValueError("db_merge_rclone_inventory_from_stage requires years")
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        return _merge_rclone_inventory_from_stage(conn, sid, years)


def db_drop_rclone_staging(
    session_id: str,
    db_path: Optional[str] = None,
) -> None:
    """DROP TABLE IF EXISTS RcloneInventoryStaging_<session_id> (idempotent)."""
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        _drop_rclone_staging(conn, session_id)


def db_delete_rclone_inventory_paths(
    paths: Iterable[str],
    db_path: Optional[str] = None,
) -> int:
    """Bulk delete RcloneInventory rows by FolderPath.

    Uses chunked IN (...) deletes (90 per batch for D1 parameter cap safety).
    """
    _ensure_imports()
    path_list = [p for p in paths if p]
    if not path_list:
        return 0
    CHUNK = 90
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        deleted = 0
        for i in range(0, len(path_list), CHUNK):
            chunk = path_list[i:i + CHUNK]
            placeholders = ','.join('?' for _ in chunk)
            cur = conn.execute(
                f"DELETE FROM RcloneInventory WHERE FolderPath IN ({placeholders})",
                chunk,
            )
            deleted += cur.rowcount or 0
        return deleted


# ── InventoryAlignNoExactMatch ──────────────────────────────────────────


def db_upsert_align_no_exact_match(
    video_code: str,
    reason: str = 'exact_video_code_not_found',
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> None:
    """Record a video code that had no exact match on JavDB search."""
    sid = _resolve_session_id(session_id)
    normalized = video_code.strip().upper()
    if not normalized:
        return
    _ensure_imports()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        conn.execute(
            """INSERT INTO InventoryAlignNoExactMatch
                   (VideoCode, Reason, DateTimeRecorded, SessionId)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(VideoCode) DO UPDATE SET
                   Reason = excluded.Reason,
                   DateTimeRecorded = excluded.DateTimeRecorded,
                   SessionId = excluded.SessionId
               WHERE InventoryAlignNoExactMatch.Reason
                     IS NOT excluded.Reason""",
            (normalized, reason, now, sid),
        )


def db_load_align_no_exact_match_codes(db_path: Optional[str] = None) -> set:
    """Return the set of normalised video codes previously marked as no-exact-match."""
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT VideoCode FROM InventoryAlignNoExactMatch"
        ).fetchall()
    return {r['VideoCode'] for r in rows}


def db_delete_align_no_exact_match(
    video_code: str,
    db_path: Optional[str] = None,
) -> None:
    """Remove a video code from the no-exact-match table."""
    _ensure_imports()
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
        conn.execute(
            "DELETE FROM InventoryAlignNoExactMatch WHERE VideoCode = ?",
            (video_code.strip().upper(),),
        )
