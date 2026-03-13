#!/usr/bin/env python3
"""One-time migration: import all existing CSV files into SQLite.

Phase 1 — Data tables:
  - parsed_movies_history.csv  →  parsed_movies_history
  - rclone_inventory.csv       →  rclone_inventory
  - dedup.csv                  →  dedup_records
  - pikpak_bridge_history.csv  →  pikpak_history
  - proxy_bans.csv             →  proxy_bans

Phase 2 — Report CSVs:
  - reports/DailyReport/*.csv  →  report_sessions + report_rows
  - reports/AdHoc/*.csv        →  report_sessions + report_rows

Usage:
    python3 migration/csv_to_sqlite.py [--reports-dir reports] [--db-path reports/javdb_autospider.db] [--dry-run] [--verify]
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


# =====================================================================
# Phase 1 — Data-table migration helpers
# =====================================================================

def migrate_history(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate parsed_movies_history.csv → parsed_movies_history table."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping history: {csv_path} not found")
        return 0

    from utils.db import get_db
    count = 0
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    href_seen = {}
    for row in rows:
        href = row.get('href', '')
        if not href:
            continue
        existing = href_seen.get(href)
        if existing is None:
            href_seen[href] = row
        else:
            existing_date = existing.get('update_datetime', existing.get('update_date', ''))
            current_date = row.get('update_datetime', row.get('update_date', ''))
            if current_date > existing_date:
                href_seen[href] = row

    unique_rows = list(href_seen.values())
    logger.info(f"History: {len(rows)} rows, {len(unique_rows)} unique hrefs")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(unique_rows)} history records")
        return len(unique_rows)

    with get_db(db_path) as conn:
        for row in unique_rows:
            create_dt = row.get('create_datetime', row.get('create_date', row.get('parsed_date', '')))
            update_dt = row.get('update_datetime', row.get('update_date', row.get('parsed_date', '')))
            last_visited = row.get('last_visited_datetime', '') or update_dt

            conn.execute(
                """INSERT OR REPLACE INTO parsed_movies_history
                   (href, phase, video_code, create_datetime, update_datetime,
                    last_visited_datetime, hacked_subtitle, hacked_no_subtitle,
                    subtitle, no_subtitle)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row.get('href', ''),
                 int(row.get('phase', 0) or 0),
                 row.get('video_code', ''),
                 create_dt, update_dt, last_visited,
                 row.get('hacked_subtitle', ''),
                 row.get('hacked_no_subtitle', ''),
                 row.get('subtitle', ''),
                 row.get('no_subtitle', '')),
            )
            count += 1

    logger.info(f"Migrated {count} history records")
    return count


def migrate_inventory(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate rclone_inventory.csv → rclone_inventory table."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping inventory: {csv_path} not found")
        return 0

    from utils.db import get_db
    count = 0
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    logger.info(f"Inventory: {len(rows)} rows")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(rows)} inventory records")
        return len(rows)

    with get_db(db_path) as conn:
        conn.execute("DELETE FROM rclone_inventory")
        for row in rows:
            conn.execute(
                """INSERT INTO rclone_inventory
                   (video_code, sensor_category, subtitle_category,
                    folder_path, folder_size, file_count, scan_datetime)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (row.get('video_code', ''),
                 row.get('sensor_category', ''),
                 row.get('subtitle_category', ''),
                 row.get('folder_path', ''),
                 int(row.get('folder_size', 0) or 0),
                 int(row.get('file_count', 0) or 0),
                 row.get('scan_datetime', '')),
            )
            count += 1

    logger.info(f"Migrated {count} inventory records")
    return count


def migrate_dedup(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate dedup.csv → dedup_records table."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping dedup: {csv_path} not found")
        return 0

    from utils.db import get_db
    count = 0
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    logger.info(f"Dedup: {len(rows)} rows")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(rows)} dedup records")
        return len(rows)

    with get_db(db_path) as conn:
        conn.execute("DELETE FROM dedup_records")
        for row in rows:
            is_del = str(row.get('is_deleted', 'False')).lower() in ('true', '1')
            conn.execute(
                """INSERT INTO dedup_records
                   (video_code, existing_sensor, existing_subtitle,
                    existing_gdrive_path, existing_folder_size,
                    new_torrent_category, deletion_reason,
                    detect_datetime, is_deleted, delete_datetime)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row.get('video_code', ''),
                 row.get('existing_sensor', ''),
                 row.get('existing_subtitle', ''),
                 row.get('existing_gdrive_path', ''),
                 int(row.get('existing_folder_size', 0) or 0),
                 row.get('new_torrent_category', ''),
                 row.get('deletion_reason', ''),
                 row.get('detect_datetime', ''),
                 1 if is_del else 0,
                 row.get('delete_datetime', '')),
            )
            count += 1

    logger.info(f"Migrated {count} dedup records")
    return count


