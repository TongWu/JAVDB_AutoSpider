#!/usr/bin/env python3
"""Standalone migration: upgrade SQLite database from schema v5 (or earlier) to v6.

This script wraps the ``_migrate_v5_to_v6`` logic already embedded in
``packages.python.javdb_platform.db.init_db`` but adds:
  - Optional ``--backup`` to snapshot the DB file before mutating it.
  - Optional ``--verify`` to run basic integrity checks after migration.
  - Dry-run mode.

The same migration runs *automatically* on every ``init_db()`` call when
the DB version is below 6, so this script is only needed when you want
explicit control (e.g. CI, manual rollout, or pre-flight backup).

Changes performed:
  - parsed_movies_history → MovieHistory + TorrentHistory
  - rclone_inventory      → RcloneInventory
  - dedup_records          → DedupRecords
  - pikpak_history         → PikpakHistory
  - proxy_bans             → ProxyBans
  - report_sessions        → ReportSessions
  - report_rows            → ReportMovies + ReportTorrents
  - spider_stats           → SpiderStats
  - uploader_stats         → UploaderStats
  - pikpak_stats           → PikpakStats
  - All column names      → BigCamelCase

Usage:
    python3 packages/python/javdb_migrations/tools/migrate_v5_to_v6.py [--db-path PATH] [--backup] [--verify] [--dry-run]
"""

import argparse
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

setup_logging()
logger = get_logger(__name__)

DEFAULT_DB_PATH = os.path.join('reports', 'javdb_autospider.db')

_V6_TABLES = [
    'SchemaVersion', 'MovieHistory', 'TorrentHistory',
    'RcloneInventory', 'DedupRecords', 'PikpakHistory', 'ProxyBans',
    'ReportSessions', 'ReportMovies', 'ReportTorrents',
    'SpiderStats', 'UploaderStats', 'PikpakStats',
]

_OLD_TABLES = [
    'parsed_movies_history', 'rclone_inventory', 'dedup_records',
    'pikpak_history', 'proxy_bans', 'report_sessions', 'report_rows',
    'spider_stats', 'uploader_stats', 'pikpak_stats', 'schema_version',
]


def _detect_version(db_path: str) -> int:
    """Read the schema version from the database without triggering migration."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'SchemaVersion' in tables:
            row = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
            return row[0] if row else 0
        if 'schema_version' in tables:
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            return row[0] if row else 0
        return 0
    finally:
        conn.close()


def _table_count(conn, table_name: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()[0]
    except sqlite3.OperationalError:
        return -1


def backup_db(db_path: str) -> str:
    """Create a timestamped backup of the database file."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.backup_v5_{ts}"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Backup created: {backup_path}")
    return backup_path


def verify_migration(db_path: str) -> bool:
    """Run basic integrity checks on a migrated v6 database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ok = True
    try:
        version = _detect_version(db_path)
        if version != 6:
            logger.error(f"Schema version is {version}, expected 6")
            ok = False

        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        for t in _V6_TABLES:
            if t not in tables:
                logger.error(f"Missing v6 table: {t}")
                ok = False
            else:
                count = _table_count(conn, t)
                logger.info(f"  {t}: {count} rows")

        for t in _OLD_TABLES:
            if t in tables:
                logger.warning(f"Old table still exists: {t}")
                ok = False

        # FK integrity
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            logger.error(f"Foreign key violations found: {len(fk_errors)}")
            ok = False
        else:
            logger.info("Foreign key integrity: OK")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != 'ok':
            logger.error(f"Integrity check failed: {integrity}")
            ok = False
        else:
            logger.info("Database integrity: OK")

    finally:
        conn.close()

    return ok


def main():
    parser = argparse.ArgumentParser(
        description='Migrate SQLite database from schema v5 to v6 (BigCamelCase)')
    parser.add_argument('--db-path', default=None,
                        help=f'Path to SQLite database (default: {DEFAULT_DB_PATH})')
    parser.add_argument('--backup', action='store_true',
                        help='Create a backup before migration')
    parser.add_argument('--verify', action='store_true',
                        help='Run integrity checks after migration')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show current state without migrating')
    args = parser.parse_args()

    db_path = args.db_path or DEFAULT_DB_PATH

    if not os.path.exists(db_path):
        logger.error(f"Database not found: {db_path}")
        logger.info("If you need to create a new database, run the spider or csv_to_sqlite.py instead.")
        sys.exit(1)

    current = _detect_version(db_path)
    logger.info("=" * 60)
    logger.info("SCHEMA MIGRATION v5 → v6")
    logger.info(f"Database: {db_path}")
    logger.info(f"Current schema version: {current}")
    logger.info(f"Database size: {os.path.getsize(db_path) / 1024:.1f} KB")
    logger.info("=" * 60)

    if current >= 6:
        logger.info("Database is already at v6 or newer. No migration needed.")
        if args.verify:
            ok = verify_migration(db_path)
            sys.exit(0 if ok else 1)
        sys.exit(0)

    if args.dry_run:
        logger.info("[DRY RUN] Would migrate from v%d to v6", current)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        logger.info("Existing tables:")
        for t in sorted(tables):
            count = _table_count(conn, t)
            logger.info(f"  {t}: {count} rows")
        conn.close()
        sys.exit(0)

    if args.backup:
        backup_db(db_path)

    logger.info("Running migration via init_db() ...")
    import packages.python.javdb_platform.db as db_mod
    db_mod.DB_PATH = db_path
    db_mod.init_db(db_path, force=True)

    new_version = _detect_version(db_path)
    logger.info(f"Migration complete. Schema version: {new_version}")
    logger.info(f"Database size: {os.path.getsize(db_path) / 1024:.1f} KB")

    if args.verify:
        logger.info("-" * 60)
        logger.info("Running verification ...")
        ok = verify_migration(db_path)
        if ok:
            logger.info("Verification PASSED")
        else:
            logger.error("Verification FAILED")
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("Done.")


if __name__ == '__main__':
    main()
