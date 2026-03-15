#!/usr/bin/env python3
"""
Unified RClone Manager — scan, report and execute via composable flags.

Flags
-----
``--scan``
    Scan the remote folder tree and write results to DB/CSV.

``--report``
    Load the inventory from DB (fallback CSV), analyse duplicates,
    generate a CSV report, and persist dedup records.

``--execute``
    Read a dedup CSV, skip already-deleted entries, execute
    ``rclone purge`` for each remaining entry, and update the CSV.

Flags can be combined.  Regardless of the order they are passed on the
command line, execution always follows **scan → report → execute**.

Valid combinations
~~~~~~~~~~~~~~~~~~
* ``--scan``
* ``--report``
* ``--execute``
* ``--scan --report``
* ``--report --execute``
* ``--scan --report --execute``

Invalid: ``--scan --execute`` without ``--report``.

Usage
-----
    python3 scripts/rclone_manager.py --scan
    python3 scripts/rclone_manager.py --report
    python3 scripts/rclone_manager.py --scan --report
    python3 scripts/rclone_manager.py --execute
    python3 scripts/rclone_manager.py --report --execute --dry-run
    python3 scripts/rclone_manager.py --scan --report --execute
"""

import os
import sys
import csv
import gc
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

from utils.config_helper import cfg
from utils.logging_config import setup_logging, get_logger
from utils.path_helper import find_latest_report_in_dated_dirs, ensure_dated_dir

from utils.rclone_helper import (
    FolderInfo,
    DedupResult,
    check_rclone_installed,
    check_remote_exists,
    setup_rclone_config_from_base64,
    get_year_folders,
    get_actor_folders,
    get_movie_folders_with_stats,
    get_all_movie_folders_for_year,
    get_folder_stats_batch,
    filter_folders_by_recent_changes,
    group_folders_by_movie_code,
    analyze_all_duplicates,
    analyze_duplicates_for_code,
    rclone_purge,
    format_size,
    generate_csv_report,
    print_summary,
    INCREMENTAL_DAYS,
)

# Config defaults
RCLONE_DRIVE_NAME = cfg('RCLONE_DRIVE_NAME', None)
RCLONE_ROOT_FOLDER = cfg('RCLONE_ROOT_FOLDER', None)
RCLONE_CONFIG_BASE64 = cfg('RCLONE_CONFIG_BASE64', None)
REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
RCLONE_INVENTORY_CSV = cfg('RCLONE_INVENTORY_CSV', 'rclone_inventory.csv')
DEDUP_CSV = cfg('DEDUP_CSV', 'dedup.csv')
DEDUP_DIR = cfg('DEDUP_DIR', os.path.join(REPORTS_DIR, 'Dedup'))
DEDUP_LOG_FILE = cfg('DEDUP_LOG_FILE', 'logs/rclone_dedup.log')

setup_logging()
logger = get_logger(__name__)

INVENTORY_FIELDNAMES = [
    'video_code', 'sensor_category', 'subtitle_category',
    'folder_path', 'folder_size', 'file_count', 'scan_datetime',
]


# ============================================================================
# Inventory helpers
# ============================================================================

def parse_root_path(root_path: str):
    """Split ``remote:/path`` into ``(remote_name, folder_path)``."""
    if ':' not in root_path:
        raise ValueError(f"Invalid root path (missing ':'): {root_path}")
    remote_name, folder_path = root_path.split(':', 1)
    return remote_name.strip(), folder_path.strip().strip('/')


def _folder_to_row(folder: FolderInfo, remote_name: str, root_folder: str, scan_time: str) -> dict:
    folder_path = folder.full_path
    if not folder_path.startswith(f"{remote_name}:"):
        folder_path = f"{remote_name}:{root_folder}/{folder.year}/{folder.actor}/{folder.folder_name}"
    return {
        'video_code': folder.movie_code,
        'sensor_category': folder.sensor_category,
        'subtitle_category': folder.subtitle_category,
        'folder_path': folder_path,
        'folder_size': folder.size,
        'file_count': folder.file_count,
        'scan_datetime': scan_time,
    }


