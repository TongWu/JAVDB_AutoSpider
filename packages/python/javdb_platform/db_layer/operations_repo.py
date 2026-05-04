"""Operations DB helpers extracted from `utils.infra.db`.

Includes the X3-rollback "staging-then-swap" replace-inventory pattern:
each run writes to its own ``RcloneInventoryStaging_<session_id>`` table
and only swaps the contents into ``RcloneInventory`` once all rows are
persisted. A failed run (or a concurrent run that started later)
therefore can never overwrite a prior run's good inventory: cleanup
just drops the staging table.
"""

from __future__ import annotations

from typing import List, Optional, Tuple
from packages.python.javdb_core.contracts import (
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


def _staging_table_name(session_id: int) -> str:
    """Derive the per-session staging table name (validated for safety)."""
    sid = int(session_id)
    if sid <= 0:
        raise ValueError(f"session_id must be a positive int, got {session_id!r}")
    return f"RcloneInventoryStaging_{sid}"


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


def open_rclone_staging(conn, session_id: int) -> str:
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
    session_id: int,
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


def swap_rclone_inventory(conn, session_id: int) -> int:
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

    batch = getattr(conn, "batch_execute", None)
    if callable(batch):
        # D1 (and DualConnection's D1 side) treat each batch as atomic.
        batch(statements)
    else:
        # Plain SQLite — implicit transaction inside ``with get_db()``
        # already gives us all-or-nothing semantics.
        for sql, params in statements:
            conn.execute(sql, params)

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM RcloneInventory"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["n"])
    except (KeyError, TypeError):
        return int(row[0])


def drop_rclone_staging(conn, session_id: int) -> None:
    """Drop this session's staging table (idempotent; rollback cleanup)."""
    staging = _staging_table_name(session_id)
    conn.execute(f"DROP TABLE IF EXISTS {staging}")


def replace_rclone_inventory(
    conn,
    entries: List[dict],
    *,
    session_id: Optional[int] = None,
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
