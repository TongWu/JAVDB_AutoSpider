#!/usr/bin/env python3
"""Standalone migration: split single SQLite database (v6) into three databases (v7).

The same split migration runs *automatically* on every ``init_db()`` call
when the old single DB exists and the three new files do not.  This script
is provided for explicit control (CI, manual rollout, pre-flight backup).

Databases created:
  - history.db    — MovieHistory, TorrentHistory
  - reports.db    — ReportSessions, ReportMovies, ReportTorrents,
                    SpiderStats, UploaderStats, PikpakStats
  - operations.db — RcloneInventory, DedupRecords, PikpakHistory, ProxyBans

Usage:
    python3 migration/migrate_v6_to_v7_split.py [--db-path PATH] [--backup] [--verify] [--dry-run]
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

DEFAULT_DB_PATH = os.path.join('reports', 'javdb_autospider.db')

_HISTORY_TABLES = ['MovieHistory', 'TorrentHistory']
_REPORTS_TABLES = [
    'ReportSessions', 'ReportMovies', 'ReportTorrents',
    'SpiderStats', 'UploaderStats', 'PikpakStats',
]
_OPERATIONS_TABLES = ['RcloneInventory', 'DedupRecords', 'PikpakHistory', 'ProxyBans']


def _detect_version(db_path: str) -> int:
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
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.backup_v6_{ts}"
    shutil.copy2(db_path, backup_path)
    logger.info(f"Backup created: {backup_path}")
    return backup_path


def verify_split(reports_dir: str) -> bool:
    import utils.db as db_mod

    ok = True
    for db_name, expected_tables in [
        (db_mod.HISTORY_DB_PATH, _HISTORY_TABLES),
        (db_mod.REPORTS_DB_PATH, _REPORTS_TABLES),
        (db_mod.OPERATIONS_DB_PATH, _OPERATIONS_TABLES),
    ]:
        if not os.path.exists(db_name):
            logger.error(f"Missing database file: {db_name}")
            ok = False
            continue

        conn = sqlite3.connect(db_name)
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        version = 0
        if 'SchemaVersion' in tables:
            row = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
            version = row[0] if row else 0

        logger.info(f"\n  {db_name} (v{version}):")
        for t in expected_tables:
            if t not in tables:
                logger.error(f"    Missing table: {t}")
                ok = False
            else:
                count = _table_count(conn, t)
                logger.info(f"    {t}: {count} rows")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != 'ok':
            logger.error(f"    Integrity check failed: {integrity}")
            ok = False
        else:
            logger.info(f"    Integrity: OK")

        conn.close()

    return ok


def main():
    parser = argparse.ArgumentParser(
        description='Split single SQLite database (v6) into three databases (v7)')
    parser.add_argument('--db-path', default=None,
                        help=f'Path to old single SQLite database (default: {DEFAULT_DB_PATH})')
    parser.add_argument('--backup', action='store_true',
                        help='Create a backup of the old DB before migration')
    parser.add_argument('--verify', action='store_true',
                        help='Run integrity checks after migration')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show current state without migrating')
    args = parser.parse_args()

    db_path = args.db_path or DEFAULT_DB_PATH

    if not os.path.exists(db_path):
        logger.error(f"Database not found: {db_path}")
        sys.exit(1)

    current = _detect_version(db_path)
    logger.info("=" * 60)
    logger.info("SCHEMA MIGRATION v6 → v7 (single DB → three DBs)")
    logger.info(f"Source database: {db_path}")
    logger.info(f"Current schema version: {current}")
    logger.info(f"Database size: {os.path.getsize(db_path) / 1024:.1f} KB")
    logger.info("=" * 60)

    if current < 6:
        logger.info("Database is below v6. Run migrate_v5_to_v6.py first.")
        sys.exit(1)

    import utils.db as db_mod

    reports_dir = os.path.dirname(db_path)
    db_mod.DB_PATH = db_path
    db_mod.HISTORY_DB_PATH = os.path.join(reports_dir, 'history.db')
    db_mod.REPORTS_DB_PATH = os.path.join(reports_dir, 'reports.db')
    db_mod.OPERATIONS_DB_PATH = os.path.join(reports_dir, 'operations.db')

    already_split = all(os.path.exists(p) for p in [
        db_mod.HISTORY_DB_PATH, db_mod.REPORTS_DB_PATH, db_mod.OPERATIONS_DB_PATH])

    if already_split:
        logger.info("All three target databases already exist. No migration needed.")
        if args.verify:
            ok = verify_split(reports_dir)
            sys.exit(0 if ok else 1)
        sys.exit(0)

    if args.dry_run:
        logger.info("[DRY RUN] Would split into:")
        logger.info(f"  {db_mod.HISTORY_DB_PATH}    — {', '.join(_HISTORY_TABLES)}")
        logger.info(f"  {db_mod.REPORTS_DB_PATH}    — {', '.join(_REPORTS_TABLES)}")
        logger.info(f"  {db_mod.OPERATIONS_DB_PATH} — {', '.join(_OPERATIONS_TABLES)}")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        logger.info("\nSource table row counts:")
        for t in _HISTORY_TABLES + _REPORTS_TABLES + _OPERATIONS_TABLES:
            count = _table_count(conn, t)
            logger.info(f"  {t}: {count}")
        conn.close()
        sys.exit(0)

    if args.backup:
        backup_db(db_path)

    logger.info("Running split migration via init_db() ...")
    db_mod.init_db(force=True)

    logger.info("Migration complete.")
    for p in [db_mod.HISTORY_DB_PATH, db_mod.REPORTS_DB_PATH, db_mod.OPERATIONS_DB_PATH]:
        if os.path.exists(p):
            logger.info(f"  {p}: {os.path.getsize(p) / 1024:.1f} KB")

    if args.verify:
        logger.info("-" * 60)
        logger.info("Running verification ...")
        ok = verify_split(reports_dir)
        if ok:
            logger.info("Verification PASSED")
        else:
            logger.error("Verification FAILED")
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("Done.")


if __name__ == '__main__':
    main()