def _process_year(
    remote_name: str, root_folder: str, year: str, scan_time: str,
    fallback_workers: int = 8,
) -> List[dict]:
    """Scan a year tree — try year-level first, fall back to actor-level."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        folders = get_all_movie_folders_for_year(remote_name, root_folder, year)
        return [_folder_to_row(f, remote_name, root_folder, scan_time) for f in folders]
    except Exception as e:
        logger.warning(f"Year-level scan failed for {year}: {e}")

    logger.warning(
        f"Year {year} too large for single call — "
        f"falling back to per-actor scan with {fallback_workers} workers"
    )
    try:
        actors = get_actor_folders(remote_name, root_folder, year)
    except Exception as e:
        logger.error(f"Error listing actors for year {year}: {e}")
        return []

    if not actors:
        return []

    all_rows: List[dict] = []
    with ThreadPoolExecutor(max_workers=fallback_workers) as executor:
        futures = {
            executor.submit(get_movie_folders_with_stats, remote_name, root_folder, year, actor): actor
            for actor in actors
        }
        for future in as_completed(futures):
            actor = futures[future]
            try:
                folders = future.result()
                all_rows.extend(_folder_to_row(f, remote_name, root_folder, scan_time) for f in folders)
            except Exception as exc:
                logger.debug(f"Error scanning {year}/{actor}: {exc}")
    return all_rows


def scan_inventory(
    remote_name: str, root_folder: str,
    max_workers: int = 4,
    year_filter: Optional[List[str]] = None,
    row_callback=None,
) -> int:
    """Scan the full folder tree using year-level parallelism with fallback."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.info(f"Scanning inventory from {remote_name}:{root_folder}...")
    years = get_year_folders(remote_name, root_folder)
    if not years:
        logger.warning("No year folders found")
        return 0

    if year_filter:
        years = [y for y in years if y in year_filter]
        logger.info(f"Year filter applied: {years}")
        if not years:
            return 0

    scan_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_rows = 0
    completed = 0
    total = len(years)

    logger.info(
        f"Scanning {total} year folders with {max_workers} workers "
        f"(year-level with per-actor fallback)..."
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_process_year, remote_name, root_folder, y, scan_time, max_workers): y
            for y in years
        }
        for future in as_completed(futures):
            year = futures[future]
            completed += 1
            try:
                rows = future.result()
                if rows:
                    if row_callback:
                        row_callback(rows)
                    total_rows += len(rows)
                logger.info(
                    f"Progress: {completed}/{total} years done — "
                    f"year {year}: {len(rows)} folders, total so far: {total_rows}"
                )
            except Exception as e:
                logger.error(f"Error processing year {year}: {e}")

    logger.info(f"Scan complete: {total_rows} movie folders found")
    return total_rows


def export_db_to_csv(output_path: str) -> int:
    """Export the rclone_inventory table from SQLite to a CSV file."""
    from utils.db import get_db, OPERATIONS_DB_PATH

    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT VideoCode AS video_code, SensorCategory AS sensor_category, "
            "SubtitleCategory AS subtitle_category, FolderPath AS folder_path, "
            "FolderSize AS folder_size, FileCount AS file_count, "
            "DateTimeScanned AS scan_datetime "
            "FROM RcloneInventory ORDER BY VideoCode"
        ).fetchall()

    if not rows:
        logger.warning("No records in DB to export to CSV")
        return 0

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

    logger.info(f"Exported {len(rows)} records from DB to {output_path}")
    return len(rows)


# ============================================================================
# Dedup-from-inventory logic
# ============================================================================

