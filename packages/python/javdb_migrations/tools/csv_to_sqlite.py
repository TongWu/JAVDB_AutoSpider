#!/usr/bin/env python3
"""One-time migration: import all existing CSV files into SQLite (v6 BigCamelCase schema).

Phase 1 — Data tables:
  - parsed_movies_history.csv  →  MovieHistory + TorrentHistory
  - rclone_inventory.csv        →  RcloneInventory
  - dedup.csv                  →  DedupRecords
  - pikpak_bridge_history.csv   →  PikpakHistory

Phase 2 — Report CSVs:
  - reports/DailyReport/*.csv  →  ReportSessions + ReportMovies + ReportTorrents
  - reports/AdHoc/*.csv        →  ReportSessions + ReportMovies + ReportTorrents

Usage:
    python3 packages/python/javdb_migrations/tools/csv_to_sqlite.py [--reports-dir reports] [--db-path reports/javdb_autospider.db] [--dry-run] [--verify]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.python.javdb_platform.logging_config import setup_logging, get_logger
from packages.python.javdb_platform.sqlite_datetime import normalize_storage_datetime
from apps.api.parsers.common import javdb_absolute_url
from packages.python.javdb_platform.config_helper import cfg

setup_logging()
logger = get_logger(__name__)


# Category → (SubtitleIndicator, CensorIndicator)
_CATEGORY_TO_INDICATORS = {
    'hacked_subtitle':    (1, 0),
    'hacked_no_subtitle': (0, 0),
    'subtitle':           (1, 1),
    'no_subtitle':        (0, 1),
}

# Magnet prefix pattern: [YYYY-MM-DD]
_MAGNET_DATE_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2})\](.*)$')
_BASE_URL = cfg('BASE_URL', 'https://javdb.com')


def _strip_magnet_prefix(val: str) -> tuple[str, str | None]:
    """Strip [YYYY-MM-DD] prefix from magnet value. Returns (magnet_uri, date_str or None)."""
    if not val or 'magnet:' not in val:
        return (val or '', None)
    m = _MAGNET_DATE_RE.match(val.strip())
    if m:
        return (m.group(2).strip(), m.group(1))
    return (val.strip(), None)


# =====================================================================
# Phase 1 — Data-table migration helpers
# =====================================================================

def migrate_history(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate parsed_movies_history.csv → MovieHistory + TorrentHistory tables."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping history: {csv_path} not found")
        return 0

    from packages.python.javdb_platform.db import get_db
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    href_seen = {}
    for row in rows:
        href = javdb_absolute_url(row.get('href', ''), _BASE_URL)
        if not href:
            continue
        existing = href_seen.get(href)
        if existing is None:
            clone = dict(row)
            clone['href'] = href
            href_seen[href] = clone
        else:
            existing_date = existing.get('update_datetime', existing.get('update_date', ''))
            current_date = row.get('update_datetime', row.get('update_date', ''))
            if current_date > existing_date:
                clone = dict(row)
                clone['href'] = href
                href_seen[href] = clone

    unique_rows = list(href_seen.values())
    logger.info(f"History: {len(rows)} rows, {len(unique_rows)} unique hrefs")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(unique_rows)} MovieHistory + TorrentHistory records")
        return len(unique_rows)

    movie_count = 0
    torrent_count = 0
    with get_db(db_path) as conn:
        for row in unique_rows:
            create_dt = normalize_storage_datetime(
                row.get('create_datetime', row.get('create_date', row.get('parsed_date', ''))) or ''
            )
            update_dt = normalize_storage_datetime(
                row.get('update_datetime', row.get('update_date', row.get('parsed_date', ''))) or ''
            )
            last_visited = row.get('last_visited_datetime', '') or update_dt
            last_visited = normalize_storage_datetime(last_visited or '')
            video_code = row.get('video_code', '')
            href = javdb_absolute_url(row.get('href', ''), _BASE_URL)

            # PerfectMatchIndicator = 1 when both subtitle AND hacked_subtitle have values
            sub_val = (row.get('subtitle', '') or '').strip()
            hack_sub_val = (row.get('hacked_subtitle', '') or '').strip()
            perfect_match = 1 if (sub_val and 'magnet:' in sub_val and hack_sub_val and 'magnet:' in hack_sub_val) else 0

            # Delete existing TorrentHistory before REPLACE to avoid FK constraint
            existing = conn.execute("SELECT Id FROM MovieHistory WHERE Href = ?", (href,)).fetchone()
            if existing:
                conn.execute("DELETE FROM TorrentHistory WHERE MovieHistoryId = ?", (existing[0],))

            # Eleven columns ↔ eleven bound values (actor fields empty for legacy CSV import).
            conn.execute(
                """INSERT OR REPLACE INTO MovieHistory
                   (VideoCode, Href, DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                    PerfectMatchIndicator, HiResIndicator, ActorName, ActorGender, ActorLink, SupportingActors)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    video_code,
                    href,
                    create_dt,
                    update_dt,
                    last_visited,
                    perfect_match,
                    0,
                    '',
                    '',
                    '',
                    '',
                ),
            )
            movie_id = conn.execute(
                "SELECT Id FROM MovieHistory WHERE Href = ?", (href,)
            ).fetchone()[0]
            movie_count += 1

            for cat in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
                val = row.get(cat, '') or ''
                if not val or 'magnet:' not in val:
                    continue
                magnet_uri, dt_created = _strip_magnet_prefix(val)
                size_col = f'size_{cat}'
                size_val = (row.get(size_col, '') or '').strip()
                sub_ind, cen_ind = _CATEGORY_TO_INDICATORS[cat]
                torrent_dt_created = normalize_storage_datetime((dt_created or create_dt or ''))
                torrent_dt_updated = normalize_storage_datetime(update_dt or '')

                conn.execute(
                    """INSERT OR REPLACE INTO TorrentHistory
                       (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
                        Size, FileCount, DateTimeCreated, DateTimeUpdated)
                       VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
                    (movie_id, magnet_uri, sub_ind, cen_ind, size_val, torrent_dt_created, torrent_dt_updated),
                )
                torrent_count += 1

    logger.info(f"Migrated {movie_count} MovieHistory + {torrent_count} TorrentHistory records")
    return movie_count


def migrate_inventory(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate rclone_inventory.csv → RcloneInventory table."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping inventory: {csv_path} not found")
        return 0

    from packages.python.javdb_platform.db import get_db
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    logger.info(f"Inventory: {len(rows)} rows")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(rows)} RcloneInventory records")
        return len(rows)

    count = 0
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM RcloneInventory")
        for row in rows:
            conn.execute(
                """INSERT INTO RcloneInventory
                   (VideoCode, SensorCategory, SubtitleCategory,
                    FolderPath, FolderSize, FileCount, DateTimeScanned)
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

    logger.info(f"Migrated {count} RcloneInventory records")
    return count


def migrate_dedup(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate dedup.csv → DedupRecords table."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping dedup: {csv_path} not found")
        return 0

    from packages.python.javdb_platform.db import get_db
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    logger.info(f"Dedup: {len(rows)} rows")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(rows)} DedupRecords")
        return len(rows)

    count = 0
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM DedupRecords")
        for row in rows:
            is_del = str(row.get('is_deleted', 'False')).lower() in ('true', '1')
            conn.execute(
                """INSERT INTO DedupRecords
                   (VideoCode, ExistingSensor, ExistingSubtitle,
                    ExistingGdrivePath, ExistingFolderSize,
                    NewTorrentCategory, DeletionReason,
                    DateTimeDetected, IsDeleted, DateTimeDeleted)
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

    logger.info(f"Migrated {count} DedupRecords")
    return count


# ── DedupRecord format field names (must match dedup_checker.DEDUP_FIELDNAMES) ─
_DEDUP_FIELDNAMES = [
    'video_code', 'existing_sensor', 'existing_subtitle',
    'existing_gdrive_path', 'existing_folder_size',
    'new_torrent_category', 'deletion_reason',
    'detect_datetime', 'is_deleted', 'delete_datetime',
]


def _parse_human_size(size_str: str) -> int:
    """Parse human-readable size like '4.94 GB' to bytes."""
    units = {'PB': 1024**5, 'TB': 1024**4, 'GB': 1024**3, 'MB': 1024**2, 'KB': 1024, 'B': 1}
    size_str = size_str.strip()
    for unit, multiplier in units.items():
        if size_str.upper().endswith(unit):
            try:
                return int(float(size_str[:len(size_str) - len(unit)].strip()) * multiplier)
            except (ValueError, TypeError):
                return 0
    try:
        return int(float(size_str))
    except (ValueError, TypeError):
        return 0


def _load_dedup_pending_csv(csv_path: str) -> list:
    """Load a Dedup_Pending_*.csv (DedupRecord format) into dicts."""
    rows = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except Exception as e:
        logger.warning(f"Failed to read pending CSV {csv_path}: {e}")
    return rows


def _load_dedup_report_csv(csv_path: str) -> list:
    """Load a Dedup_Report_*.csv (DeletionRecord format) and map to DedupRecord dicts."""
    rows = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                rows.append({
                    'video_code': row.get('Movie Code', ''),
                    'existing_sensor': row.get('Sensor Category', ''),
                    'existing_subtitle': row.get('Subtitle Category', ''),
                    'existing_gdrive_path': row.get('Deleted Folder Path', ''),
                    'existing_folder_size': _parse_human_size(row.get('Folder Size', '0')),
                    'new_torrent_category': '',
                    'deletion_reason': row.get('Deletion Reason', ''),
                    'detect_datetime': row.get('Delete Datetime', ''),
                    'is_deleted': 'True',
                    'delete_datetime': row.get('Delete Datetime', ''),
                })
    except Exception as e:
        logger.warning(f"Failed to read report CSV {csv_path}: {e}")
    return rows


def migrate_dedup_all(reports_dir: str, db_path: str, dry_run: bool = False) -> int:
    """Merge all dedup CSV files and import into DedupRecords table.

    Sources (in order):
      1. reports/dedup.csv                          (legacy, DedupRecord format)
      2. reports/Dedup/**/Dedup_Pending_*.csv       (DedupRecord format)
      3. reports/Dedup/**/Dedup_Report_*.csv        (DeletionRecord format, mapped)
      4. reports/dedup_history.csv                  (runtime-generated, preserves records)

    Deduplication uses ``existing_gdrive_path`` as a unique key.  Records
    already present in the DB are skipped (INSERT OR IGNORE).

    After import the merged data is exported to ``reports/dedup_history.csv``.
    """
    from packages.python.javdb_platform.db import get_db
    import glob as _glob

    all_rows: list = []
    seen_paths: set = set()
    dedup_dir = os.path.join(reports_dir, 'Dedup')

    # 1. Legacy dedup.csv
    legacy_path = os.path.join(reports_dir, 'dedup.csv')
    if os.path.exists(legacy_path):
        legacy_rows = _load_dedup_pending_csv(legacy_path)
        logger.info(f"Legacy dedup.csv: {len(legacy_rows)} rows")
        for row in legacy_rows:
            p = row.get('existing_gdrive_path', '')
            if p and p not in seen_paths:
                seen_paths.add(p)
                all_rows.append(row)

    # 2. Dedup_Pending_*.csv files (DedupRecord format)
    for csv_file in sorted(_glob.glob(os.path.join(dedup_dir, '**', 'Dedup_Pending_*.csv'), recursive=True)):
        rows = _load_dedup_pending_csv(csv_file)
        added = 0
        for row in rows:
            p = row.get('existing_gdrive_path', '')
            if p and p not in seen_paths:
                seen_paths.add(p)
                all_rows.append(row)
                added += 1
        if rows:
            logger.info(f"Pending CSV {csv_file}: {len(rows)} rows, {added} new")

    # 3. Dedup_Report_*.csv files (DeletionRecord format, mapped)
    for csv_file in sorted(_glob.glob(os.path.join(dedup_dir, '**', 'Dedup_Report_*.csv'), recursive=True)):
        rows = _load_dedup_report_csv(csv_file)
        added = 0
        for row in rows:
            p = row.get('existing_gdrive_path', '')
            if p and p not in seen_paths:
                seen_paths.add(p)
                all_rows.append(row)
                added += 1
        if rows:
            logger.info(f"Report CSV {csv_file}: {len(rows)} rows, {added} new")

    # 4. Existing dedup_history.csv (preserve records added at runtime)
    history_csv = os.path.join(reports_dir, 'dedup_history.csv')
    if os.path.exists(history_csv):
        history_rows = _load_dedup_pending_csv(history_csv)
        added = 0
        for row in history_rows:
            p = row.get('existing_gdrive_path', '')
            if p and p not in seen_paths:
                seen_paths.add(p)
                all_rows.append(row)
                added += 1
        if history_rows:
            logger.info(f"Existing dedup_history.csv: {len(history_rows)} rows, {added} new")

    logger.info(f"Dedup merge total: {len(all_rows)} unique records from all sources")

    if not all_rows:
        logger.info("No dedup records found to migrate")
        return 0

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(all_rows)} merged DedupRecords")
        return len(all_rows)

    # Import into DB using INSERT OR IGNORE to preserve existing records
    count = 0
    with get_db(db_path) as conn:
        for row in all_rows:
            is_del = str(row.get('is_deleted', 'False')).lower() in ('true', '1')
            cur = conn.execute(
                """INSERT OR IGNORE INTO DedupRecords
                   (VideoCode, ExistingSensor, ExistingSubtitle,
                    ExistingGdrivePath, ExistingFolderSize,
                    NewTorrentCategory, DeletionReason,
                    DateTimeDetected, IsDeleted, DateTimeDeleted)
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
            if cur.rowcount > 0:
                count += 1

    logger.info(f"Imported {count} new DedupRecords into DB ({len(all_rows) - count} already existed)")

    # Export merged data to dedup_history.csv (snake_case column names for CSV)
    output_csv = os.path.join(reports_dir, 'dedup_history.csv')
    with get_db(db_path) as conn:
        db_rows = conn.execute(
            """SELECT VideoCode, ExistingSensor, ExistingSubtitle,
               ExistingGdrivePath, ExistingFolderSize, NewTorrentCategory,
               DeletionReason, DateTimeDetected, IsDeleted, DateTimeDeleted
               FROM DedupRecords ORDER BY Id"""
        ).fetchall()
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=_DEDUP_FIELDNAMES)
        writer.writeheader()
        for r in db_rows:
            d = {
                'video_code': r[0],
                'existing_sensor': r[1],
                'existing_subtitle': r[2],
                'existing_gdrive_path': r[3],
                'existing_folder_size': r[4],
                'new_torrent_category': r[5],
                'deletion_reason': r[6],
                'detect_datetime': r[7],
                'is_deleted': 'True' if r[8] in (1, True) else 'False',
                'delete_datetime': r[9],
            }
            writer.writerow(d)
    logger.info(f"Exported {len(db_rows)} merged DedupRecords to {output_csv}")

    return count