def migrate_pikpak(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate pikpak_bridge_history.csv → pikpak_history table."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping pikpak: {csv_path} not found")
        return 0

    from utils.db import get_db
    count = 0
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    logger.info(f"PikPak history: {len(rows)} rows")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(rows)} pikpak records")
        return len(rows)

    with get_db(db_path) as conn:
        conn.execute("DELETE FROM pikpak_history")
        for row in rows:
            conn.execute(
                """INSERT INTO pikpak_history
                   (torrent_hash, torrent_name, category, magnet_uri,
                    added_to_qb_date, deleted_from_qb_date,
                    uploaded_to_pikpak_date, transfer_status, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row.get('torrent_hash', ''),
                 row.get('torrent_name', ''),
                 row.get('category', ''),
                 row.get('magnet_uri', ''),
                 row.get('added_to_qb_date', ''),
                 row.get('deleted_from_qb_date', ''),
                 row.get('uploaded_to_pikpak_date', ''),
                 row.get('transfer_status', ''),
                 row.get('error_message', '')),
            )
            count += 1

    logger.info(f"Migrated {count} pikpak records")
    return count


def migrate_proxy_bans(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate proxy_bans.csv → proxy_bans table."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping proxy_bans: {csv_path} not found")
        return 0

    from utils.db import get_db
    count = 0
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    logger.info(f"Proxy bans: {len(rows)} rows")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(rows)} proxy ban records")
        return len(rows)

    with get_db(db_path) as conn:
        conn.execute("DELETE FROM proxy_bans")
        for row in rows:
            conn.execute(
                "INSERT INTO proxy_bans (proxy_name, ban_time, unban_time) VALUES (?, ?, ?)",
                (row.get('proxy_name', ''),
                 row.get('ban_time', ''),
                 row.get('unban_time', '')),
            )
            count += 1

    logger.info(f"Migrated {count} proxy ban records")
    return count


# =====================================================================
# Phase 2 — Report CSV migration helpers
# =====================================================================

REPORT_COLUMNS = [
    'href', 'video_code', 'page', 'actor', 'rate', 'comment_number',
    'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
    'size_hacked_subtitle', 'size_hacked_no_subtitle', 'size_subtitle', 'size_no_subtitle',
]

_ADHOC_RE = re.compile(
    r'^Javdb_AdHoc_'
    r'(?P<url_type>actors|directors|makers|publishers|series|video_codes|rankings)'
    r'_(?P<display>.+)_(?P<date>\d{8})\.csv$'
)

_ADHOC_FALLBACK_RE = re.compile(
    r'^Javdb_AdHoc_(?P<part>.+)_(?P<date>\d{8})\.csv$'
)

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

    date_m = re.search(r'(\d{8})', base)
    return {
        'report_type': 'adhoc' if is_adhoc_dir else 'daily',
        'report_date': date_m.group(1) if date_m else '19700101',
        'url_type': None,
        'display_name': None,
    }


def collect_csv_files(reports_dir: str) -> list:
    """Collect all report CSV files (excluding data-table CSVs).

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

    Session creation and row insertion run inside a single transaction so
    a failure in row insertion does not leave an orphaned session record.

    Returns dict with keys: session_id, row_count, skipped.
    """
    from datetime import datetime
    from utils.db import get_db

    meta = parse_csv_filename(filename, is_adhoc)

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

    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM report_sessions WHERE csv_filename = ?", (filename,)
        ).fetchone()
        if existing:
            logger.debug(f"Already migrated: {filename} (session_id={existing[0]})")
            return {'session_id': existing[0], 'row_count': 0, 'skipped': True}

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            """INSERT INTO report_sessions
               (report_type, report_date, url_type, display_name,
                url, start_page, end_page, csv_filename, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (meta['report_type'], meta['report_date'],
             meta.get('url_type'), meta.get('display_name'),
             None, None, None, filename, created_at),
        )
        session_id = cur.lastrowid

        for row in rows:
            conn.execute(
                """INSERT INTO report_rows
                   (session_id, href, video_code, page, actor, rate,
                    comment_number, hacked_subtitle, hacked_no_subtitle,
                    subtitle, no_subtitle, size_hacked_subtitle,
                    size_hacked_no_subtitle, size_subtitle, size_no_subtitle)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id,
                 row.get('href', ''), row.get('video_code', ''),
                 int(row['page']) if row.get('page') else None,
                 row.get('actor', ''),
                 float(row['rate']) if row.get('rate') else None,
                 int(row['comment_number']) if row.get('comment_number') else None,
                 row.get('hacked_subtitle', ''), row.get('hacked_no_subtitle', ''),
                 row.get('subtitle', ''), row.get('no_subtitle', ''),
                 row.get('size_hacked_subtitle', ''),
                 row.get('size_hacked_no_subtitle', ''),
                 row.get('size_subtitle', ''),
                 row.get('size_no_subtitle', '')),
            )

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


# =====================================================================
# CLI entry point
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description='Migrate all CSV files to SQLite')
    parser.add_argument('--reports-dir', default='reports', help='Reports directory')
    parser.add_argument('--db-path', default=None, help='SQLite database path')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be migrated')
    parser.add_argument('--verify', action='store_true',
                        help='Verify migrated report data against original CSVs')
    args = parser.parse_args()

    reports_dir = args.reports_dir
    db_path = args.db_path or os.path.join(reports_dir, 'javdb_autospider.db')

    logger.info("=" * 60)
    logger.info("CSV → SQLite MIGRATION")
    logger.info(f"Reports dir: {reports_dir}")
    logger.info(f"Database: {db_path}")
    if args.dry_run:
        logger.info("[DRY RUN MODE]")
    if args.verify:
        logger.info("[VERIFY MODE]")
    logger.info("=" * 60)

    import utils.db
    utils.db.DB_PATH = db_path
    utils.db.init_db(db_path, force=True)

    # ── Phase 1: data tables ─────────────────────────────────────────
    logger.info("-" * 60)
    logger.info("Phase 1: Data-table CSVs")
    logger.info("-" * 60)

    table_totals = {}
    table_totals['history'] = migrate_history(
        os.path.join(reports_dir, 'parsed_movies_history.csv'), db_path, args.dry_run)
    table_totals['inventory'] = migrate_inventory(
        os.path.join(reports_dir, 'rclone_inventory.csv'), db_path, args.dry_run)
    table_totals['dedup'] = migrate_dedup(
        os.path.join(reports_dir, 'dedup.csv'), db_path, args.dry_run)
    table_totals['pikpak'] = migrate_pikpak(
        os.path.join(reports_dir, 'pikpak_bridge_history.csv'), db_path, args.dry_run)
    table_totals['proxy_bans'] = migrate_proxy_bans(
        os.path.join(reports_dir, 'proxy_bans.csv'), db_path, args.dry_run)

    # ── Phase 2: report CSVs ─────────────────────────────────────────
    logger.info("-" * 60)
    logger.info("Phase 2: Report CSVs")
    logger.info("-" * 60)

    csv_files = collect_csv_files(reports_dir)
    logger.info(f"Found {len(csv_files)} report CSVs")

    report_sessions = 0
    report_rows = 0
    report_skipped = 0
    verify_ok = 0
    verify_fail = 0

    for csv_path, filename, is_adhoc in csv_files:
        result = migrate_single_csv(csv_path, filename, is_adhoc, db_path, args.dry_run)
        if result['skipped']:
            report_skipped += 1
        else:
            report_sessions += 1
            report_rows += result['row_count']
            logger.info(f"Migrated: {filename} → session_id={result['session_id']}, "
                        f"{result['row_count']} rows")

        if args.verify and result.get('session_id') and not args.dry_run:
            ok = verify_session(result['session_id'], csv_path, db_path)
            if ok:
                verify_ok += 1
            else:
                verify_fail += 1
                logger.error(f"VERIFY FAILED: {filename}")

    # ── Summary ──────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("MIGRATION SUMMARY")
    logger.info("  Phase 1 — Data tables:")
    for table, count in table_totals.items():
        logger.info(f"    {table}: {count} records")
    logger.info(f"    Total: {sum(table_totals.values())} records")
    logger.info("  Phase 2 — Report CSVs:")
    logger.info(f"    CSVs found: {len(csv_files)}")
    logger.info(f"    Sessions created: {report_sessions}")
    logger.info(f"    Rows inserted: {report_rows}")
    logger.info(f"    Skipped: {report_skipped}")
    if args.verify:
        logger.info(f"    Verified OK: {verify_ok}")
        logger.info(f"    Verified FAIL: {verify_fail}")
    if not args.dry_run:
        db_size = os.path.getsize(db_path)
        logger.info(f"  Database size: {db_size / 1024:.1f} KB")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
