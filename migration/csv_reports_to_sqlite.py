#!/usr/bin/env python3
"""One-time migration: import existing report CSVs into SQLite.

Scans reports/DailyReport/ and reports/AdHoc/ for CSV files.
For each CSV:
  1. Parse report_type, report_date, url_type, display_name from the filename
  2. Create a report_sessions row
  3. Read CSV rows and insert them into report_rows

Filenames follow one of these patterns:
  - Javdb_TodayTitle_YYYYMMDD.csv                       (daily or early adhoc)
  - Javdb_AdHoc_{url_type}_{display_name}_{YYYYMMDD}.csv (adhoc)
  - Javdb_AdHoc_{url_part}_{YYYYMMDD}.csv                (adhoc fallback)

Usage:
    python3 migration/csv_reports_to_sqlite.py [--reports-dir reports] [--db-path reports/javdb_autospider.db] [--dry-run] [--verify]
"""

import argparse
import csv
import os
import re
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

REPORT_COLUMNS = [
    'href', 'video_code', 'page', 'actor', 'rate', 'comment_number',
    'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
    'size_hacked_subtitle', 'size_hacked_no_subtitle', 'size_subtitle', 'size_no_subtitle',
]

# Regex for the AdHoc filename pattern: Javdb_AdHoc_{url_type}_{display}_{YYYYMMDD}.csv
_ADHOC_RE = re.compile(
    r'^Javdb_AdHoc_'
    r'(?P<url_type>actors|directors|makers|publishers|series|video_codes|rankings)'
    r'_(?P<display>.+)_(?P<date>\d{8})\.csv$'
)

# Regex for generic adhoc fallback: Javdb_AdHoc_{something}_{YYYYMMDD}.csv
_ADHOC_FALLBACK_RE = re.compile(
    r'^Javdb_AdHoc_(?P<part>.+)_(?P<date>\d{8})\.csv$'
)

# Regex for daily: Javdb_TodayTitle_YYYYMMDD.csv
_DAILY_RE = re.compile(r'^Javdb_TodayTitle_(?P<date>\d{8})\.csv$')


def parse_csv_filename(filename: str, is_adhoc_dir: bool) -> dict:
    """Parse metadata from a report CSV filename.

    Returns dict with keys: report_type, report_date, url_type, display_name.
    """
    base = os.path.basename(filename)

    m = _ADHOC_RE.match(base)
    if m:
        return {
            'report_type': 'adhoc',
            'report_date': m.group('date'),
            'url_type': m.group('url_type'),
            'display_name': m.group('display'),
        }

    m = _DAILY_RE.match(base)
    if m:
        return {
            'report_type': 'adhoc' if is_adhoc_dir else 'daily',
            'report_date': m.group('date'),
            'url_type': None,
            'display_name': None,
        }

    m = _ADHOC_FALLBACK_RE.match(base)
    if m:
        return {
            'report_type': 'adhoc',
            'report_date': m.group('date'),
            'url_type': None,
            'display_name': m.group('part'),
        }

    # Last resort: try to extract YYYYMMDD from filename
    date_m = re.search(r'(\d{8})', base)
    return {
        'report_type': 'adhoc' if is_adhoc_dir else 'daily',
        'report_date': date_m.group(1) if date_m else '19700101',
        'url_type': None,
        'display_name': None,
    }


def collect_csv_files(reports_dir: str) -> list:
    """Collect all report CSV files (excluding Dedup, history, inventory, etc.).

    Returns list of (full_path, db_filename, is_adhoc).
    db_filename is normally just the basename, but when the same basename
    appears under both DailyReport/ and AdHoc/, the adhoc copy is stored
    with a ``[adhoc]`` prefix to avoid unique-index collisions.
    """
    files = []
    skip_names = {
        'parsed_movies_history.csv', 'parsed_movies_history_backup.csv',
        'rclone_inventory.csv', 'pikpak_bridge_history.csv',
        'proxy_bans.csv', 'dedup.csv',
    }

    seen_basenames: dict[str, str] = {}  # basename -> first subdir_name

    for subdir_name in ('DailyReport', 'AdHoc'):
        subdir = os.path.join(reports_dir, subdir_name)
        if not os.path.isdir(subdir):
            continue
        is_adhoc = subdir_name == 'AdHoc'
        for root, _dirs, filenames in os.walk(subdir):
            for fn in sorted(filenames):
                if not fn.endswith('.csv'):
                    continue
                if fn in skip_names:
                    continue
                full_path = os.path.join(root, fn)

                if fn in seen_basenames and seen_basenames[fn] != subdir_name:
                    db_filename = f"[adhoc]{fn}" if is_adhoc else f"[daily]{fn}"
                else:
                    db_filename = fn
                    seen_basenames[fn] = subdir_name

                files.append((full_path, db_filename, is_adhoc))

    files.sort(key=lambda t: t[1])
    return files