def load_inventory_as_folder_structure(
    csv_path: str,
) -> Dict[str, Dict[str, List[FolderInfo]]]:
    """Load the inventory from DB (priority) or CSV and rebuild a
    ``{year: {actor: [FolderInfo, ...]}}`` structure usable by the
    dedup analysis pipeline.
    """
    from utils.config_helper import use_sqlite

    rows: List[dict] = []

    if use_sqlite():
        try:
            from utils.db import db_load_rclone_inventory
            raw = db_load_rclone_inventory()
            for code, entries in raw.items():
                for e in entries:
                    rows.append(e)
            if rows:
                logger.info(f"Loaded {len(rows)} inventory records from SQLite")
        except Exception as e:
            logger.warning(f"Could not load inventory from SQLite: {e}")

    if not rows and os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            logger.info(f"Loaded {len(rows)} inventory records from CSV: {csv_path}")
        except Exception as e:
            logger.error(f"Failed to load inventory CSV: {e}")

    if not rows:
        logger.warning("No inventory data available for dedup")
        return {}

    structure: Dict[str, Dict[str, List[FolderInfo]]] = {}
    for row in rows:
        folder_path = row.get('FolderPath', row.get('folder_path', ''))
        parts = folder_path.split('/')
        # typical: remote:root/YYYY/Actor/FolderName
        # We need year and actor from the path.
        year = ''
        actor = ''
        folder_name = ''
        if len(parts) >= 3:
            folder_name = parts[-1]
            actor = parts[-2]
            year = parts[-3]

        code = row.get('VideoCode', row.get('video_code', '')).strip().upper()
        if not code:
            continue

        fi = FolderInfo(
            full_path=folder_path,
            year=year,
            actor=actor,
            movie_code=code,
            sensor_category=row.get('SensorCategory', row.get('sensor_category', '')),
            subtitle_category=row.get('SubtitleCategory', row.get('subtitle_category', '')),
            folder_name=folder_name,
            size=int(row.get('FolderSize', row.get('folder_size', 0)) or 0),
            file_count=int(row.get('FileCount', row.get('file_count', 0)) or 0),
        )
        structure.setdefault(year, {}).setdefault(actor, []).append(fi)

    total_folders = sum(
        len(folders)
        for actors in structure.values()
        for folders in actors.values()
    )
    logger.info(f"Rebuilt folder structure: {len(structure)} years, {total_folders} folders")
    return structure


def run_report_from_inventory(
    csv_path: str,
    max_workers: int = 4,
    incremental: bool = False,
) -> int:
    """Analyse inventory for duplicates and generate a report.

    This function never executes deletions — it only persists dedup
    records with ``is_deleted=False``.  Actual deletion is handled
    separately by :func:`run_execute_from_csv`.

    Returns 0 on success, 1 on failure.
    """
    folder_structure = load_inventory_as_folder_structure(csv_path)
    if not folder_structure:
        logger.info("No inventory data — nothing to analyse.")
        return 0

    if incremental:
        logger.info(f"Filtering for recent changes (last {INCREMENTAL_DAYS} days)...")
        folder_structure = filter_folders_by_recent_changes(
            folder_structure, days=INCREMENTAL_DAYS, max_workers=max_workers,
        )
        if not folder_structure:
            logger.info("No movie codes with recent changes. Nothing to analyse.")
            return 0

    logger.info("Analyzing duplicates from inventory...")
    dedup_results = analyze_all_duplicates(folder_structure, max_workers=max_workers)
    if not dedup_results:
        logger.info("No duplicates found.")
        return 0

    logger.info("Generating report...")
    csv_report = generate_csv_report(dedup_results)

    print_summary(csv_report, 0, 0, 0, 0, dry_run=True)

    _persist_dedup_records(dedup_results)
    export_dedup_history()

    return 0


def _persist_dedup_records(dedup_results: List[DedupResult]) -> None:
    """Save dedup records to DB via spider/dedup_checker.

    Records are always written with ``is_deleted=False``.  The execute
    phase is responsible for updating the flag after purging.

    No per-run CSV file is generated; use :func:`export_dedup_history`
    to produce a consolidated ``dedup_history.csv`` from the DB.
    """
    try:
        from scripts.spider.dedup_checker import DedupRecord, append_dedup_record

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        appended = 0
        skipped = 0
        for result in dedup_results:
            for folder, reason in result.folders_to_delete:
                rec = DedupRecord(
                    video_code=folder.movie_code,
                    existing_sensor=folder.sensor_category,
                    existing_subtitle=folder.subtitle_category,
                    existing_gdrive_path=folder.full_path,
                    existing_folder_size=folder.size,
                    new_torrent_category='',
                    deletion_reason=reason,
                    detect_datetime=now_str,
                    is_deleted='False',
                    delete_datetime='',
                )
                # csv_path arg kept for API compat but no longer written
                if append_dedup_record('', rec):
                    appended += 1
                else:
                    skipped += 1
        logger.info(f"Persisted dedup records: {appended} appended, {skipped} duplicates skipped")
    except Exception as e:
        logger.warning(f"Could not persist dedup records: {e}")


