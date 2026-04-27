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
    python3 -m apps.cli.rclone_manager --scan
    python3 -m apps.cli.rclone_manager --report
    python3 -m apps.cli.rclone_manager --scan --report
    python3 -m apps.cli.rclone_manager --execute
    python3 -m apps.cli.rclone_manager --report --execute --dry-run
    python3 -m apps.cli.rclone_manager --scan --report --execute
"""

import os
import re
import sys
import csv
import gc
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_YEAR_RE = re.compile(r"^\d{4}$")

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.python.javdb_platform.config_helper import cfg
from packages.python.javdb_platform.logging_config import setup_logging, get_logger
from packages.python.javdb_platform.path_helper import find_latest_report_in_dated_dirs, ensure_dated_dir

from packages.python.javdb_integrations.rclone_helper import (
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
    rclone_move,
    format_size,
    generate_csv_report,
    print_summary,
    strip_drive_name,
    get_configured_drive_name,
    prepend_drive_name,
    get_configured_root_folder,
    strip_root_folder,
    to_full_remote_path,
    has_remote_prefix,
    INCREMENTAL_DAYS,
)

# Config defaults
RCLONE_FOLDER_PATH = cfg('RCLONE_FOLDER_PATH', None)
RCLONE_CONFIG_BASE64 = cfg('RCLONE_CONFIG_BASE64', None)
REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
RCLONE_INVENTORY_CSV = cfg('RCLONE_INVENTORY_CSV', 'rclone_inventory.csv')
DEDUP_CSV = cfg('DEDUP_CSV', 'dedup.csv')
DEDUP_DIR = cfg('DEDUP_DIR', os.path.join(REPORTS_DIR, 'Dedup'))
DEDUP_LOG_FILE = cfg('DEDUP_LOG_FILE', 'logs/rclone_dedup.log')
SOFT_DELETE_CSV = cfg('SOFT_DELETE_CSV', 'soft_delete_plan.csv')
RCLONE_SOFT_DELETE_BACKUP_PREFIX = cfg('RCLONE_SOFT_DELETE_BACKUP_PREFIX', '')

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


def resolve_rclone_root(cli_root_path: Optional[str]) -> Optional[Tuple[str, str]]:
    """Resolve ``(remote_name, root_folder)`` from ``--root-path`` or config.

    Config: ``RCLONE_FOLDER_PATH`` (e.g. ``gdrive:/folder``). Legacy
    ``RCLONE_DRIVE_NAME`` + ``RCLONE_ROOT_FOLDER`` is still accepted if the new
    variable is unset.
    """
    if cli_root_path and cli_root_path.strip():
        return parse_root_path(cli_root_path.strip())
    path = RCLONE_FOLDER_PATH
    if path and str(path).strip():
        return parse_root_path(str(path).strip())
    drive = cfg('RCLONE_DRIVE_NAME', None)
    root = cfg('RCLONE_ROOT_FOLDER', None)
    if drive and root is not None:
        r = str(root).strip().strip('/')
        combined = f"{str(drive).strip()}:/{r}" if r else f"{str(drive).strip()}:"
        return parse_root_path(combined)
    return None


def _folder_to_row(folder: FolderInfo, remote_name: str, root_folder: str, scan_time: str) -> dict:
    # Persist only the relative path under the configured root folder.
    folder_path = strip_root_folder(strip_drive_name(folder.full_path))
    if not folder_path:
        # Fallback: always relative (no root prefix).
        folder_path = f"{folder.year}/{folder.actor}/{folder.movie_code}/{folder.folder_name}"
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
    from packages.python.javdb_platform.db import get_db, OPERATIONS_DB_PATH

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
    from packages.python.javdb_platform.config_helper import use_sqlite

    rows: List[dict] = []

    if use_sqlite():
        try:
            from packages.python.javdb_platform.db import db_load_rclone_inventory, current_backend
            raw = db_load_rclone_inventory()
            for entries in raw.values():
                rows.extend(entries)
            if rows:
                logger.info(f"Loaded {len(rows)} inventory records from {current_backend()} backend")
        except Exception as e:
            logger.warning(f"Could not load inventory from db backend: {e}")

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

    drive_name = get_configured_drive_name()
    root = get_configured_root_folder()

    structure: Dict[str, Dict[str, List[FolderInfo]]] = {}
    for row in rows:
        folder_path = row.get('FolderPath', row.get('folder_path', ''))
        # folder_path is stored relative to root.  Still accept older absolute
        # paths and strip root if present.
        raw_rel = strip_root_folder(strip_drive_name(folder_path), root=root)
        parts = raw_rel.split('/') if raw_rel else []
        year = ''
        actor = ''
        folder_name = ''
        # New layout: <year>/<actor>/<movie_code>/<sensor-subtitle>
        # Legacy layout: <year>/<actor>/<movie_code [sensor-subtitle]>
        # Validate the candidate year against ^\d{4}$ before accepting it —
        # without this, a folder whose upstream segments contain extra
        # slashes (or any path that simply lacks a year prefix) silently
        # gets misclassified with a non-numeric "year" like "Actor".
        if len(parts) >= 4 and _YEAR_RE.match(parts[-4]):
            folder_name = parts[-1]
            actor = parts[-3]
            year = parts[-4]
        elif len(parts) >= 3 and _YEAR_RE.match(parts[-3]):
            folder_name = parts[-1]
            actor = parts[-2]
            year = parts[-3]
        else:
            logger.warning(
                "Inventory path missing 4-digit year segment, skipping: %s",
                folder_path,
            )
            continue

        code = row.get('VideoCode', row.get('video_code', '')).strip().upper()
        if not code:
            continue

        fi = FolderInfo(
            full_path=to_full_remote_path(raw_rel, drive=drive_name, root=root),
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

    # Self-heal: drop any pending DedupRecords whose path is no longer in
    # the freshly loaded inventory. Zero remote calls; safe to run always.
    validate_dedup_records_against_inventory()

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
        from packages.python.javdb_spider.services.dedup import DedupRecord, append_dedup_record

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        appended = 0
        skipped = 0
        for result in dedup_results:
            for folder, reason in result.folders_to_delete:
                rec = DedupRecord(
                    video_code=folder.movie_code,
                    existing_sensor=folder.sensor_category,
                    existing_subtitle=folder.subtitle_category,
                    existing_gdrive_path=strip_root_folder(strip_drive_name(folder.full_path)),
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


# ============================================================================
# Path validation & self-healing
# ============================================================================

ORPHAN_REASON_SUFFIX = '[orphan: missing in inventory]'

DEDUP_ORPHAN_FIELDNAMES = [
    'VideoCode', 'ExistingSensor', 'ExistingSubtitle',
    'ExistingGdrivePath', 'ExistingFolderSize',
    'NewTorrentCategory', 'DeletionReason',
    'DateTimeDetected', 'DateTimeDeleted',
]

INVENTORY_ORPHAN_FIELDNAMES = [
    'video_code', 'sensor_category', 'subtitle_category',
    'folder_path', 'folder_size', 'file_count', 'scan_datetime',
]


def _write_dedup_orphan_csv(rows: List[dict], when: str) -> Optional[str]:
    """Persist orphan dedup rows to ``reports/Dedup/<YYYYMMDD>/orphans-*.csv``.

    Returns the absolute file path written, or ``None`` if no rows.
    """
    if not rows:
        return None
    date_str = when.split(' ', 1)[0].replace('-', '')
    time_str = when.split(' ', 1)[1].replace(':', '') if ' ' in when else '000000'
    try:
        out_dir = ensure_dated_dir(DEDUP_DIR, date_str)
    except Exception:
        out_dir = os.path.join(DEDUP_DIR, date_str)
        os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'orphans-{date_str}-{time_str}.csv')
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=DEDUP_ORPHAN_FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, '') for k in DEDUP_ORPHAN_FIELDNAMES})
    return out_path


def _write_inventory_orphan_csv(rows: List[dict]) -> Optional[str]:
    """Persist orphan inventory rows to ``reports/inventory_orphans.csv``."""
    if not rows:
        return None
    os.makedirs(REPORTS_DIR, exist_ok=True)
    out_path = os.path.join(REPORTS_DIR, 'inventory_orphans.csv')
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=INVENTORY_ORPHAN_FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, '') for k in INVENTORY_ORPHAN_FIELDNAMES})
    return out_path


def validate_dedup_records_against_inventory() -> Tuple[int, List[dict]]:
    """Self-heal DedupRecords whose path no longer exists in the inventory.

    The truth set is the current ``RcloneInventory`` (FolderPath column,
    already stored as a relative path). Pending dedup records (``IsDeleted=0``)
    whose ``ExistingGdrivePath`` is not in this set are considered orphans:

    - Marked ``IsDeleted=1`` with ``DateTimeDeleted=now``.
    - ``DeletionReason`` is suffixed with :data:`ORPHAN_REASON_SUFFIX`.
    - The original row dicts are returned (and persisted to a CSV report
      by the caller) so operators can audit the self-heal.

    Returns ``(orphan_count, orphan_rows)``. Zero remote calls are made.
    """
    try:
        from packages.python.javdb_platform.db import (
            db_load_rclone_inventory,
            db_load_dedup_records,
            db_mark_orphan_records,
        )
    except Exception as e:
        logger.warning(f"Skipping dedup self-heal — DB helpers unavailable: {e}")
        return 0, []

    try:
        inventory = db_load_rclone_inventory()
    except Exception as e:
        logger.warning(f"Skipping dedup self-heal — could not load inventory: {e}")
        return 0, []

    inventory_paths = {
        (entry.get('FolderPath') or '').strip()
        for entries in inventory.values()
        for entry in entries
    }
    inventory_paths.discard('')

    if not inventory_paths:
        # Mirror :func:`run_validate_inventory`: an empty truth-set is a
        # serious signal (operations DB lost the inventory, or the scan
        # never ran), not an "all clean" no-op. Logging at error so the
        # signal isn't lost in default INFO-only log handlers; the function
        # still returns ``(0, [])`` without marking anything deleted because
        # treating every dedup record as orphan would be destructive.
        logger.error(
            "Dedup self-heal: inventory is empty — refusing to validate "
            "(would risk marking every pending dedup record as orphan)."
        )
        return 0, []

    try:
        all_records = db_load_dedup_records()
    except Exception as e:
        logger.warning(f"Skipping dedup self-heal — could not load dedup records: {e}")
        return 0, []

    orphans: List[dict] = []
    orphan_paths: List[str] = []
    for rec in all_records:
        if int(rec.get('IsDeleted') or 0) != 0:
            continue
        path = (rec.get('ExistingGdrivePath') or '').strip()
        if not path:
            continue
        if path not in inventory_paths:
            orphans.append(rec)
            orphan_paths.append(path)

    if not orphans:
        logger.info("Dedup self-heal: no orphan records found.")
        return 0, []

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    updated = db_mark_orphan_records(orphan_paths, ORPHAN_REASON_SUFFIX, now_str)
    for r in orphans:
        r['DateTimeDeleted'] = now_str
        existing_reason = (r.get('DeletionReason') or '').strip()
        r['DeletionReason'] = (
            f"{existing_reason} {ORPHAN_REASON_SUFFIX}".strip()
            if existing_reason else ORPHAN_REASON_SUFFIX
        )
    logger.warning(
        f"Dedup self-heal: marked {updated} orphan record(s) as deleted "
        f"(path missing in inventory). Sample: "
        f"{orphan_paths[:3]}{'...' if len(orphan_paths) > 3 else ''}"
    )
    csv_path = _write_dedup_orphan_csv(orphans, now_str)
    if csv_path:
        logger.info(f"Dedup orphans report written: {csv_path}")
    return updated, orphans


_DEFAULT_LIST_YEAR_TIMEOUT_SEC = 600


def _list_remote_dirs_for_year(
    remote_name: str, root_folder: str, year: str,
    timeout_sec: int = _DEFAULT_LIST_YEAR_TIMEOUT_SEC,
) -> List[str]:
    """Return relative paths ``<year>/<actor>/<code>/<leaf>`` for a year via
    a single dirs-only ``rclone lsjson -R`` call (no sizes, no file counts).

    Used by :func:`run_validate_inventory` to build a fresh truth set with
    minimal remote cost (vs. a full :func:`scan_inventory`).

    ``timeout_sec`` is the per-year subprocess wall-clock budget. Callers
    that want a global validation deadline can pass ``min(remaining, default)``
    so a stuck rclone on year N doesn't push validation past the operator's
    expected window.
    """
    import json as _json
    import subprocess as _subprocess

    # Avoid emitting "remote:/year" (with a stray leading slash on the path)
    # when ``root_folder`` is empty — rclone treats those as different paths.
    if root_folder:
        remote_path = f"{remote_name}:{root_folder}/{year}"
    else:
        remote_path = f"{remote_name}:{year}"
    try:
        result = _subprocess.run(
            ['rclone', 'lsjson', remote_path, '-R', '--dirs-only', '--fast-list'],
            capture_output=True, text=True, timeout=timeout_sec,
        )
    except _subprocess.TimeoutExpired:
        logger.error(
            f"Timeout listing {remote_path} for validation "
            f"(per-year budget: {timeout_sec}s)"
        )
        return []
    if result.returncode != 0:
        if 'directory not found' in (result.stderr or '').lower():
            return []
        logger.error(f"Failed to list {remote_path}: {result.stderr}")
        return []
    try:
        entries = _json.loads(result.stdout)
    except Exception as exc:
        logger.error(f"Invalid JSON for {remote_path}: {exc}")
        return []
    out: List[str] = []
    for entry in entries:
        path = entry.get('Path', '')
        parts = path.split('/')
        # depth 3 == <actor>/<movie_code>/<sensor-subtitle>
        if entry.get('IsDir') and len(parts) == 3:
            out.append(f"{year}/{path}")
    return out


def list_remote_truth_paths(
    remote_name: str, root_folder: str,
    year_filter: Optional[List[str]] = None,
    max_workers: int = 4,
) -> set:
    """Build a fresh remote truth-set of relative paths for validation."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    years = get_year_folders(remote_name, root_folder)
    if year_filter:
        years = [y for y in years if y in year_filter]
    if not years:
        return set()

    truth: set = set()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futs = {
            executor.submit(_list_remote_dirs_for_year, remote_name, root_folder, y): y
            for y in years
        }
        for fut in as_completed(futs):
            year = futs[fut]
            try:
                paths = fut.result()
                truth.update(paths)
                logger.info(f"Validate: year {year} → {len(paths)} dirs")
            except Exception as e:
                logger.error(f"Validate: error listing year {year}: {e}")
    return truth


