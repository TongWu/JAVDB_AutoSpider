"""Normalize TEXT datetime values stored in SQLite to ``YYYY-MM-DD HH:MM:SS``."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime

_CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_US_PARSE_FORMATS = (
    "%m/%d/%y %H:%M:%S",
    "%m/%d/%y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


def normalize_storage_datetime(value: str | None) -> str:
    """Return a canonical datetime string, or unchanged / empty for blanks.

    Handles:
    - Already canonical ``YYYY-MM-DD HH:MM:SS``
    - Date-only ``YYYY-MM-DD`` → midnight
    - US-style ``M/D/YY H:MM`` (and 4-digit year variants)
    - ISO strings containing ``T`` (naive output; Z stripped to naive local-less)
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if _CANONICAL_RE.match(s):
        return s
    if _DATE_ONLY_RE.match(s):
        return f"{s} 00:00:00"
    for fmt in _US_PARSE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    if "T" in s:
        try:
            t = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return s


# Known DateTime* TEXT columns per report DB (basename → (table, column)).
SQLITE_REPORT_DATETIME_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "history.db": (
        ("MovieHistory", "DateTimeCreated"),
        ("MovieHistory", "DateTimeUpdated"),
        ("MovieHistory", "DateTimeVisited"),
        ("TorrentHistory", "DateTimeCreated"),
        ("TorrentHistory", "DateTimeUpdated"),
    ),
    "reports.db": (
        ("ReportSessions", "DateTimeCreated"),
        ("SpiderStats", "DateTimeCreated"),
        ("UploaderStats", "DateTimeCreated"),
        ("PikpakStats", "DateTimeCreated"),
    ),
    "operations.db": (
        ("RcloneInventory", "DateTimeScanned"),
        ("DedupRecords", "DateTimeDetected"),
        ("DedupRecords", "DateTimeDeleted"),
        ("PikpakHistory", "DateTimeAddedToQb"),
        ("PikpakHistory", "DateTimeDeletedFromQb"),
        ("PikpakHistory", "DateTimeUploadedToPikpak"),
        ("ProxyBans", "DateTimeBanned"),
        ("ProxyBans", "DateTimeUnbanned"),
    ),
}


def _list_datetime_columns(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = [r[0] for r in cur.fetchall() if r[0] != "SchemaVersion"]
    out: list[tuple[str, str]] = []
    for t in tables:
        for row in conn.execute(f'PRAGMA table_info("{t}")').fetchall():
            col = row[1]
            if col.startswith("DateTime"):
                out.append((t, col))
    return out


def rewrite_datetime_text_columns(db_path: str, dry_run: bool = False) -> tuple[int, int, int]:
    """Scan known DateTime TEXT columns and rewrite non-canonical values.

    Returns ``(rows_scanned, rows_updated, rows_still_noncanonical)``.
    Commits when ``dry_run`` is False and at least one row was updated.
    """
    if not os.path.isfile(db_path):
        return (0, 0, 0)

    base = os.path.basename(db_path)
    pairs = SQLITE_REPORT_DATETIME_COLUMNS.get(base)

    conn = sqlite3.connect(db_path)
    try:
        if pairs is None:
            pairs = tuple(_list_datetime_columns(conn))

        scanned = updated = skipped = 0
        for table, col in pairs:
            try:
                cur = conn.execute(
                    f'SELECT rowid, "{col}" FROM "{table}" '
                    f'WHERE "{col}" IS NOT NULL AND TRIM("{col}") != ""'
                )
            except sqlite3.OperationalError:
                continue

            for rowid, raw in cur.fetchall():
                scanned += 1
                s = str(raw).strip()
                if _CANONICAL_RE.match(s):
                    # Strip-only fix: DB may still hold leading/trailing whitespace.
                    if str(raw) == s:
                        continue
                    updated += 1
                    if not dry_run:
                        conn.execute(
                            f'UPDATE "{table}" SET "{col}" = ? WHERE rowid = ?',
                            (s, rowid),
                        )
                    continue
                new_val = normalize_storage_datetime(s)
                if new_val == s:
                    skipped += 1
                    continue
                updated += 1
                if not dry_run:
                    conn.execute(
                        f'UPDATE "{table}" SET "{col}" = ? WHERE rowid = ?',
                        (new_val, rowid),
                    )

        if not dry_run and updated:
            conn.commit()
    finally:
        conn.close()

    return (scanned, updated, skipped)