def export_dedup_history() -> int:
    """Export the DB dedup_records table to ``reports/dedup_history.csv``.

    Mirrors the pattern used by :func:`export_db_to_csv` for inventory.
    """
    from scripts.spider.dedup_checker import export_dedup_db_to_csv

    output_path = os.path.join(REPORTS_DIR, 'dedup_history.csv')
    return export_dedup_db_to_csv(output_path)


# ============================================================================
# Execute mode — purge folders from a dedup CSV
# ============================================================================

def resolve_latest_dedup_file(dedup_dir: str) -> Optional[str]:
    """Resolve the dedup CSV to use for execute: choose the newest by mtime
    between the latest Dedup_Pending_* and latest Dedup_Report_* so we never
    run against stale data.  When mtime is tied, prefer Dedup_Pending_* so
    mark_records_deleted() mutates the pending file.
    """
    latest_pending = find_latest_report_in_dated_dirs(dedup_dir, 'Dedup_Pending_*.csv')
    latest_report = find_latest_report_in_dated_dirs(dedup_dir, 'Dedup_Report_*.csv')
    candidates = []
    if latest_pending:
        candidates.append((latest_pending, os.path.getmtime(latest_pending), 0))
    if latest_report:
        candidates.append((latest_report, os.path.getmtime(latest_report), 1))
    if not candidates:
        return None
    # Max by (mtime, -prefer): prefer pending (0) over report (1) when tied
    return max(candidates, key=lambda x: (x[1], -x[2]))[0]