def run_validate_inventory(
    remote_name: str, root_folder: str,
    year_filter: Optional[List[str]] = None,
    max_workers: int = 4,
    prune: bool = True,
) -> int:
    """Re-validate ``RcloneInventory`` against the remote.

    Lists the remote with one ``lsjson -R --dirs-only`` per year, diffs
    against the locally stored inventory, and (when *prune* is True) deletes
    inventory rows whose path no longer exists. Always writes
    ``reports/inventory_orphans.csv`` with the orphan rows. Then chains
    :func:`validate_dedup_records_against_inventory` to clean up any
    DedupRecords pending rows that point to those removed paths.

    Returns 0 on success, 1 on failure.
    """
    from packages.python.javdb_platform.db import (
        db_load_rclone_inventory,
        db_delete_rclone_inventory_paths,
    )

    logger.info("Building remote truth-set (dirs-only listing)...")
    truth = list_remote_truth_paths(
        remote_name, root_folder,
        year_filter=year_filter, max_workers=max_workers,
    )
    logger.info(f"Validate: remote truth-set size = {len(truth)}")

    if not truth:
        logger.error(
            "Validate: remote returned 0 directories — refusing to prune "
            "inventory (would wipe everything). Aborting."
        )
        return 1

    try:
        inventory = db_load_rclone_inventory()
    except Exception as e:
        logger.error(f"Validate: could not load inventory: {e}")
        return 1

    # Flatten and (optionally) restrict to year_filter.
    year_set = set(year_filter) if year_filter else None
    orphan_rows: List[dict] = []
    inventory_total = 0
    for entries in inventory.values():
        for r in entries:
            inventory_total += 1
            path = (r.get('FolderPath') or '').strip()
            if not path:
                continue
            if year_set:
                head = path.split('/', 1)[0]
                if head not in year_set:
                    continue
            if path not in truth:
                orphan_rows.append({
                    'video_code': r.get('VideoCode', ''),
                    'sensor_category': r.get('SensorCategory', ''),
                    'subtitle_category': r.get('SubtitleCategory', ''),
                    'folder_path': path,
                    'folder_size': r.get('FolderSize', 0),
                    'file_count': r.get('FileCount', 0),
                    'scan_datetime': r.get('DateTimeScanned', ''),
                })

    logger.info(
        f"Validate: inventory rows = {inventory_total} "
        f"(filtered scope) → orphans = {len(orphan_rows)}"
    )

    csv_path = _write_inventory_orphan_csv(orphan_rows)
    if csv_path:
        logger.info(f"Inventory orphans report written: {csv_path}")

    if orphan_rows and prune:
        deleted = db_delete_rclone_inventory_paths(
            r['folder_path'] for r in orphan_rows
        )
        logger.warning(f"Validate: pruned {deleted} orphan inventory row(s)")
        try:
            csv_export_path = os.path.join(REPORTS_DIR, RCLONE_INVENTORY_CSV)
            os.makedirs(REPORTS_DIR, exist_ok=True)
            export_db_to_csv(csv_export_path)
        except Exception as e:
            logger.warning(f"Validate: could not refresh inventory CSV: {e}")
    elif orphan_rows and not prune:
        logger.info("Validate: --validate-prune disabled, leaving inventory untouched")

    # Chain dedup self-heal so callers don't need to run --report just to
    # clean up dedup pendings that referenced removed paths.
    validate_dedup_records_against_inventory()

    return 0


