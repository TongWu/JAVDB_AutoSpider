#!/usr/bin/env python3
"""One-shot migration: strip the configured rclone root folder prefix from
stored paths so CSV/SQLite only store **relative paths**.

This rewrites:

* ``reports/rclone_inventory.csv`` — column ``folder_path``
* ``reports/operations.db`` — ``RcloneInventory.FolderPath`` and
  ``DedupRecords.ExistingGdrivePath``

The root folder is resolved via ``RCLONE_FOLDER_PATH`` (preferred) or
``RCLONE_ROOT_FOLDER`` (fallback) from config.

Idempotent: already-relative paths are left unchanged.
"""

from __future__ import annotations

import argparse
import csv
import os
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
from packages.python.javdb_integrations.rclone_helper import strip_drive_name, strip_root_folder, get_configured_root_folder

setup_logging()
logger = get_logger(__name__)

DEFAULT_CSV = os.path.join("reports", "rclone_inventory.csv")
DEFAULT_DB = os.path.join("reports", "operations.db")


def _backup(path: str, label: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{path}.backup_strip_root_{label}_{ts}"
    shutil.copy2(path, backup_path)
    logger.info("Backup: %s", backup_path)
    return backup_path


def migrate_csv(csv_path: str, *, root: str, dry_run: bool) -> tuple[int, int]:
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
        old = row.get("folder_path", "") or ""
        new = strip_root_folder(strip_drive_name(old), root=root)
        if new != old and new:
            row["folder_path"] = new
            rewritten += 1
        elif new == "" and old:
            # Root folder itself; store as empty string.
            row["folder_path"] = ""
            rewritten += 1

    logger.info(
        "CSV %s: %d/%d rows would be rewritten%s",
        csv_path, rewritten, len(rows), " (dry-run)" if dry_run else "",
    )
    if dry_run or rewritten == 0:
        return rewritten, len(rows)

    _backup(csv_path, "csv")
    tmp = f"{csv_path}.tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, csv_path)
    return rewritten, len(rows)


def _migrate_table(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    root: str,
    dry_run: bool,
    pk: str = "Id",
) -> tuple[int, int]:
    cur = conn.execute(
        f"SELECT {pk}, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
    )
    rows = cur.fetchall()
    updates: list[tuple[str, int]] = []
    for row_id, path in rows:
        new = strip_root_folder(strip_drive_name(path), root=root)
        if new != path:
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


def migrate_db(db_path: str, *, root: str, dry_run: bool) -> None:
    if not os.path.exists(db_path):
        logger.warning("DB not found, skipping: %s", db_path)
        return

    if not dry_run:
        _backup(db_path, "db")

    with sqlite3.connect(db_path) as conn:
        existing = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "RcloneInventory" in existing:
            _migrate_table(conn, table="RcloneInventory", column="FolderPath", root=root, dry_run=dry_run)
        if "DedupRecords" in existing:
            _migrate_table(conn, table="DedupRecords", column="ExistingGdrivePath", root=root, dry_run=dry_run)
        if not dry_run:
            conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strip configured RCLONE root folder from stored paths (store relative paths only).",
    )
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-csv", action="store_true")
    parser.add_argument("--skip-db", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = get_configured_root_folder()
    logger.info("=" * 60)
    logger.info("STRIP RCLONE ROOT FOLDER (store relative paths)")
    logger.info("Root: %s", root or "<empty>")
    logger.info("CSV:  %s", args.csv if not args.skip_csv else "<skipped>")
    logger.info("DB:   %s", args.db if not args.skip_db else "<skipped>")
    logger.info("Dry-run: %s", args.dry_run)
    logger.info("=" * 60)

    if not root:
        logger.warning("Root folder is empty; nothing will be stripped.")

    if not args.skip_csv:
        migrate_csv(args.csv, root=root, dry_run=args.dry_run)
    if not args.skip_db:
        migrate_db(args.db, root=root, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

