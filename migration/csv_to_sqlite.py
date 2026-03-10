#!/usr/bin/env python3
"""One-time migration: import existing CSV files into SQLite.

Handles:
  - parsed_movies_history.csv  →  parsed_movies_history table
  - rclone_inventory.csv       →  rclone_inventory table
  - dedup.csv                  →  dedup_records table
  - pikpak_bridge_history.csv  →  pikpak_history table
  - proxy_bans.csv             →  proxy_bans table

Usage:
    python3 migration/csv_to_sqlite.py [--reports-dir reports] [--db-path reports/javdb_autospider.db] [--dry-run]
"""

import argparse
import csv
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


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


def main():
    parser = argparse.ArgumentParser(description='Migrate CSV files to SQLite')
    parser.add_argument('--reports-dir', default='reports', help='Reports directory')
    parser.add_argument('--db-path', default=None, help='SQLite database path')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be migrated')
    args = parser.parse_args()

    reports_dir = args.reports_dir
    db_path = args.db_path or os.path.join(reports_dir, 'javdb_autospider.db')

    logger.info("=" * 60)
    logger.info("CSV → SQLite MIGRATION")
    logger.info(f"Reports dir: {reports_dir}")
    logger.info(f"Database: {db_path}")
    if args.dry_run:
        logger.info("[DRY RUN MODE]")
    logger.info("=" * 60)

    import utils.db
    utils.db.DB_PATH = db_path
    utils.db.init_db(db_path)

    totals = {}

    totals['history'] = migrate_history(
        os.path.join(reports_dir, 'parsed_movies_history.csv'), db_path, args.dry_run)
    totals['inventory'] = migrate_inventory(
        os.path.join(reports_dir, 'rclone_inventory.csv'), db_path, args.dry_run)
    totals['dedup'] = migrate_dedup(
        os.path.join(reports_dir, 'dedup.csv'), db_path, args.dry_run)
    totals['pikpak'] = migrate_pikpak(
        os.path.join(reports_dir, 'pikpak_bridge_history.csv'), db_path, args.dry_run)
    totals['proxy_bans'] = migrate_proxy_bans(
        os.path.join(reports_dir, 'proxy_bans.csv'), db_path, args.dry_run)

    logger.info("=" * 60)
    logger.info("MIGRATION SUMMARY")
    for table, count in totals.items():
        logger.info(f"  {table}: {count} records")
    logger.info(f"  Total: {sum(totals.values())} records")
    if not args.dry_run:
        db_size = os.path.getsize(db_path)
        logger.info(f"  Database size: {db_size / 1024:.1f} KB")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