def export_dedup_history() -> int:
    """Export the DB dedup_records table to ``reports/dedup_history.csv``.

    Mirrors the pattern used by :func:`export_db_to_csv` for inventory.
    """
    from packages.python.javdb_spider.services.dedup import export_dedup_db_to_csv

    output_path = os.path.join(REPORTS_DIR, 'dedup_history.csv')
    return export_dedup_db_to_csv(output_path)


def migrate_strip_drive_names() -> int:
    """One-time migration: strip drive-name prefix from all paths in operations.db.

    Idempotent — only rows with a *leading* rclone remote (``:`` before first ``/``)
    are updated; paths like ``dir/file:name`` are left unchanged.
    Returns the total number of rows updated across both tables.
    """
    from packages.python.javdb_platform.db import get_db, OPERATIONS_DB_PATH

    updated = 0
    with get_db(OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE RcloneInventory SET FolderPath = "
            "SUBSTR(FolderPath, INSTR(FolderPath, ':') + 1) "
            "WHERE INSTR(FolderPath, ':') > 0 "
            "AND (INSTR(FolderPath, '/') = 0 OR INSTR(FolderPath, ':') < INSTR(FolderPath, '/'))"
        )
        updated += cur.rowcount
        cur = conn.execute(
            "UPDATE DedupRecords SET ExistingGdrivePath = "
            "SUBSTR(ExistingGdrivePath, INSTR(ExistingGdrivePath, ':') + 1) "
            "WHERE INSTR(ExistingGdrivePath, ':') > 0 "
            "AND (INSTR(ExistingGdrivePath, '/') = 0 OR "
            "INSTR(ExistingGdrivePath, ':') < INSTR(ExistingGdrivePath, '/'))"
        )
        updated += cur.rowcount
        conn.commit()
    logger.info(f"migrate_strip_drive_names: updated {updated} rows in operations.db")
    return updated


