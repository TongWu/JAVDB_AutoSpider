"""Operations DB helpers extracted from `utils.infra.db`.

Includes the X3-rollback "staging-then-swap" replace-inventory pattern:
each run writes to its own ``RcloneInventoryStaging_<session_id>`` table
and only swaps the contents into ``RcloneInventory`` once all rows are
persisted. A failed run (or a concurrent run that started later)
therefore can never overwrite a prior run's good inventory: cleanup
just drops the staging table.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple, Union
from javdb.spider.contracts import (
    get_video_code,
    get_sensor_category,
    get_subtitle_category,
    get_folder_path,
)


_STAGING_DDL = """
CREATE TABLE {staging} (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT NOT NULL,
    SensorCategory TEXT,
    SubtitleCategory TEXT,
    FolderPath TEXT,
    FolderSize INTEGER,
    FileCount INTEGER,
    DateTimeScanned TEXT
)
"""


def _staging_table_name(session_id: str) -> str:
    """Derive the per-session staging table name (validated for safety).

    Session ids are TEXT snowflakes (post-2026-05-13) that contain ``.``
    and ``-`` separators; both are illegal in unquoted SQL identifiers,
    so map every non-``[A-Za-z0-9_]`` byte to ``_`` before splicing into
    the table name. Empty or whitespace-only ids are rejected.
    """
    if session_id is None or str(session_id).strip() == "":
        raise ValueError(f"session_id must be non-empty, got {session_id!r}")
    suffix = re.sub(r'[^0-9A-Za-z_]', '_', str(session_id))
    return f"RcloneInventoryStaging_{suffix}"


def _normalize_inventory_entry(entry: dict) -> Tuple[str, str, str, str, int, int, str]:
    return (
        get_video_code(entry, ""),
        get_sensor_category(entry),
        get_subtitle_category(entry),
        get_folder_path(entry),
        int(entry.get("FolderSize", entry.get("folder_size", 0)) or 0),
        int(entry.get("FileCount", entry.get("file_count", 0)) or 0),
        entry.get("DateTimeScanned", entry.get("scan_datetime")),
    )


def open_rclone_staging(conn, session_id: str) -> str:
    """Drop+recreate the staging table for *session_id*.

    Returns the staging table name. Idempotent: safe to call at the start
    of every scan (a previously-aborted run's leftover staging is wiped).
    """
    staging = _staging_table_name(session_id)
    conn.execute(f"DROP TABLE IF EXISTS {staging}")
    conn.execute(_STAGING_DDL.format(staging=staging))
    return staging


def append_rclone_staging(
    conn,
    entries: List[dict],
    session_id: str,
) -> int:
    """INSERT *entries* into this session's staging table."""
    if not entries:
        return 0
    staging = _staging_table_name(session_id)
    conn.executemany(
        f"""
        INSERT INTO {staging}
        (VideoCode, SensorCategory, SubtitleCategory, FolderPath,
         FolderSize, FileCount, DateTimeScanned)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [_normalize_inventory_entry(entry) for entry in entries],
    )
    return len(entries)


def _d1_failure_count(conn) -> int:
    try:
        return int(getattr(conn, "d1_failure_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _execute_inventory_batch(conn, statements, *, action: str) -> None:
    before_failures = _d1_failure_count(conn)
    batch = getattr(conn, "batch_execute", None)
    if callable(batch):
        batch(statements)
        if _d1_failure_count(conn) > before_failures:
            raise RuntimeError(
                f"D1 mirror failed during rclone inventory {action}; "
                "rolling back SQLite changes"
            )
    else:
        for sql, params in statements:
            conn.execute(sql, params)


def swap_rclone_inventory(conn, session_id: str) -> int:
    """Atomically replace ``RcloneInventory`` with this session's staging rows.

    Issues DELETE + INSERT FROM staging + DROP staging in a single D1
    batch (when running against D1) so any failure mid-swap leaves the
    main table either entirely old or entirely new — never partially
    rewritten by a concurrent run.

    Returns the number of rows that ended up in ``RcloneInventory``.
    """
    staging = _staging_table_name(session_id)
    main_cols = (
        "VideoCode, SensorCategory, SubtitleCategory, FolderPath, "
        "FolderSize, FileCount, DateTimeScanned"
    )
    statements = [
        ("DELETE FROM RcloneInventory", ()),
        (
            f"INSERT INTO RcloneInventory ({main_cols}) "
            f"SELECT {main_cols} FROM {staging}",
            (),
        ),
        (f"DROP TABLE {staging}", ()),
    ]

    # Plain SQLite relies on the surrounding ``with get_db()`` transaction.
    # DualConnection suppresses D1 write exceptions, so detect that signal and
    # re-raise here; callers must not mark a live inventory swap successful
    # unless both backends applied this all-or-nothing batch.
    _execute_inventory_batch(conn, statements, action="swap")

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM RcloneInventory"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["n"])
    except (KeyError, TypeError):
        return int(row[0])


def merge_rclone_inventory_from_stage(
    conn,
    session_id: str,
    years: Iterable[str],
) -> int:
    """Refresh only the requested year prefixes from this session's staging."""
    staging = _staging_table_name(session_id)
    normalized_years = [
        str(year).strip().strip("/")
        for year in years
        if str(year).strip().strip("/")
    ]
    if not normalized_years:
        raise ValueError(
            "merge_rclone_inventory_from_stage requires at least one year"
        )

    where_parts = []
    params = []
    for year in normalized_years:
        where_parts.append("(FolderPath = ? OR FolderPath LIKE ?)")
        params.extend([year, f"{year}/%"])

    main_cols = (
        "VideoCode, SensorCategory, SubtitleCategory, FolderPath, "
        "FolderSize, FileCount, DateTimeScanned"
    )
    statements = [
        (
            "DELETE FROM RcloneInventory WHERE " + " OR ".join(where_parts),
            tuple(params),
        ),
        (
            f"INSERT INTO RcloneInventory ({main_cols}) "
            f"SELECT {main_cols} FROM {staging} "
            "WHERE " + " OR ".join(where_parts),
            tuple(params),
        ),
        (f"DROP TABLE {staging}", ()),
    ]

    _execute_inventory_batch(conn, statements, action="merge")

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM RcloneInventory"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["n"])
    except (KeyError, TypeError):
        return int(row[0])


def drop_rclone_staging(conn, session_id: str) -> None:
    """Drop this session's staging table (idempotent; rollback cleanup)."""
    staging = _staging_table_name(session_id)
    conn.execute(f"DROP TABLE IF EXISTS {staging}")


def replace_rclone_inventory(
    conn,
    entries: List[dict],
    *,
    session_id: Optional[str] = None,
) -> int:
    """Replace the inventory table.

    *session_id* (preferred): use the staging-then-swap path so concurrent
    runs cannot stomp on each other and so a failed run can be rolled
    back by simply dropping the staging table.

    No *session_id*: fall back to the legacy single-shot DELETE+INSERT
    path. Used by tests and by ad-hoc maintenance callers that don't
    care about per-run isolation.
    """
    if session_id is not None:
        open_rclone_staging(conn, session_id)
        append_rclone_staging(conn, entries, session_id)
        return swap_rclone_inventory(conn, session_id)

    conn.execute("DELETE FROM RcloneInventory")
    if not entries:
        return 0
    conn.executemany(
        """
        INSERT INTO RcloneInventory
        (VideoCode, SensorCategory, SubtitleCategory, FolderPath, FolderSize, FileCount, DateTimeScanned)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [_normalize_inventory_entry(entry) for entry in entries],
    )
    return len(entries)


# ── OperationsRepo (ADR-005 PR-1) ─────────────────────────────────────
#
# Typed surface over the write-domain function family in
# ``javdb/storage/db/db_operations.py``. See ADR-005 amendment 2 for
# the ``__init__(*, db_path=None)`` rationale.


class OperationsRepo:
    """Thin typed wrapper over the Operations domain (`operations.db`).

    Wraps RcloneInventory, DedupRecords, and PikpakHistory operations
    by delegating to the ``db_*`` functions in
    ``javdb/storage/db/db_operations.py``. PR-2 will inline the SQL
    here and retire the underlying functions.

    The conn-taking helpers earlier in this file (``open_rclone_staging``
    et al.) are pre-existing internal utilities used by the function
    family itself; they intentionally remain at module level rather
    than becoming Repo methods, because their callers already manage
    their own conn.
    """

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    # ── Rclone inventory ──────────────────────────────────────────

    def load_rclone_inventory(self) -> List[dict]:
        """Return every row in RcloneInventory as a list of dicts."""
        from javdb.storage.db.db_operations import db_load_rclone_inventory
        return db_load_rclone_inventory(db_path=self._db_path)

    def replace_rclone_inventory(self, entries: List[dict]) -> int:
        """Replace the full RcloneInventory atomically. Returns row count."""
        from javdb.storage.db.db_operations import db_replace_rclone_inventory
        return db_replace_rclone_inventory(
            entries=entries, db_path=self._db_path,
        )

    def swap_rclone_inventory(self, session_id: str) -> int:
        """Promote this session's staging table into RcloneInventory."""
        from javdb.storage.db.db_operations import db_swap_rclone_inventory
        return db_swap_rclone_inventory(
            session_id=session_id, db_path=self._db_path,
        )

    def clear_rclone_inventory(self) -> None:
        """Truncate RcloneInventory. Forensic / reset use only."""
        from javdb.storage.db.db_operations import db_clear_rclone_inventory
        db_clear_rclone_inventory(db_path=self._db_path)

    def append_rclone_inventory(
        self, entries: List[dict], *, session_id: str,
    ) -> int:
        """Append rows directly to RcloneInventory (rare; prefer swap)."""
        from javdb.storage.db.db_operations import db_append_rclone_inventory
        return db_append_rclone_inventory(
            entries=entries, session_id=session_id, db_path=self._db_path,
        )

    # ── Dedup records ─────────────────────────────────────────────

    def load_dedup_records(self) -> List[dict]:
        """Return every DedupRecords row as a list of dicts."""
        from javdb.storage.db.db_operations import db_load_dedup_records
        return db_load_dedup_records(db_path=self._db_path)

    def save_dedup_records(self, rows: List[dict]) -> None:
        """Bulk-replace DedupRecords with ``rows`` (post-dedup commit)."""
        from javdb.storage.db.db_operations import db_save_dedup_records
        db_save_dedup_records(rows=rows, db_path=self._db_path)

    def append_dedup_record(
        self, *, session_id: str, payload: dict,
    ) -> None:
        """Append a single DedupRecords row tagged with ``session_id``."""
        from javdb.storage.db.db_operations import db_append_dedup_record
        db_append_dedup_record(
            session_id=session_id, payload=payload, db_path=self._db_path,
        )

    # ── PikpakHistory ─────────────────────────────────────────────

    def append_pikpak_history(
        self, *, session_id: str, payload: dict,
    ) -> None:
        """Append one PikpakHistory row tagged with ``session_id``."""
        from javdb.storage.db.db_operations import db_append_pikpak_history
        db_append_pikpak_history(
            session_id=session_id, payload=payload, db_path=self._db_path,
        )

    # ── Dedup lifecycle ──────────────────────────────────────────

    def mark_records_deleted(
        self,
        path_datetime_pairs: List[Tuple[str, str]],
        *,
        session_id: Optional[str] = None,
    ) -> int:
        """Mark dedup records as deleted by gdrive path."""
        from javdb.storage.db.db_operations import db_mark_records_deleted
        return db_mark_records_deleted(
            path_datetime_pairs,
            db_path=self._db_path,
            session_id=session_id,
        )

    def cleanup_deleted_records(self, older_than_days: int = 30) -> int:
        """Remove dedup records deleted more than *older_than_days* ago."""
        from javdb.storage.db.db_operations import db_cleanup_deleted_records
        return db_cleanup_deleted_records(
            older_than_days=older_than_days, db_path=self._db_path,
        )

    def mark_orphan_records(
        self,
        paths: Iterable[str],
        reason_suffix: str,
        when: str,
        *,
        session_id: Optional[str] = None,
    ) -> int:
        """Mark dedup pending rows as deleted with custom reason suffix."""
        from javdb.storage.db.db_operations import db_mark_orphan_records
        return db_mark_orphan_records(
            paths,
            reason_suffix=reason_suffix,
            when=when,
            db_path=self._db_path,
            session_id=session_id,
        )

    # ── Rclone staging ───────────────────────────────────────────

    def open_rclone_staging(self, session_id: str) -> Optional[str]:
        """Initialise this session's staging table. Returns table name."""
        from javdb.storage.db.db_operations import db_open_rclone_staging
        return db_open_rclone_staging(
            session_id=session_id, db_path=self._db_path,
        )

    def append_rclone_staging(
        self, entries: List[dict], session_id: str,
    ) -> int:
        """Append rows to this session's staging table."""
        from javdb.storage.db.db_operations import db_append_rclone_staging
        return db_append_rclone_staging(
            entries, session_id=session_id, db_path=self._db_path,
        )

    def merge_rclone_inventory_from_stage(
        self, session_id: str, years: Iterable[str],
    ) -> int:
        """Merge staging rows into selected RcloneInventory year prefixes."""
        from javdb.storage.db.db_operations import (
            db_merge_rclone_inventory_from_stage,
        )
        return db_merge_rclone_inventory_from_stage(
            session_id=session_id, years=years, db_path=self._db_path,
        )

    def drop_rclone_staging(self, session_id: str) -> None:
        """Drop this session's staging table (idempotent)."""
        from javdb.storage.db.db_operations import db_drop_rclone_staging
        db_drop_rclone_staging(session_id=session_id, db_path=self._db_path)

    def delete_rclone_inventory_paths(self, paths: Iterable[str]) -> int:
        """Bulk delete RcloneInventory rows by FolderPath."""
        from javdb.storage.db.db_operations import db_delete_rclone_inventory_paths
        return db_delete_rclone_inventory_paths(
            paths, db_path=self._db_path,
        )

    # ── InventoryAlignNoExactMatch ───────────────────────────────

    def upsert_align_no_exact_match(
        self,
        video_code: str,
        reason: str = 'exact_video_code_not_found',
        *,
        session_id: Optional[str] = None,
    ) -> None:
        """Record a video code that had no exact match on JavDB search."""
        from javdb.storage.db.db_operations import db_upsert_align_no_exact_match
        db_upsert_align_no_exact_match(
            video_code,
            reason=reason,
            db_path=self._db_path,
            session_id=session_id,
        )

    def load_align_no_exact_match_codes(self) -> set:
        """Return normalised video codes previously marked as no-exact-match."""
        from javdb.storage.db.db_operations import (
            db_load_align_no_exact_match_codes,
        )
        return db_load_align_no_exact_match_codes(db_path=self._db_path)

    def delete_align_no_exact_match(self, video_code: str) -> None:
        """Remove a video code from the no-exact-match table."""
        from javdb.storage.db.db_operations import db_delete_align_no_exact_match
        db_delete_align_no_exact_match(video_code, db_path=self._db_path)

    # ── EmailNotificationHistory ──────────────────────────────────

    def append_email_history(
        self,
        session_id: Optional[str],
        recipient: str,
        subject: str,
        status: str,
        *,
        error: Optional[str] = None,
        attachments: Optional[List[str]] = None,
        created_by: str = 'pipeline',
    ) -> None:
        """Insert a record after a send attempt.

        Args:
            session_id: Active pipeline session id (may be None).
            recipient: Destination email address.
            subject: Email subject line.
            status: 'sent' | 'failed' | 'resent'.
            error: Error message when status='failed'.
            attachments: List of attachment filenames (stored as JSON).
            created_by: 'pipeline' | 'manual' | 'resend'.
        """
        from javdb.storage.db.db_connection import get_db, OPERATIONS_DB_PATH
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        attachment_names = json.dumps(attachments) if attachments is not None else None
        with get_db(self._db_path or OPERATIONS_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO EmailNotificationHistory
                    (SessionId, Recipient, Subject, Status,
                     ErrorMessage, AttachmentNames, SentAt, CreatedBy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, recipient, subject, status,
                 error, attachment_names, now, created_by),
            )

    def list_email_history(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Tuple[List[dict], Optional[str]]:
        """List email notification history, newest first, with optional filtering.

        Uses keyset pagination on Id (descending). ``cursor`` is a base64-encoded Id;
        only rows with Id < cursor are returned.

        Args:
            status: Optional status filter ('sent', 'failed', 'resent').
            limit: Maximum number of rows to return (default 50).
            cursor: Opaque pagination token from a previous call.

        Returns:
            (items, next_cursor) — next_cursor is None when no more pages.
        """
        from javdb.storage.db.db_connection import get_db, OPERATIONS_DB_PATH

        cursor_id: Optional[int] = None
        if cursor is not None:
            try:
                cursor_id = int(base64.b64decode(cursor).decode())
            except Exception:
                raise ValueError("invalid cursor")

        conditions: List[str] = []
        params: List = []
        if status is not None:
            conditions.append("Status = ?")
            params.append(status)
        if cursor_id is not None:
            conditions.append("Id < ?")
            params.append(cursor_id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT Id, SessionId, Recipient, Subject, Status,
                   ErrorMessage, AttachmentNames, SentAt, ResentAt, CreatedBy
            FROM EmailNotificationHistory
            {where}
            ORDER BY Id DESC
            LIMIT ?
        """
        fetch_limit = limit + 1
        with get_db(self._db_path or OPERATIONS_DB_PATH) as conn:
            rows = conn.execute(sql, params + [fetch_limit]).fetchall()

        items = [dict(r) for r in rows]
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]

        next_cursor: Optional[str] = None
        if has_more and items:
            next_cursor = base64.b64encode(str(items[-1]["Id"]).encode()).decode()

        return items, next_cursor

    def get_email_history_by_id(self, record_id: int) -> Optional[dict]:
        """Fetch a single EmailNotificationHistory row by Id.

        Returns the row as a dict, or None if not found.
        """
        from javdb.storage.db.db_connection import get_db, OPERATIONS_DB_PATH
        with get_db(self._db_path or OPERATIONS_DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT Id, SessionId, Recipient, Subject, Status,
                       ErrorMessage, AttachmentNames, SentAt, ResentAt, CreatedBy
                FROM EmailNotificationHistory
                WHERE Id = ?
                """,
                (record_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_email_resent(self, record_id: int) -> None:
        """Update Status='resent' and ResentAt=now() for a history row.

        Args:
            record_id: The Id of the EmailNotificationHistory row to update.
        """
        from javdb.storage.db.db_connection import get_db, OPERATIONS_DB_PATH
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        with get_db(self._db_path or OPERATIONS_DB_PATH) as conn:
            conn.execute(
                """
                UPDATE EmailNotificationHistory
                SET Status = 'resent', ResentAt = ?
                WHERE Id = ?
                """,
                (now, record_id),
            )