def migrate_single_csv(csv_path: str, filename: str, is_adhoc: bool,
                        db_path: str, dry_run: bool) -> dict:
    """Migrate one report CSV → report_sessions + report_rows.

    Returns dict with keys: session_id, row_count, skipped.
    """
    from utils.db import get_db, db_create_report_session, db_insert_report_rows

    meta = parse_csv_filename(filename, is_adhoc)

    # Read rows
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as e:
        logger.warning(f"Failed to read {csv_path}: {e}")
        return {'session_id': None, 'row_count': 0, 'skipped': True}

    if not rows:
        logger.debug(f"Empty CSV: {filename}")

    if dry_run:
        return {'session_id': None, 'row_count': len(rows), 'skipped': False}

    # Check if already migrated (unique index on csv_filename)
    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM report_sessions WHERE csv_filename = ?", (filename,)
        ).fetchone()
        if existing:
            logger.debug(f"Already migrated: {filename} (session_id={existing[0]})")
            return {'session_id': existing[0], 'row_count': 0, 'skipped': True}

    session_id = db_create_report_session(
        report_type=meta['report_type'],
        report_date=meta['report_date'],
        csv_filename=filename,
        url_type=meta.get('url_type'),
        display_name=meta.get('display_name'),
        db_path=db_path,
    )

    if rows:
        db_insert_report_rows(session_id, rows, db_path=db_path)

    return {'session_id': session_id, 'row_count': len(rows), 'skipped': False}


def verify_session(session_id: int, csv_path: str, db_path: str) -> bool:
    """Verify a migrated session matches the original CSV content."""
    from utils.db import db_get_report_rows

    db_rows = db_get_report_rows(session_id, db_path=db_path)

    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            csv_rows = list(reader)
    except Exception:
        return False

    if len(db_rows) != len(csv_rows):
        logger.warning(f"Row count mismatch: DB={len(db_rows)} CSV={len(csv_rows)} in {csv_path}")
        return False

    for i, (db_row, csv_row) in enumerate(zip(db_rows, csv_rows)):
        for col in REPORT_COLUMNS:
            db_val = str(db_row.get(col, '') or '')
            csv_val = str(csv_row.get(col, '') or '')
            if col == 'rate':
                try:
                    if db_val and csv_val:
                        if abs(float(db_val) - float(csv_val)) > 0.01:
                            logger.warning(f"Row {i} col {col}: DB={db_val} CSV={csv_val}")
                            return False
                    elif db_val != csv_val:
                        if not (db_val in ('', 'None', '0.0') and csv_val in ('', 'None', '0.0')):
                            return False
                    continue
                except (ValueError, TypeError):
                    pass
            elif col in ('page', 'comment_number'):
                try:
                    if db_val and csv_val:
                        if int(float(db_val)) != int(float(csv_val)):
                            logger.warning(f"Row {i} col {col}: DB={db_val} CSV={csv_val}")
                            return False
                    continue
                except (ValueError, TypeError):
                    pass
            if db_val != csv_val:
                logger.warning(f"Row {i} col {col}: DB='{db_val}' CSV='{csv_val}' in {csv_path}")
                return False
    return True


def main():
    parser = argparse.ArgumentParser(description='Migrate report CSVs to SQLite')
    parser.add_argument('--reports-dir', default='reports', help='Reports directory')
    parser.add_argument('--db-path', default=None, help='SQLite database path')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be migrated')
    parser.add_argument('--verify', action='store_true', help='Verify migrated data against CSVs')
    args = parser.parse_args()

    reports_dir = args.reports_dir
    db_path = args.db_path or os.path.join(reports_dir, 'javdb_autospider.db')

    logger.info("=" * 60)
    logger.info("REPORT CSV → SQLite MIGRATION")
    logger.info(f"Reports dir: {reports_dir}")
    logger.info(f"Database: {db_path}")
    if args.dry_run:
        logger.info("[DRY RUN MODE]")
    if args.verify:
        logger.info("[VERIFY MODE]")
    logger.info("=" * 60)

    import utils.db
    utils.db.DB_PATH = db_path
    utils.db.init_db(db_path)

    csv_files = collect_csv_files(reports_dir)
    logger.info(f"Found {len(csv_files)} report CSVs")

    total_sessions = 0
    total_rows = 0
    skipped = 0
    verify_ok = 0
    verify_fail = 0

    for csv_path, filename, is_adhoc in csv_files:
        result = migrate_single_csv(csv_path, filename, is_adhoc, db_path, args.dry_run)
        if result['skipped']:
            skipped += 1
        else:
            total_sessions += 1
            total_rows += result['row_count']
            logger.info(f"Migrated: {filename} → session_id={result['session_id']}, {result['row_count']} rows")

        if args.verify and result.get('session_id') and not args.dry_run:
            ok = verify_session(result['session_id'], csv_path, db_path)
            if ok:
                verify_ok += 1
            else:
                verify_fail += 1
                logger.error(f"VERIFY FAILED: {filename}")

    logger.info("=" * 60)
    logger.info("MIGRATION SUMMARY")
    logger.info(f"  CSVs found: {len(csv_files)}")
    logger.info(f"  Sessions created: {total_sessions}")
    logger.info(f"  Rows inserted: {total_rows}")
    logger.info(f"  Skipped (already migrated or unreadable): {skipped}")
    if args.verify:
        logger.info(f"  Verified OK: {verify_ok}")
        logger.info(f"  Verified FAIL: {verify_fail}")
    if not args.dry_run:
        db_size = os.path.getsize(db_path)
        logger.info(f"  Database size: {db_size / 1024:.1f} KB")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
