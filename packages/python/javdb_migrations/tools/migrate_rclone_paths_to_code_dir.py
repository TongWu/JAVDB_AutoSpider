#!/usr/bin/env python3
"""One-shot migration: rewrite rclone paths from legacy layout to the new
``<code>/<sensor-subtitle>`` layout.

Old (pre ``scripts/rclone_group_jav.py``)::

    <root>/<year>/<actor>/<movie_code> [<sensor>-<subtitle>]

New::

    <root>/<year>/<actor>/<movie_code>/<sensor>-<subtitle>

The script rewrites:

* ``reports/rclone_inventory.csv`` — column ``folder_path``
* ``reports/operations.db`` — ``RcloneInventory.FolderPath`` and
  ``DedupRecords.ExistingGdrivePath``

Idempotent: rows whose last path segment does not match the legacy pattern
``... [<inner>]`` are left untouched, so re-running the script on already
migrated data is a safe no-op.

Examples::

    python -m packages.python.javdb_migrations.tools.migrate_rclone_paths_to_code_dir
    python -m packages.python.javdb_migrations.tools.migrate_rclone_paths_to_code_dir --dry-run
    python -m packages.python.javdb_migrations.tools.migrate_rclone_paths_to_code_dir \
        --csv reports/rclone_inventory.csv --db reports/operations.db
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.python.javdb_platform.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

# Match a trailing ``<base> [<inner>]`` (square or round brackets, no nesting).
LEAF_RE = re.compile(r"^(?P<base>.+?)\s*[\[\(](?P<inner>[^\[\]\(\)]+)[\]\)]\s*$")

DEFAULT_CSV = os.path.join("reports", "rclone_inventory.csv")
DEFAULT_DB = os.path.join("reports", "operations.db")


def convert_path(path: str) -> str | None:
    """Return the new-layout path, or ``None`` if *path* does not need rewriting.

    Only the **last** segment is inspected; the rest of the path is preserved
    verbatim so any leading remote prefix (``gdrive:...``) and arbitrary
    intermediate directories are kept intact.
    """
    if not path:
        return None
    # Preserve trailing slash semantics: split off the last non-empty segment.
    head, sep, tail = path.rpartition("/")
    if not tail:
        return None
    match = LEAF_RE.match(tail)
    if not match:
        return None
    code = match.group("base").strip()
    inner = match.group("inner").strip()
    if not code or not inner:
        return None
    new_tail = f"{code}/{inner}"
    return f"{head}{sep}{new_tail}" if sep else new_tail


# ---------------------------------------------------------------------------
# CSV migration
# ---------------------------------------------------------------------------
def migrate_csv(csv_path: str, *, dry_run: bool) -> tuple[int, int]:
    """Return ``(rewritten, total)``."""
    if not os.path.exists(csv_path):
        logger.warning("CSV not found, skipping: %s", csv_path)
        return 0, 0

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "folder_path" not in fieldnames:
        logger.warning("CSV %s has no 'folder_path' column — skipping", csv_path)
        return 0, len(rows)

    rewritten = 0
    for row in rows:
        new = convert_path(row.get("folder_path", ""))
        if new is not None:
            row["folder_path"] = new
            rewritten += 1

    logger.info(
        "CSV %s: %d/%d rows would be rewritten%s",
        csv_path, rewritten, len(rows), " (dry-run)" if dry_run else "",
    )

    if dry_run or rewritten == 0:
        return rewritten, len(rows)

    backup = f"{csv_path}.backup_rclone_paths_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(csv_path, backup)
    logger.info("CSV backup: %s", backup)

    tmp_path = f"{csv_path}.tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, csv_path)
    logger.info("CSV updated: %s (%d rows rewritten)", csv_path, rewritten)
    return rewritten, len(rows)


# ---------------------------------------------------------------------------
# SQLite migration
# ---------------------------------------------------------------------------
def _migrate_table(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    pk: str = "Id",
    dry_run: bool,
) -> tuple[int, int]:
    cur = conn.execute(
        f"SELECT {pk}, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
    )
    rows = cur.fetchall()
    updates: list[tuple[str, int]] = []
    for row_id, path in rows:
        new = convert_path(path)
        if new is not None and new != path:
            updates.append((new, row_id))

    logger.info(
        "DB %s.%s: %d/%d rows would be rewritten%s",
        table, column, len(updates), len(rows), " (dry-run)" if dry_run else "",
    )

    if dry_run or not updates:
        return len(updates), len(rows)

    conn.executemany(
        f"UPDATE {table} SET {column} = ? WHERE {pk} = ?",
        updates,
    )
    return len(updates), len(rows)


def migrate_db(db_path: str, *, dry_run: bool) -> None:
    if not os.path.exists(db_path):
        logger.warning("DB not found, skipping: %s", db_path)
        return

    backup = None
    if not dry_run:
        backup = f"{db_path}.backup_rclone_paths_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(db_path, backup)
        logger.info("DB backup: %s", backup)

    with sqlite3.connect(db_path) as probe:
        existing = {
            row[0] for row in probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    # Each table is migrated in its OWN connection / transaction so that a
    # ``sqlite3.IntegrityError`` from the DedupRecords UNIQUE index (active
    # paths can collide post-rewrite when old and new layout rows coexist)
    # does NOT roll back the already-applied RcloneInventory updates.
    if "RcloneInventory" in existing:
        with sqlite3.connect(db_path) as conn:
            _migrate_table(
                conn,
                table="RcloneInventory",
                column="FolderPath",
                dry_run=dry_run,
            )
            if not dry_run:
                conn.commit()
                logger.info("DB updated (RcloneInventory): %s", db_path)
    else:
        logger.warning("Table RcloneInventory missing in %s", db_path)

    if "DedupRecords" in existing:
        try:
            with sqlite3.connect(db_path) as conn:
                _migrate_table(
                    conn,
                    table="DedupRecords",
                    column="ExistingGdrivePath",
                    dry_run=dry_run,
                )
                if not dry_run:
                    conn.commit()
                    logger.info("DB updated (DedupRecords): %s", db_path)
        except sqlite3.IntegrityError as exc:
            # Re-raise after logging so the operator sees both the offending
            # context and the underlying constraint message. Dry-runs do not
            # create backups or commit either table, so keep that guidance
            # separate from the real migration recovery path.
            if dry_run:
                logger.error(
                    "DedupRecords migration hit an IntegrityError on db=%s "
                    "(dry-run, table=DedupRecords, column=ExistingGdrivePath): "
                    "No backup was created and no database changes were committed "
                    "by dry-run. Inspect/fix conflicting DedupRecords rows and "
                    "re-run migration. Original exception: %s",
                    db_path, exc,
                )
            else:
                logger.error(
                    "DedupRecords migration hit an IntegrityError on db=%s "
                    "(backup=%s, table=DedupRecords, column=ExistingGdrivePath): "
                    "RcloneInventory changes were committed while DedupRecords rolled back. "
                    "Restore from the backup path or inspect/fix conflicting DedupRecords "
                    "rows and re-run migration. Original exception: %s",
                    db_path, backup, exc,
                )
            raise
    else:
        logger.warning("Table DedupRecords missing in %s", db_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite rclone paths from legacy '<code> [<sensor-subtitle>]' "
            "layout to the new '<code>/<sensor-subtitle>' layout."
        ),
    )
    parser.add_argument("--csv", default=DEFAULT_CSV, help=f"Inventory CSV (default: {DEFAULT_CSV})")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Operations SQLite DB (default: {DEFAULT_DB})")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument("--skip-csv", action="store_true", help="Skip CSV migration")
    parser.add_argument("--skip-db", action="store_true", help="Skip DB migration")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger.info("=" * 60)
    logger.info("RCLONE PATH MIGRATION (legacy → code-dir layout)")
    logger.info("CSV: %s", args.csv if not args.skip_csv else "<skipped>")
    logger.info("DB:  %s", args.db if not args.skip_db else "<skipped>")
    logger.info("Dry-run: %s", args.dry_run)
    logger.info("=" * 60)

    if not args.skip_csv:
        migrate_csv(args.csv, dry_run=args.dry_run)
    if not args.skip_db:
        migrate_db(args.db, dry_run=args.dry_run)

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
