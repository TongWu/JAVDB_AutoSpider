#!/usr/bin/env python3
"""CLI for ``utils.sqlite_datetime.rewrite_datetime_text_columns`` — see module docstring."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.logging_config import setup_logging, get_logger
from utils.sqlite_datetime import rewrite_datetime_text_columns

setup_logging()
logger = get_logger(__name__)

DEFAULT_DBS = (
    os.path.join("reports", "history.db"),
    os.path.join("reports", "reports.db"),
    os.path.join("reports", "operations.db"),
)


def _backup_db(db_path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_normalize_dt_{ts}"
    shutil.copy2(db_path, backup_path)
    logger.info("Backup: %s", backup_path)
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize TEXT DateTime* columns in report SQLite DBs to "
            "YYYY-MM-DD HH:MM:SS."
        )
    )
    parser.add_argument(
        "--db",
        nargs="*",
        default=None,
        help="SQLite files (default: reports/history.db reports/reports.db reports/operations.db)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes")
    parser.add_argument("--no-backup", action="store_true", help="Skip .backup_* copy")
    args = parser.parse_args()

    paths = args.db if args.db else list(DEFAULT_DBS)
    logger.info("=" * 60)
    logger.info("Normalize SQLite TEXT datetimes → YYYY-MM-DD HH:MM:SS")
    logger.info("Dry-run: %s", args.dry_run)
    logger.info("=" * 60)

    total_upd = 0
    for db_path in paths:
        if not args.dry_run and not args.no_backup and os.path.isfile(db_path):
            _backup_db(db_path)
        scanned, updated, skipped = rewrite_datetime_text_columns(db_path, args.dry_run)
        logger.info(
            "%s: scanned=%d updated=%d still_noncanonical=%d",
            db_path,
            scanned,
            updated,
            skipped,
        )
        total_upd += updated

    if args.dry_run:
        logger.info("Dry-run complete (no writes).")
    else:
        logger.info("Done. Total rows updated: %d", total_upd)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
