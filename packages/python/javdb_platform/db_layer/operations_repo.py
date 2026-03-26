"""Operations DB helpers extracted from `utils.infra.db`."""

from __future__ import annotations

from typing import List, Tuple
from packages.python.javdb_core.contracts import (
    get_video_code,
    get_sensor_category,
    get_subtitle_category,
    get_folder_path,
)


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


def replace_rclone_inventory(conn, entries: List[dict]) -> int:
    """Replace inventory table using one DELETE + executemany INSERT."""
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