def migrate_pikpak(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """Migrate pikpak_bridge_history.csv → PikpakHistory table."""
    if not os.path.exists(csv_path):
        logger.info(f"Skipping pikpak: {csv_path} not found")
        return 0

    from packages.python.javdb_platform.db import get_db
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    logger.info(f"PikPak history: {len(rows)} rows")

    if dry_run:
        logger.info(f"[DRY RUN] Would insert {len(rows)} PikpakHistory records")
        return len(rows)

    count = 0
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM PikpakHistory")
        for row in rows:
            conn.execute(
                """INSERT INTO PikpakHistory
                   (TorrentHash, TorrentName, Category, MagnetUri,
                    DateTimeAddedToQb, DateTimeDeletedFromQb, DateTimeUploadedToPikpak,
                    TransferStatus, ErrorMessage)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row.get('torrent_hash', ''),
                 row.get('torrent_name', ''),
                 row.get('category', ''),
                 row.get('magnet_uri', ''),
                 normalize_storage_datetime(row.get('added_to_qb_date', '') or ''),
                 normalize_storage_datetime(row.get('deleted_from_qb_date', '') or ''),
                 normalize_storage_datetime(row.get('uploaded_to_pikpak_date', '') or ''),
                 row.get('transfer_status', ''),
                 row.get('error_message', '')),
            )
            count += 1

    logger.info(f"Migrated {count} PikpakHistory records")
    return count


def migrate_proxy_bans(csv_path: str, db_path: str, dry_run: bool = False) -> int:
    """No-op: proxy bans are now session-scoped (in-memory only).

    Kept for backward compatibility with callers that still reference
    this function.  Always returns 0.
    """
    logger.info("Skipping proxy_bans migration (proxy bans are now session-scoped, not persisted)")
    return 0


# =====================================================================
# Phase 2 — Report CSV migration helpers
# =====================================================================

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
    """Migrate one report CSV → ReportSessions + ReportMovies + ReportTorrents.

    Session creation and row insertion run inside a single transaction so
    a failure in row insertion does not leave an orphaned session record.

    Returns dict with keys: session_id, row_count, skipped.
    """
    from datetime import datetime
    from packages.python.javdb_platform.db import get_db

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
            "SELECT Id FROM ReportSessions WHERE CsvFilename = ?", (filename,)
        ).fetchone()
        if existing:
            logger.debug(f"Already migrated: {filename} (session_id={existing[0]})")
            return {'session_id': existing[0], 'row_count': 0, 'skipped': True}

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            """INSERT INTO ReportSessions
               (ReportType, ReportDate, UrlType, DisplayName,
                Url, StartPage, EndPage, CsvFilename, DateTimeCreated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (meta['report_type'], meta['report_date'],
             meta.get('url_type'), meta.get('display_name'),
             None, None, None, filename, created_at),
        )
        session_id = cur.lastrowid

        for row in rows:
            href = javdb_absolute_url(row.get('href', ''), _BASE_URL)
            cur = conn.execute(
                """INSERT INTO ReportMovies
                   (SessionId, Href, VideoCode, Page, Actor, Rate, CommentNumber)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id,
                 href, row.get('video_code', ''),
                 int(row['page']) if row.get('page') else None,
                 row.get('actor', ''),
                 float(row['rate']) if row.get('rate') else None,
                 int(row['comment_number']) if row.get('comment_number') else None),
            )
            report_movie_id = cur.lastrowid
            video_code = row.get('video_code', '')

            for cat in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
                val = (row.get(cat, '') or '').strip()
                if not val or 'magnet:' not in val:
                    continue
                magnet_uri, _ = _strip_magnet_prefix(val)
                size_val = (row.get(f'size_{cat}', '') or '').strip()
                sub_ind, cen_ind = _CATEGORY_TO_INDICATORS[cat]

                conn.execute(
                    """INSERT INTO ReportTorrents
                       (ReportMovieId, VideoCode, MagnetUri, SubtitleIndicator, CensorIndicator,
                        Size, FileCount)
                       VALUES (?, ?, ?, ?, ?, ?, 0)""",
                    (report_movie_id, video_code, magnet_uri, sub_ind, cen_ind, size_val),
                )

    return {'session_id': session_id, 'row_count': len(rows), 'skipped': False}


def verify_session(session_id: int, csv_path: str, db_path: str) -> bool:
    """Verify a migrated session: movie count matches CSV row count."""
    from packages.python.javdb_platform.db import get_db

    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            csv_rows = list(reader)
    except Exception:
        return False

    with get_db(db_path) as conn:
        db_count = conn.execute(
            "SELECT COUNT(*) FROM ReportMovies WHERE SessionId = ?", (session_id,)
        ).fetchone()[0]

    if db_count != len(csv_rows):
        logger.warning(f"Row count mismatch: DB={db_count} CSV={len(csv_rows)} in {csv_path}")
        return False
    return True


# =====================================================================
# CLI entry point
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description='Migrate all CSV files to SQLite (v6 schema)')
    parser.add_argument('--reports-dir', default='reports', help='Reports directory')
    parser.add_argument('--db-path', default=None, help='SQLite database path')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be migrated')
    parser.add_argument('--verify', action='store_true',
                        help='Verify migrated report data against original CSVs')
    args = parser.parse_args()

    reports_dir = args.reports_dir
    db_path = args.db_path or os.path.join(reports_dir, 'javdb_autospider.db')

    logger.info("=" * 60)
    logger.info("CSV → SQLite MIGRATION (v6 BigCamelCase schema)")
    logger.info(f"Reports dir: {reports_dir}")
    logger.info(f"Database: {db_path}")
    if args.dry_run:
        logger.info("[DRY RUN MODE]")
    if args.verify:
        logger.info("[VERIFY MODE]")
    logger.info("=" * 60)

    import packages.python.javdb_platform.db as db_mod
    db_mod.DB_PATH = db_path
    db_mod.init_db(db_path, force=True)

    # ── Phase 1: data tables ─────────────────────────────────────────
    logger.info("-" * 60)
    logger.info("Phase 1: Data-table CSVs")
    logger.info("-" * 60)

    table_totals = {}
    table_totals['history'] = migrate_history(
        os.path.join(reports_dir, 'parsed_movies_history.csv'), db_path, args.dry_run)
    table_totals['inventory'] = migrate_inventory(
        os.path.join(reports_dir, 'rclone_inventory.csv'), db_path, args.dry_run)
    table_totals['dedup'] = migrate_dedup_all(reports_dir, db_path, args.dry_run)
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