# ============================================================================
# Execute mode — purge folders from a dedup CSV
# ============================================================================

def _assert_remote_drive_resolved(
    drive_name: str,
    sample_paths,
    *,
    context: str,
) -> None:
    """Fail fast when about to invoke ``rclone`` with paths that lack a remote
    prefix and no drive name is configured.

    Without this guard, ``rclone purge`` (or ``move``) would interpret the
    relative path as a *local* filesystem path and resolve it against the
    current working directory — which on CI runners is the repo checkout.
    Best case: ``directory not found`` errors.  Worst case: silent deletion
    of legitimate local files that happen to share the path prefix.

    Raises:
        RuntimeError: when the configuration cannot produce a remote-qualified
            path for the given samples.
    """
    if drive_name:
        return

    offenders = [p for p in sample_paths if p and not has_remote_prefix(p)]
    if not offenders:
        return

    sample = offenders[:3]
    raise RuntimeError(
        f"{context}: refusing to run rclone — drive name is not configured "
        f"(set RCLONE_FOLDER_PATH like 'gdrive:/...' or RCLONE_DRIVE_NAME) "
        f"and {len(offenders)} path(s) lack a remote prefix, e.g. {sample}. "
        f"Without a remote prefix rclone would treat them as LOCAL paths "
        f"relative to the current working directory."
    )


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
    from packages.python.javdb_spider.services.dedup import (
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

    drive_name = get_configured_drive_name()
    root = get_configured_root_folder()

    unique_paths: Dict[str, bool] = {}
    for row in pending:
        folder_path = row.get('ExistingGdrivePath', row.get('existing_gdrive_path', ''))
        if not folder_path:
            logger.warning(f"Skipping record with empty path: {row.get('VideoCode', row.get('video_code', '?'))}")
            skip_count += 1
            continue
        unique_paths.setdefault(folder_path, True)

    _assert_remote_drive_resolved(
        drive_name, unique_paths.keys(), context='dedup execute',
    )

    purged_pairs: list = []
    for folder_path in unique_paths:
        full_path = to_full_remote_path(folder_path, drive=drive_name, root=root)
        ok = rclone_purge(full_path, dry_run=dry_run)
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


def run_execute_soft_delete_from_csv(
    soft_delete_csv: str,
    dry_run: bool = False,
    backup_prefix: str = '',
) -> int:
    """Move lower-version folders to backup path (soft delete)."""
    if not os.path.exists(soft_delete_csv):
        logger.info(f"Soft-delete CSV not found: {soft_delete_csv}")
        return 0

    with open(soft_delete_csv, 'r', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        logger.info("No soft-delete rows found — nothing to do")
        return 0

    drive_name = get_configured_drive_name()
    root = get_configured_root_folder()
    success = 0
    failed = 0
    skipped = 0
    seen_sources = set()

    _candidate_paths = [
        (row.get('source_path') or row.get('SourcePath') or '').strip()
        for row in rows
    ]
    _assert_remote_drive_resolved(
        drive_name, _candidate_paths, context='soft-delete execute',
    )

    for row in rows:
        source_path = (row.get('source_path') or row.get('SourcePath') or '').strip()
        if not source_path:
            skipped += 1
            continue
        if source_path in seen_sources:
            skipped += 1
            continue
        seen_sources.add(source_path)

        destination_path = (row.get('destination_path') or row.get('DestinationPath') or '').strip()
        full_source = to_full_remote_path(source_path, drive=drive_name, root=root)
        if not destination_path:
            if not backup_prefix:
                logger.warning("Missing destination_path and no backup_prefix set for source: %s", source_path)
                failed += 1
                continue
            src_rel = strip_root_folder(strip_drive_name(source_path), root=root).lstrip('/')
            destination_path = f"{backup_prefix.rstrip('/')}/{src_rel}"
        else:
            destination_path = to_full_remote_path(destination_path, drive=drive_name, root=root)

        if rclone_move(full_source, destination_path, dry_run=dry_run):
            success += 1
        else:
            failed += 1

    logger.info("=" * 60)
    logger.info("SOFT DELETE EXECUTION COMPLETE")
    logger.info(f"Rows: {len(rows)}, unique sources: {len(seen_sources)}")
    logger.info(f"Moved: {success}, failed: {failed}, skipped: {skipped}")
    logger.info("=" * 60)

    if success > 0:
        return 0
    if failed > 0:
        return 1
    return 0


def run_execute_inventory_purge_from_csv(
    purge_plan_csv: str,
    *,
    dry_run: bool = False,
) -> int:
    """Purge folders listed in an inventory-alignment plan CSV (``rclone purge``).

    Expects rows with a ``source_path`` (or ``SourcePath``) column — the same
    shape produced by ``packages/python/javdb_migrations/tools/align_inventory_with_moviehistory.py``.
    """
    if not os.path.exists(purge_plan_csv):
        logger.info(f"Purge-plan CSV not found: {purge_plan_csv}")
        return 0

    with open(purge_plan_csv, 'r', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        logger.info("No purge-plan rows found — nothing to do")
        return 0

    drive_name = get_configured_drive_name()
    root = get_configured_root_folder()
    success = 0
    failed = 0
    skipped = 0
    seen_sources: set[str] = set()

    _candidate_paths = [
        (row.get('source_path') or row.get('SourcePath') or '').strip()
        for row in rows
    ]
    _assert_remote_drive_resolved(
        drive_name, _candidate_paths, context='inventory-purge execute',
    )

    for row in rows:
        source_path = (row.get('source_path') or row.get('SourcePath') or '').strip()
        if not source_path:
            skipped += 1
            continue
        if source_path in seen_sources:
            skipped += 1
            continue
        seen_sources.add(source_path)

        full_path = to_full_remote_path(source_path, drive=drive_name, root=root)
        if rclone_purge(full_path, dry_run=dry_run):
            success += 1
        else:
            failed += 1

    logger.info("=" * 60)
    logger.info("INVENTORY PURGE EXECUTION COMPLETE")
    logger.info(f"Rows: {len(rows)}, unique sources: {len(seen_sources)}")
    logger.info(f"Purged: {success}, failed: {failed}, skipped: {skipped}")
    logger.info("=" * 60)

    if success > 0:
        return 0
    if failed > 0:
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
    if args.execute_soft_delete:
        parts.append('EXECUTE_SOFT_DELETE')
    if getattr(args, 'validate', False):
        parts.append('VALIDATE')
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
    mode_group.add_argument('--execute-soft-delete', action='store_true', help='Execute soft-delete moves from CSV plan')
    mode_group.add_argument(
        '--validate', action='store_true',
        help='Re-validate inventory against the remote (dirs-only listing); '
             'prunes orphan inventory rows and self-heals related dedup pendings',
    )

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
    execute_group.add_argument(
        '--soft-delete-csv', type=str, default=None,
        help='Soft-delete CSV path (default: REPORTS_DIR/SOFT_DELETE_CSV)',
    )
    execute_group.add_argument(
        '--soft-delete-backup-prefix', type=str, default='',
        help='Backup destination prefix for rows without destination_path',
    )

    validate_group = parser.add_argument_group('validate options')
    validate_group.add_argument(
        '--no-validate-prune', dest='validate_prune', action='store_false',
        default=True,
        help='Validate mode: only report orphans, do not delete from inventory',
    )

    args = parser.parse_args(argv)

    if not (args.scan or args.report or args.execute or args.execute_soft_delete or args.validate):
        parser.error('At least one mode flag is required')
    if args.scan and args.execute and not args.report:
        parser.error('--scan --execute requires --report (use --scan --report --execute)')
    if args.validate and (args.scan or args.report or args.execute or args.execute_soft_delete):
        parser.error('--validate must be used on its own (no other mode flag)')

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
    if args.execute and not args.scan and not args.report and not args.execute_soft_delete:
        if args.dedup_csv:
            dedup_csv = args.dedup_csv
            from_file_only = True
        else:
            # Read from DB (authoritative); dedup_csv is only used as
            # a fallback path inside load_dedup_csv when DB is empty.
            dedup_csv = os.path.join(REPORTS_DIR, 'dedup_history.csv')
            from_file_only = False
        return run_execute_from_csv(dedup_csv, dry_run=args.dry_run, from_file_only=from_file_only)

    if args.execute_soft_delete and not args.scan and not args.report and not args.execute:
        soft_delete_csv = args.soft_delete_csv or os.path.join(REPORTS_DIR, SOFT_DELETE_CSV)
        backup_prefix = args.soft_delete_backup_prefix or RCLONE_SOFT_DELETE_BACKUP_PREFIX
        return run_execute_soft_delete_from_csv(
            soft_delete_csv,
            dry_run=args.dry_run,
            backup_prefix=backup_prefix,
        )

    # ── Scan / Report (/ Execute) / Validate need a remote ────────────
    resolved = resolve_rclone_root(args.root_path)
    if not resolved:
        logger.error(
            "No --root-path provided and RCLONE_FOLDER_PATH not set in config "
            "(expected form: gdrive:/folder)"
        )
        return 1
    remote_name, root_folder = resolved

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
    if args.execute_soft_delete:
        logger.info(f"Soft delete dry run: {args.dry_run}")
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

    # ── Validate phase (mutually exclusive with scan/report/execute) ──
    if args.validate:
        logger.info("")
        logger.info("=" * 60)
        logger.info("VALIDATE PHASE — re-validating inventory against remote")
        logger.info(f"Prune orphans: {args.validate_prune}")
        logger.info("=" * 60)
        return run_validate_inventory(
            remote_name, root_folder,
            year_filter=year_filter,
            max_workers=args.workers,
            prune=args.validate_prune,
        )

    # ── Scan phase ───────────────────────────────────────────────────
    if args.scan:
        from packages.python.javdb_platform.config_helper import use_sqlite as _use_sqlite, use_csv as _use_csv

        total_written = 0
        _sqlite_ok = False
        if _use_sqlite():
            try:
                from packages.python.javdb_platform.db import init_db, db_clear_rclone_inventory, db_append_rclone_inventory
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

    if args.execute_soft_delete:
        logger.info("")
        logger.info("=" * 60)
        logger.info("EXECUTE SOFT DELETE PHASE — moving lower versions")
        logger.info("=" * 60)
        soft_delete_csv = args.soft_delete_csv or os.path.join(REPORTS_DIR, SOFT_DELETE_CSV)
        backup_prefix = args.soft_delete_backup_prefix or RCLONE_SOFT_DELETE_BACKUP_PREFIX
        return run_execute_soft_delete_from_csv(
            soft_delete_csv,
            dry_run=args.dry_run,
            backup_prefix=backup_prefix,
        )

    return 0


if __name__ == '__main__':
    sys.exit(main())