def run_execute_from_csv(
    dedup_csv: str,
    dry_run: bool = False,
    from_file_only: bool = False,
) -> int:
    """Read pending dedup records, purge them, and update the DB.

    When *from_file_only* is True, only the given CSV file is read
    (e.g. a per-run CSV passed via ``--dedup-csv``).  Otherwise,
    records are loaded from the DB (authoritative source).

    After execution (non-dry-run), the DB state is exported to
    ``reports/dedup_history.csv``.

    Returns 0 when at least one purge succeeded (or nothing to do);
    returns 1 only when all attempted purges failed.
    """
    from scripts.spider.dedup_checker import (
        load_dedup_csv, mark_records_deleted, cleanup_deleted_records,
    )

    os.makedirs(os.path.dirname(DEDUP_LOG_FILE) or '.', exist_ok=True)
    setup_logging(DEDUP_LOG_FILE)

    logger.info("=" * 60)
    logger.info("RCLONE DEDUP EXECUTOR")
    logger.info(f"Dedup CSV: {dedup_csv}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("=" * 60)

    rows = load_dedup_csv(dedup_csv, from_file_only=from_file_only)
    if not rows:
        logger.info("No dedup records found — nothing to do")
        return 0

    pending = [r for r in rows if r.get('is_deleted', 'False') != 'True']
    logger.info(f"Total records: {len(rows)}, pending deletion: {len(pending)}")

    if not pending:
        logger.info("All records already deleted — nothing to do")
        return 0

    success_count = 0
    fail_count = 0
    skip_count = 0

    unique_paths: Dict[str, bool] = {}
    for row in pending:
        folder_path = row.get('ExistingGdrivePath', row.get('existing_gdrive_path', ''))
        if not folder_path:
            logger.warning(f"Skipping record with empty path: {row.get('VideoCode', row.get('video_code', '?'))}")
            skip_count += 1
            continue
        unique_paths.setdefault(folder_path, True)

    purged_pairs: list = []
    for folder_path in unique_paths:
        ok = rclone_purge(folder_path, dry_run=dry_run)
        if ok:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            purged_pairs.append((folder_path, now_str))
            success_count += 1
        else:
            fail_count += 1

    if not dry_run and purged_pairs:
        mark_records_deleted(dedup_csv, purged_pairs)
        logger.info(f"Marked {len(purged_pairs)} paths as deleted in DB")

    if not dry_run:
        retention = int(cfg('DEDUP_RETENTION_DAYS', '30'))
        cleanup_deleted_records(dedup_csv, older_than_days=retention)
        export_dedup_history()

    total_unique = success_count + fail_count
    logger.info("=" * 60)
    logger.info("DEDUP EXECUTOR COMPLETE")
    logger.info(f"Pending rows: {len(pending)}, unique paths: {total_unique}")
    logger.info(f"Purged: {success_count}, failed: {fail_count}, skipped (empty path): {skip_count}")
    logger.info("=" * 60)

    # Partial success (some purged, some failed) is still success — allow workflow to commit.
    # Only fail when every attempted purge failed (no success at all).
    if success_count > 0:
        return 0
    if fail_count > 0:
        logger.warning("All purges failed — treating as job failure")
        return 1
    return 0


# ============================================================================
# CLI
# ============================================================================

def _describe_mode(args: argparse.Namespace) -> str:
    """Return a human-readable label for the active flag combination."""
    parts = []
    if args.scan:
        parts.append('SCAN')
    if args.report:
        parts.append('REPORT')
    if args.execute:
        parts.append('EXECUTE')
    return '+'.join(parts) or 'NONE'


def parse_arguments(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Unified rclone manager — scan, report & execute via composable flags',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --scan
  %(prog)s --scan --root-path "gdrive:/path" --years "2025,2026"
  %(prog)s --report
  %(prog)s --scan --report
  %(prog)s --execute
  %(prog)s --report --execute --dry-run
  %(prog)s --scan --report --execute
        """,
    )

    mode_group = parser.add_argument_group('mode flags (at least one required)')
    mode_group.add_argument('--scan', action='store_true', help='Scan remote folder tree into DB/CSV')
    mode_group.add_argument('--report', action='store_true', help='Generate dedup report from inventory')
    mode_group.add_argument('--execute', action='store_true', help='Execute pending deletions from dedup CSV')

    parser.add_argument('--root-path', type=str, default=None, help='rclone path (remote:/path)')
    parser.add_argument('--years', type=str, default=None, help='Comma-separated years')
    parser.add_argument('--workers', type=int, default=4, help='Parallel workers (default: 4)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    parser.add_argument('--output', type=str, default=None, help='Override output CSV path')

    report_group = parser.add_argument_group('report options')
    report_group.add_argument('--incremental', action='store_true', help='Only process recent changes')

    execute_group = parser.add_argument_group('execute options')
    execute_group.add_argument('--dry-run', action='store_true', help='Simulate without deleting')
    execute_group.add_argument(
        '--dedup-csv', type=str, default=None,
        help='Override dedup CSV path (default: REPORTS_DIR/DEDUP_CSV)',
    )

    args = parser.parse_args(argv)

    if not (args.scan or args.report or args.execute):
        parser.error('At least one of --scan, --report, --execute is required')
    if args.scan and args.execute and not args.report:
        parser.error('--scan --execute requires --report (use --scan --report --execute)')

    return args


def main() -> int:
    args = parse_arguments()
    setup_logging(log_level=args.log_level)

    mode_label = _describe_mode(args)

    # Setup rclone config
    if RCLONE_CONFIG_BASE64:
        if not setup_rclone_config_from_base64(RCLONE_CONFIG_BASE64):
            return 1
    else:
        logger.info("No RCLONE_CONFIG_BASE64 in config — assuming rclone is pre-configured")

    # ── Execute-only (independent of remote/inventory) ────────────────
    if args.execute and not args.scan and not args.report:
        if args.dedup_csv:
            dedup_csv = args.dedup_csv
            from_file_only = True
        else:
            # Read from DB (authoritative); dedup_csv is only used as
            # a fallback path inside load_dedup_csv when DB is empty.
            dedup_csv = os.path.join(REPORTS_DIR, 'dedup_history.csv')
            from_file_only = False
        return run_execute_from_csv(dedup_csv, dry_run=args.dry_run, from_file_only=from_file_only)

    # ── Scan / Report (/ Execute) need a remote ───────────────────────
    if args.root_path:
        remote_name, root_folder = parse_root_path(args.root_path)
    else:
        if not RCLONE_DRIVE_NAME or not RCLONE_ROOT_FOLDER:
            logger.error("No --root-path provided and RCLONE_DRIVE_NAME/RCLONE_ROOT_FOLDER not in config")
            return 1
        remote_name = RCLONE_DRIVE_NAME
        root_folder = RCLONE_ROOT_FOLDER.strip('/')

    if args.output:
        output_path = args.output
    else:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        output_path = os.path.join(REPORTS_DIR, RCLONE_INVENTORY_CSV)

    year_filter = None
    if args.years:
        year_filter = [y.strip() for y in args.years.split(',') if y.strip()]

    logger.info("=" * 60)
    logger.info("RCLONE MANAGER")
    logger.info(f"Mode: {mode_label}")
    logger.info(f"Remote: {remote_name}:{root_folder}")
    if year_filter:
        logger.info(f"Year filter: {year_filter}")
    logger.info(f"Workers: {args.workers}")
    if args.report:
        logger.info(f"Incremental: {args.incremental}")
    if args.execute:
        logger.info(f"Dry run: {args.dry_run}")
    logger.info(f"Output: {output_path}")
    logger.info("=" * 60)

    # Health checks
    ok, msg = check_rclone_installed()
    if not ok:
        logger.error(msg)
        return 1
    logger.info(f"  {msg}")

    ok, msg = check_remote_exists(remote_name)
    if not ok:
        logger.error(msg)
        return 1
    logger.info(f"  {msg}")

    # ── Scan phase ───────────────────────────────────────────────────
    if args.scan:
        from utils.config_helper import use_sqlite as _use_sqlite, use_csv as _use_csv

        total_written = 0
        _sqlite_ok = False
        if _use_sqlite():
            try:
                from utils.db import init_db, db_clear_rclone_inventory, db_append_rclone_inventory
                init_db()
                db_clear_rclone_inventory()
                _sqlite_ok = True
            except Exception as e:
                logger.warning(f"Failed initializing SQLite for rclone inventory: {e}")

        _csv_file = None
        _csv_writer = None
        if _use_csv():
            _csv_file = open(output_path, 'w', newline='', encoding='utf-8')
            _csv_writer = csv.DictWriter(_csv_file, fieldnames=INVENTORY_FIELDNAMES)
            _csv_writer.writeheader()

        def on_rows(rows: list):
            nonlocal total_written
            if _csv_writer is not None:
                for row in rows:
                    _csv_writer.writerow(row)
                _csv_file.flush()
            if _sqlite_ok:
                db_append_rclone_inventory(rows)
            total_written += len(rows)

        total_found = scan_inventory(
            remote_name, root_folder,
            max_workers=args.workers,
            year_filter=year_filter,
            row_callback=on_rows,
        )

        if _csv_file is not None:
            _csv_file.close()

        if _sqlite_ok:
            csv_export_path = os.path.join(REPORTS_DIR, RCLONE_INVENTORY_CSV)
            os.makedirs(REPORTS_DIR, exist_ok=True)
            export_db_to_csv(csv_export_path)

        logger.info("=" * 60)
        logger.info("SCAN COMPLETE")
        logger.info(f"Total movies recorded: {total_written}")
        logger.info(f"Output: {output_path}")
        logger.info("=" * 60)

        if total_found == 0 and not args.report:
            logger.warning("No movie folders found")
            return 0

    # ── Report phase ─────────────────────────────────────────────────
    if args.report:
        logger.info("")
        logger.info("=" * 60)
        logger.info("REPORT PHASE — analysing inventory for duplicates")
        logger.info("=" * 60)

        rc = run_report_from_inventory(
            csv_path=output_path,
            max_workers=args.workers,
            incremental=args.incremental,
        )
        if rc != 0:
            return rc

    # ── Execute phase ────────────────────────────────────────────────
    if args.execute:
        logger.info("")
        logger.info("=" * 60)
        logger.info("EXECUTE PHASE — purging duplicates")
        logger.info("=" * 60)

        if args.dedup_csv:
            dedup_csv = args.dedup_csv
            from_file_only = True
        else:
            # Records were persisted to DB in the report phase above;
            # read from DB (authoritative source).
            dedup_csv = os.path.join(REPORTS_DIR, 'dedup_history.csv')
            from_file_only = False
        return run_execute_from_csv(dedup_csv, dry_run=args.dry_run, from_file_only=from_file_only)

    return 0


if __name__ == '__main__':
    sys.exit(main())
