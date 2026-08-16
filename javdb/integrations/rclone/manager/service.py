#!/usr/bin/env python3
"""
Unified RClone Manager service — scan, report and execute orchestration.

Owns the non-CLI manager orchestration: inventory scan, dedup report,
execute / execute-soft-delete, and inventory validation. The command-line
surface lives in :mod:`apps.cli.rclone.manager`, which maps parsed arguments to
:class:`RcloneManagerOptions` and calls :func:`run_manager`.

Phases (regardless of flag order) always run **scan → report → execute**.

Valid combinations
~~~~~~~~~~~~~~~~~~
* ``scan``
* ``report``
* ``execute``
* ``scan + report``
* ``report + execute``
* ``scan + report + execute``

Invalid: ``scan + execute`` without ``report``.
"""

import os
import re
import csv
import tempfile
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from javdb.infra.config import cfg, use_db_storage
from javdb.infra.logging import setup_logging, get_logger
from javdb.infra.paths import find_latest_report_in_dated_dirs, ensure_dated_dir

from javdb.integrations.rclone.dedup import (
    analyze_all_duplicates,
    generate_csv_report,
    print_summary,
    rclone_move,
    rclone_purge,
)
from javdb.integrations.rclone.path_utils import (
    get_configured_drive_name,
    get_configured_root_folder,
    has_remote_prefix,
    strip_drive_name,
    strip_root_folder,
    to_full_remote_path,
)
from javdb.integrations.rclone.scan import (
    check_rclone_installed,
    check_remote_exists,
    filter_folders_by_recent_changes,
    get_actor_folders,
    get_all_movie_folders_for_year,
    get_movie_folders_with_stats,
    get_year_folders,
    setup_rclone_config_from_base64,
)
from javdb.integrations.rclone.types import (
    DedupResult,
    FolderInfo,
    INCREMENTAL_DAYS,
)
from javdb.storage import advisory_lock
from javdb.storage.repos.operations_repo import OperationsRepo
from javdb.storage.repos.session_lifecycle_repo import SessionLifecycleRepo
from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult

_YEAR_RE = re.compile(r"^\d{4}$")

# Cross-runner lease guarding the destructive dedup execute path. Every caller
# of the executor competes for this one key, whichever workflow launched it.
DEDUP_EXECUTE_LOCK_KEY = "lock:rclone_dedup_execute"

# How often the purge loop pushes that lease's deadline out. Neither
# WeeklyDedup.yml nor RcloneManager.yml caps the job below the lease TTL, and
# the purge marks nothing deleted until the whole pass finishes, so a backlog
# that runs past the deadline would let a second run steal the lease and purge
# the same paths concurrently. 30 min keeps hours of headroom while costing one
# tiny UPDATE per half hour, whatever the folder count.
DEDUP_LEASE_RENEW_INTERVAL_SECONDS = 30 * 60

# Exit code for a lock-held skip in the file-specific execute mode
# (``--dedup-csv``), where the lease holder will NOT cover this file's plan.
# 75 mirrors sysexits.h ``EX_TEMPFAIL``: the work was not done, the cause is
# temporary, retry once the holder finishes. The shared-queue mode returns 0
# instead — there the holder drains the very rows this run would have.
EXIT_LOCK_HELD = 75

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


def run_manager(
    options: RcloneManagerOptions, session_id: Optional[str] = None,
) -> RcloneManagerResult:
    """Public service entry point: run the manager for the given options.

    *session_id* (ADR-046 D2 — never ambient) is forwarded as the
    standalone-vs-inherited signal; the standalone CLI passes ``None``.
    """
    exit_code = run_manager_from_options(options, session_id=session_id)
    return RcloneManagerResult(exit_code=exit_code)


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
) -> Optional[List[dict]]:
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
        return None

    if not actors:
        return []

    all_rows: List[dict] = []
    actor_failed = False
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
                actor_failed = True
                logger.error(f"Error scanning {year}/{actor}: {exc}")
    if actor_failed:
        return None
    return all_rows


def scan_inventory(
    remote_name: str, root_folder: str,
    max_workers: int = 4,
    year_filter: Optional[List[str]] = None,
    row_callback=None,
) -> Tuple[int, int]:
    """Scan the full folder tree using year-level parallelism with fallback."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.info(f"Scanning inventory from {remote_name}:{root_folder}...")
    years = get_year_folders(remote_name, root_folder)
    if not years:
        logger.warning("No year folders found")
        return 0, 0

    if year_filter:
        years = [y for y in years if y in year_filter]
        logger.info(f"Year filter applied: {years}")
        if not years:
            return 0, 0

    scan_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    total_rows = 0
    error_count = 0
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
                if rows is None:
                    error_count += 1
                    logger.error(f"Error processing year {year}: fallback scan incomplete")
                    continue
                if rows:
                    if row_callback:
                        row_callback(rows)
                    total_rows += len(rows)
                logger.info(
                    f"Progress: {completed}/{total} years done — "
                    f"year {year}: {len(rows)} folders, total so far: {total_rows}"
                )
            except Exception as e:
                error_count += 1
                logger.error(f"Error processing year {year}: {e}")

    logger.info(
        f"Scan complete: {total_rows} movie folders found "
        f"({error_count} year error(s))"
    )
    return total_rows, error_count


def export_db_to_csv(output_path: str) -> int:
    """Export the rclone_inventory table from SQLite to a CSV file."""
    from javdb.storage.db import get_db, OPERATIONS_DB_PATH

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
    from javdb.infra.config import use_sqlite

    rows: List[dict] = []

    if use_sqlite():
        try:
            from javdb.storage.db import current_backend
            raw = OperationsRepo().load_rclone_inventory()
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
    session_id: Optional[str] = None,
) -> int:
    """Analyse inventory for duplicates and generate a report.

    This function never executes deletions — it only persists dedup
    records with ``is_deleted=False``.  Actual deletion is handled
    separately by :func:`run_execute_from_csv`.

    *session_id* (ADR-046 D2) tags the dedup-record writes; standalone
    callers pass ``None`` (DedupRecords.SessionId is nullable).

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

    _persist_dedup_records(dedup_results, session_id=session_id)

    # Self-heal: drop any pending DedupRecords whose path is no longer in
    # the freshly loaded inventory. Zero remote calls; safe to run always.
    validate_dedup_records_against_inventory(session_id=session_id)

    export_dedup_history()

    return 0


def _persist_dedup_records(
    dedup_results: List[DedupResult], session_id: Optional[str] = None,
) -> None:
    """Save dedup records to DB via spider/dedup_checker.

    Records are always written with ``is_deleted=False``.  The execute
    phase is responsible for updating the flag after purging.

    *session_id* (ADR-046 D2) tags the dedup-record writes; standalone
    callers pass ``None`` (DedupRecords.SessionId is nullable).

    No per-run CSV file is generated; use :func:`export_dedup_history`
    to produce a consolidated ``dedup_history.csv`` from the DB.
    """
    try:
        from javdb.spider.services.dedup_store import append_dedup_record
        from javdb.spider.services.dedup_types import DedupRecord

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
                if append_dedup_record('', rec, session_id=session_id):
                    appended += 1
                else:
                    skipped += 1
        logger.info(f"Persisted dedup records: {appended} appended, {skipped} duplicates skipped")
    except Exception:
        logger.exception("Could not persist dedup records")
        raise


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


def validate_dedup_records_against_inventory(
    session_id: Optional[str] = None,
) -> Tuple[int, List[dict]]:
    """Self-heal DedupRecords whose path no longer exists in the inventory.

    The truth set is the current ``RcloneInventory`` (FolderPath column,
    already stored as a relative path). Pending dedup records (``IsDeleted=0``)
    whose ``ExistingGdrivePath`` is not in this set are considered orphans:

    - Marked ``IsDeleted=1`` with ``DateTimeDeleted=now``.
    - ``DeletionReason`` is suffixed with :data:`ORPHAN_REASON_SUFFIX`.
    - The original row dicts are returned (and persisted to a CSV report
      by the caller) so operators can audit the self-heal.

    *session_id* (ADR-046 D2 — never ambient) tags the orphan write. DedupRecords
    has a nullable SessionId, so a standalone caller passes ``None`` and the row
    persists untagged (the orphan update is non-raising).

    Returns ``(orphan_count, orphan_rows)``. Zero remote calls are made.
    """
    try:
        inventory = OperationsRepo().load_rclone_inventory()
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
        all_records = OperationsRepo().load_dedup_records()
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
    # ADR-046 D2: bind the explicit session on the repo (the global is never
    # read). DedupRecords.SessionId is nullable, so a None session_id persists
    # the orphan untagged without raising (Phase-2 contract).
    updated = OperationsRepo(session_id=session_id).mark_orphan_records(
        orphan_paths, ORPHAN_REASON_SUFFIX, now_str,
    )
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
    session_id: Optional[str] = None,
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
        inventory = OperationsRepo().load_rclone_inventory()
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
        deleted = OperationsRepo().delete_rclone_inventory_paths(
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
    validate_dedup_records_against_inventory(session_id=session_id)

    return 0


def export_dedup_history() -> int:
    """Export the DB dedup_records table to ``reports/dedup_history.csv``.

    Mirrors the pattern used by :func:`export_db_to_csv` for inventory.
    """
    from javdb.spider.services.dedup_store import export_dedup_db_to_csv

    output_path = os.path.join(REPORTS_DIR, 'dedup_history.csv')
    return export_dedup_db_to_csv(output_path)


def migrate_strip_drive_names() -> int:
    """One-time migration: strip drive-name prefix from all paths in operations.db.

    Idempotent — only rows with a *leading* rclone remote (``:`` before first ``/``)
    are updated; paths like ``dir/file:name`` are left unchanged.
    Returns the total number of rows updated across both tables.
    """
    from javdb.storage.db import get_db, OPERATIONS_DB_PATH

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
    session_id: Optional[str] = None,
) -> int:
    """Purge pending dedup records under the dedup advisory lease.

    The executor drains **every** pending ``DedupRecords`` row, so two
    concurrent runs would purge the same paths twice. ``WeeklyDedup.yml`` and
    ``RcloneManager.yml`` serialise on a shared GitHub ``concurrency`` group,
    but ``DailyIngestion.yml`` / ``AdHocIngestion.yml`` run this same CLI
    outside it — hence the cross-runner lease taken here (see
    :mod:`javdb.storage.advisory_lock`).

    Losing the lease means different things per mode, so the skip exit differs:

    * **Shared-queue mode** (*from_file_only* False — every workflow caller):
      the holder is draining the very same pending ``DedupRecords`` rows, so the
      work does happen and losing is not an error. We name the holder and return
      0. Both ingestion workflows mark this step ``continue-on-error`` and the
      dedicated dedup workflows do not, so a clean 0 keeps a scheduled pipeline
      green while the skip stays visible in the log.
    * **File-only mode** (*from_file_only* True — an explicit ``--dedup-csv``
      working set): the holder drains the shared queue, **not** this file's
      plan, so nobody executes it. Reporting 0 would claim success for work that
      never ran, so we return :data:`EXIT_LOCK_HELD` (75, ``EX_TEMPFAIL``) to
      mean "resource busy, retry later". The same applies if the lease is lost
      *mid*-purge (see the lease-renewal heartbeat below): a partial run of this
      file's plan is exactly as incomplete as never starting it.

    The lease is only as shared as the operations DB behind it: under
    ``STORAGE_BACKEND=sqlite`` it is machine-local and grants no cross-runner
    exclusion, which is logged as a warning rather than refused (see
    :mod:`javdb.storage.advisory_lock`).

    Scan / report stay lock-free; only this destructive path is guarded.

    Returns 0 when at least one purge succeeded, when there was nothing to do,
    or when the lease was held elsewhere (or lost mid-purge) in shared-queue
    mode; :data:`EXIT_LOCK_HELD` when the lease was held elsewhere, or lost
    mid-purge, in file-only mode; 1 when all attempted purges failed, or when
    the lease could not be evaluated at all.
    """
    if dry_run:
        # A dry run purges nothing and writes no DB rows, so it needs no lease
        # — and must never be able to block a real execution.
        return _execute_dedup_purge(
            dedup_csv, dry_run=True, from_file_only=from_file_only,
            session_id=session_id,
        )

    if not use_db_storage():
        # CSV-only / ``JAVDB_FORBID_DB_WRITES`` (TestIngestion): taking the
        # lease would itself be a forbidden DB write, and the pending set is a
        # local file rather than the shared queue — nothing to serialise.
        logger.warning(
            "DB storage disabled — running dedup execute WITHOUT the "
            "advisory lease (no shared pending queue to race over)"
        )
        return _execute_dedup_purge(
            dedup_csv, dry_run=False, from_file_only=from_file_only,
            session_id=session_id,
        )

    # The lease is only as shared as the operations DB holding it. Under
    # sqlite that DB is this checkout's own file, so the lease still serialises
    # runs on one machine (useful to a self-hoster) but cannot referee two
    # runners. Production runs d1, where it genuinely is shared — so degrade
    # visibly instead of implying a guarantee we don't have here.
    from javdb.storage.db import current_backend

    if current_backend() == 'sqlite':
        logger.warning(
            "Dedup advisory lease %s is MACHINE-LOCAL under "
            "STORAGE_BACKEND=sqlite — it lives in this machine's own "
            "operations.db and provides NO cross-runner mutual exclusion "
            "(concurrent runners would each acquire it and drain the same "
            "pending records). Cross-runner safety requires "
            "STORAGE_BACKEND=d1. Proceeding anyway.",
            DEDUP_EXECUTE_LOCK_KEY,
        )

    try:
        attempt = advisory_lock.try_acquire(DEDUP_EXECUTE_LOCK_KEY)
    except Exception:
        # Fail closed: this path already depends on the operations DB (it reads
        # DedupRecords and records deletions through it). If that DB cannot be
        # reached, purging first and failing to mark the rows deleted would
        # leave them pending for the next run — the exact double-purge this
        # lease exists to prevent. Refuse instead, loudly.
        logger.error(
            "Cannot evaluate the dedup advisory lease (%s) — refusing to "
            "purge anything; no records drained",
            DEDUP_EXECUTE_LOCK_KEY, exc_info=True,
        )
        return 1

    if attempt.lease is None:
        if from_file_only:
            # The holder drains the shared pending queue; it will never look at
            # this file. Skipping quietly with 0 would report success for a
            # deletion plan that nobody executed.
            logger.warning(
                "EXECUTE SKIPPED — dedup advisory lease %s is held by %s. The "
                "deletion plan in %s was NOT executed and will not be: the "
                "holder drains the shared pending queue, not this file's "
                "working set. Retry after the holder finishes (exit %s).",
                DEDUP_EXECUTE_LOCK_KEY, attempt.holder, dedup_csv,
                EXIT_LOCK_HELD,
            )
            return EXIT_LOCK_HELD
        logger.warning(
            "EXECUTE SKIPPED — dedup advisory lease %s is held by %s; another "
            "run is draining the same pending records. Nothing purged.",
            DEDUP_EXECUTE_LOCK_KEY, attempt.holder,
        )
        return 0

    # The purge marks nothing deleted until every path is done, so a large
    # backlog can outlive the lease's TTL — and an expired lease is stealable
    # while we are still purging. Heartbeat it instead: each renewal returns a
    # NEW token, so the release below must use the latest one.
    lease = attempt.lease

    def _renew_lease() -> bool:
        nonlocal lease
        try:
            renewed = advisory_lock.renew(lease)
        except Exception:
            # A transient D1/sqlite error here must not propagate: it would
            # abort _execute_dedup_purge() mid-loop and skip the
            # mark_records_deleted() call below the loop, losing every path
            # already purged in this pass. Treat "can't confirm we still hold
            # it" the same as "confirmed lost" — the loop below checkpoints
            # purged_pairs before returning either way. If the UPDATE actually
            # landed before this raised, `lease` still holds the pre-renewal
            # token; the release() below already tolerates a stale token
            # (logs and lets the row expire on its own).
            logger.error(
                "Dedup advisory lease %s renewal raised — treating as lost",
                lease.key, exc_info=True,
            )
            return False
        if renewed is None:
            return False
        lease = renewed
        return True

    try:
        return _execute_dedup_purge(
            dedup_csv, dry_run=False, from_file_only=from_file_only,
            session_id=session_id, renew_lease=_renew_lease,
        )
    finally:
        try:
            advisory_lock.release(lease)
        except Exception:
            logger.warning(
                "Failed to release dedup advisory lease %s — it expires on "
                "its own at %s",
                DEDUP_EXECUTE_LOCK_KEY, lease.expires_at,
                exc_info=True,
            )


def _write_dedup_csv_rows(path: str, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    """Atomically rewrite *path* with *rows* (temp file + :func:`os.replace`).

    file-only mode's own progress tracking: :func:`mark_records_deleted` never
    rewrites this file (DB-only by design, see its docstring), and an
    externally-sourced ``--dedup-csv`` plan may have no matching DedupRecords
    rows at all — the DB cross-check in :func:`_execute_dedup_purge` cannot
    help such a plan, so this file is the only durable record of what already
    purged. ``os.replace`` is atomic on the same filesystem, so a crash
    mid-write leaves the previous, still-consistent file in place.
    """
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _execute_dedup_purge(
    dedup_csv: str,
    dry_run: bool = False,
    from_file_only: bool = False,
    session_id: Optional[str] = None,
    renew_lease: Optional[Callable[[], bool]] = None,
) -> int:
    """Read pending dedup records, purge them, and update the DB.

    Call through :func:`run_execute_from_csv`, which holds the advisory lease
    for the duration of this work.

    *renew_lease* extends that lease between purges (see
    :data:`DEDUP_LEASE_RENEW_INTERVAL_SECONDS`); it returns False once the lease
    is no longer ours — including when confirming that raised an error, which
    is treated the same as confirmed loss rather than propagated — which stops
    the pass so a runner that took it over is not purging the same remote
    paths alongside us. Paths already purged before that point are still
    persisted below (this is not an abort). The unlocked callers (dry run, DB
    storage disabled) pass None.

    When *from_file_only* is True, only the given CSV file is read
    (e.g. a per-run CSV passed via ``--dedup-csv``).  Otherwise,
    records are loaded from the DB (authoritative source).

    After execution (non-dry-run), the DB state is exported to
    ``reports/dedup_history.csv``.

    *session_id* (ADR-046 D2) tags the deletion update; standalone callers
    pass ``None`` (DedupRecords.SessionId is nullable).

    Returns 0 when at least one purge succeeded (or nothing to do); 1 when all
    attempted purges failed; :data:`EXIT_LOCK_HELD` when *from_file_only* is
    True and the lease was lost mid-purge, since the caller's file-mode plan is
    left incomplete either way (see :func:`run_execute_from_csv`).
    """
    from javdb.spider.services.dedup_store import (
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

    # Preserved for _write_dedup_csv_rows below, before anything mutates the
    # row dicts: DictReader keys already mirror the file's own header order.
    file_fieldnames = list(rows[0].keys())

    rows_by_path: Dict[str, List[Dict[str, str]]] = {}
    if from_file_only:
        for r in rows:
            path = r.get('ExistingGdrivePath', r.get('existing_gdrive_path', ''))
            if path:
                rows_by_path.setdefault(path, []).append(r)

        # mark_records_deleted() only ever writes the DB (see its own
        # docstring) — this file is never rewritten by it — so a retry after
        # EXIT_LOCK_HELD (lease lost mid-purge; see below) needs some other
        # record of what already purged. The DB is kept current at each
        # renewal checkpoint (see the purge loop) for the common case where
        # this file was produced by export_dedup_history(), so cross-check it
        # here. A path the DB has never heard of (a plan sourced outside the
        # normal scan pipeline) falls back to the file's own column — the
        # purge loop below additionally rewrites this file in place at each
        # checkpoint, which is what actually closes that gap for such a path.
        #
        # uq_dedup_active_path only constrains IsDeleted=0 rows, so a path can
        # legally carry both an old deleted row AND a fresh pending one (a new
        # duplicate landed where an earlier one was already purged). Treating
        # ANY deleted row for the path as "done" would then hide that fresh
        # pending row from the purge queue entirely. Only apply "already
        # done" where the DB has no pending row left for that path.
        db_deleted_paths: set = set()
        db_pending_paths: set = set()
        for r in load_dedup_csv(dedup_csv, from_file_only=False):
            path = r.get('ExistingGdrivePath', r.get('existing_gdrive_path', ''))
            if not path:
                continue
            (db_deleted_paths if r.get('is_deleted') == 'True' else db_pending_paths).add(path)
        already_deleted = db_deleted_paths - db_pending_paths
        for path in already_deleted & rows_by_path.keys():
            for r in rows_by_path[path]:
                r['is_deleted'] = 'True'

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
    lease_lost = False
    next_renew_at = time.monotonic() + DEDUP_LEASE_RENEW_INTERVAL_SECONDS
    for folder_path in unique_paths:
        if renew_lease is not None and time.monotonic() >= next_renew_at:
            # Flush before risking the lease: if renewal below fails or
            # raises, a new holder can start reading pending rows from the DB
            # immediately, and any path we've already purged but not yet
            # marked is exactly the double-purge this lease exists to
            # prevent. mark_records_deleted's WHERE ... AND IsDeleted=0 guard
            # makes re-flushing already-marked pairs at the next checkpoint a
            # safe no-op, so there is no need to track what changed since the
            # last flush.
            if not dry_run and purged_pairs:
                mark_records_deleted(dedup_csv, purged_pairs, session_id=session_id)
                if from_file_only:
                    _write_dedup_csv_rows(dedup_csv, rows, file_fieldnames)
            if not renew_lease():
                lease_lost = True
                logger.error(
                    "Dedup advisory lease lost mid-purge — stopping after %s of "
                    "%s paths. The records left pending stay for whoever holds "
                    "the lease now; purging on would double-purge them.",
                    success_count + fail_count, len(unique_paths),
                )
                break
            next_renew_at = time.monotonic() + DEDUP_LEASE_RENEW_INTERVAL_SECONDS
        full_path = to_full_remote_path(folder_path, drive=drive_name, root=root)
        ok = rclone_purge(full_path, dry_run=dry_run)
        if ok:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            purged_pairs.append((folder_path, now_str))
            success_count += 1
            if from_file_only:
                for r in rows_by_path.get(folder_path, ()):
                    r['is_deleted'] = 'True'
                    r['delete_datetime'] = now_str
        else:
            fail_count += 1

    if not dry_run and purged_pairs:
        mark_records_deleted(dedup_csv, purged_pairs, session_id=session_id)
        logger.info(f"Marked {len(purged_pairs)} paths as deleted in DB")
        if from_file_only:
            _write_dedup_csv_rows(dedup_csv, rows, file_fieldnames)

    if not dry_run:
        if lease_lost:
            logger.warning(
                "Skipping dedup retention cleanup — the lease was lost "
                "mid-purge and another runner may now hold it and be "
                "writing these same rows"
            )
        else:
            retention = int(cfg('DEDUP_RETENTION_DAYS', '30'))
            cleanup_deleted_records(dedup_csv, older_than_days=retention)
        export_dedup_history()

    total_unique = success_count + fail_count
    logger.info("=" * 60)
    logger.info("DEDUP EXECUTOR COMPLETE")
    logger.info(f"Pending rows: {len(pending)}, unique paths: {total_unique}")
    logger.info(f"Purged: {success_count}, failed: {fail_count}, skipped (empty path): {skip_count}")
    logger.info("=" * 60)

    if lease_lost and from_file_only:
        # Same contract as failing to acquire the lease up front (see
        # run_execute_from_csv): in file-only mode nobody else will pick up
        # this file's remaining paths, so reporting 0 here would claim success
        # for a deletion plan that only partially (or, if lost on the first
        # checkpoint, not at all) ran. EXIT_LOCK_HELD tells the caller to retry.
        logger.warning(
            "File-mode purge incomplete — lease lost after %s of %s paths; "
            "reporting exit %s so the caller retries the remainder.",
            success_count + fail_count, len(unique_paths), EXIT_LOCK_HELD,
        )
        return EXIT_LOCK_HELD

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
    shape produced by ``javdb/migrations/tools/align_inventory_with_moviehistory.py``.
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
# Programmatic API
# ============================================================================

def run_rclone_manager(
    scan: bool = True,
    report: bool = True,
    execute: bool = False,
    dry_run: bool = True,
    session_id: Optional[str] = None,
) -> dict:
    """Programmatic entry point for the rclone manager pipeline.

    Mirrors the options-driven orchestration without a command-line parser:
    set up the rclone config, resolve the remote, then run whichever phases
    are requested.

    Only the phases that actually ran appear in ``phase_results``.  Each
    phase's value is a dict with at least ``{"exit_code": int}``.

    *session_id* (ADR-046 D2 — never ambient) is the standalone-vs-inherited
    signal: ``None`` (the default, used by every standalone caller) makes this
    run create + own + finalize a local report session; a caller-supplied id is
    treated as inherited (we use it but don't finalize it).

    Raises:
        ValueError: Invalid flag combination (e.g. execute without report).
        RuntimeError: Setup failure (no remote configured, rclone not
            installed, remote not reachable).
    """
    if not scan and not report and not execute:
        raise ValueError("At least one of scan/report/execute must be True")
    if execute and not report:
        # execute=True requires report=True regardless of scan — the endpoint
        # contract is that execute always needs a fresh report phase.
        raise ValueError(
            "execute=True requires report=True (or scan=True + report=True)"
        )

    # Setup rclone config from base64 if available.
    if RCLONE_CONFIG_BASE64:
        ok = setup_rclone_config_from_base64(RCLONE_CONFIG_BASE64)
        if not ok:
            raise RuntimeError(
                "Failed to write rclone config from RCLONE_CONFIG_BASE64"
            )

    phase_results: dict = {}

    # ── Scan phase ────────────────────────────────────────────────────────
    if scan:
        resolved = resolve_rclone_root(None)
        if not resolved:
            raise RuntimeError(
                "No remote configured — set RCLONE_FOLDER_PATH (e.g. "
                "'gdrive:/folder') or RCLONE_DRIVE_NAME + RCLONE_ROOT_FOLDER"
            )
        remote_name, root_folder = resolved

        ok, msg = check_rclone_installed()
        if not ok:
            raise RuntimeError(f"rclone not available: {msg}")
        ok, msg = check_remote_exists(remote_name)
        if not ok:
            raise RuntimeError(f"Remote not reachable: {msg}")

        os.makedirs(REPORTS_DIR, exist_ok=True)
        # Persist the scan via the same staging-then-swap path the CLI main()
        # uses, so a subsequent report/execute phase reads fresh inventory
        # rather than stale rows. A partial scan drops staging and leaves the
        # live RcloneInventory untouched.
        session_repo = SessionLifecycleRepo()
        operations_repo = OperationsRepo()

        session_repo.init_storage()
        # ADR-046 D2: the active session is passed in explicitly (never read
        # from the process-global). A None session_id means "standalone" — we
        # create + own + finalize a local session here.
        staging_sid = session_id
        # Only finalize (commit/fail) a session we created ourselves — an
        # inherited session is owned by the caller.
        created_local_session = staging_sid is None
        if created_local_session:
            staging_sid = session_repo.create_report_session(
                report_type="rclone_inventory",
                report_date=datetime.now().strftime("%Y%m%d"),
                csv_filename=RCLONE_INVENTORY_CSV,
            )
        try:
            # open_rclone_staging is inside the try so a failure here also
            # triggers the drop-staging / mark-failed finalization below.
            operations_repo.open_rclone_staging(staging_sid)
            total_rows, error_count = scan_inventory(
                remote_name,
                root_folder,
                row_callback=lambda rows: operations_repo.append_rclone_staging(
                    rows,
                    session_id=staging_sid,
                ),
            )
            if error_count == 0:
                operations_repo.swap_rclone_inventory(staging_sid)
                if created_local_session:
                    session_repo.mark_session_committed(staging_sid)
            else:
                # Partial scan — drop staging, leave live inventory untouched.
                operations_repo.drop_rclone_staging(staging_sid)
                if created_local_session:
                    session_repo.mark_session_failed(
                        staging_sid,
                        reason="rclone_scan_partial",
                    )
        except Exception:
            # scan_inventory / swap raised — never leave the staging table or
            # the report session dangling in an in-progress state.
            operations_repo.drop_rclone_staging(staging_sid)
            if created_local_session:
                session_repo.mark_session_failed(
                    staging_sid,
                    reason="rclone_scan_error",
                )
            raise
        phase_results["scan"] = {
            "exit_code": 0 if error_count == 0 else 1,
            "total_rows": total_rows,
            "error_count": error_count,
        }
        if error_count != 0:
            # A failed scan dropped staging and left the live inventory
            # unchanged — stop here rather than run report/execute against
            # stale data.
            return {"phase_results": phase_results, "dry_run": dry_run}

    # ── Report phase ──────────────────────────────────────────────────────
    if report:
        if not scan:
            # Report-only: resolve remote for CSV path but don't scan.
            os.makedirs(REPORTS_DIR, exist_ok=True)
        output_path = os.path.join(REPORTS_DIR, RCLONE_INVENTORY_CSV)
        exit_code = run_report_from_inventory(output_path, session_id=session_id)
        phase_results["report"] = {"exit_code": exit_code}

    # ── Execute phase ─────────────────────────────────────────────────────
    if execute:
        dedup_csv = os.path.join(REPORTS_DIR, 'dedup_history.csv')
        exit_code = run_execute_from_csv(
            dedup_csv, dry_run=dry_run, session_id=session_id,
        )
        phase_results["execute"] = {"exit_code": exit_code, "dry_run": dry_run}

    return {"phase_results": phase_results, "dry_run": dry_run}


# ============================================================================
# Manager orchestration (from options)
# ============================================================================

def _describe_mode(options: "RcloneManagerOptions") -> str:
    """Return a human-readable label for the active flag combination."""
    parts = []
    if options.scan:
        parts.append('SCAN')
    if options.report:
        parts.append('REPORT')
    if options.execute:
        parts.append('EXECUTE')
    if options.execute_soft_delete:
        parts.append('EXECUTE_SOFT_DELETE')
    if getattr(options, 'validate', False):
        parts.append('VALIDATE')
    return '+'.join(parts) or 'NONE'


def run_manager_from_options(
    options: "RcloneManagerOptions", session_id: Optional[str] = None,
) -> int:
    # ADR-046 D2: session_id is the explicit standalone-vs-inherited signal
    # (never read from the process-global). None ⇒ standalone: create + own +
    # finalize a local staging session below.
    setup_logging(log_level=options.log_level)

    mode_label = _describe_mode(options)

    # Setup rclone config
    if RCLONE_CONFIG_BASE64:
        if not setup_rclone_config_from_base64(RCLONE_CONFIG_BASE64):
            return 1
    else:
        logger.info("No RCLONE_CONFIG_BASE64 in config — assuming rclone is pre-configured")

    # ── Execute-only (independent of remote/inventory) ────────────────
    if options.execute and not options.scan and not options.report and not options.execute_soft_delete:
        if options.dedup_csv:
            dedup_csv = options.dedup_csv
            from_file_only = True
        else:
            # Read from DB (authoritative); dedup_csv is only used as
            # a fallback path inside load_dedup_csv when DB is empty.
            dedup_csv = os.path.join(REPORTS_DIR, 'dedup_history.csv')
            from_file_only = False
        return run_execute_from_csv(
            dedup_csv, dry_run=options.dry_run,
            from_file_only=from_file_only, session_id=session_id,
        )

    if options.execute_soft_delete and not options.scan and not options.report and not options.execute:
        soft_delete_csv = options.soft_delete_csv or os.path.join(REPORTS_DIR, SOFT_DELETE_CSV)
        backup_prefix = options.soft_delete_backup_prefix or RCLONE_SOFT_DELETE_BACKUP_PREFIX
        return run_execute_soft_delete_from_csv(
            soft_delete_csv,
            dry_run=options.dry_run,
            backup_prefix=backup_prefix,
        )

    # ── Scan / Report (/ Execute) / Validate need a remote ────────────
    resolved = resolve_rclone_root(options.root_path)
    if not resolved:
        logger.error(
            "No --root-path provided and RCLONE_FOLDER_PATH not set in config "
            "(expected form: gdrive:/folder)"
        )
        return 1
    remote_name, root_folder = resolved

    if options.output:
        output_path = options.output
    else:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        output_path = os.path.join(REPORTS_DIR, RCLONE_INVENTORY_CSV)

    year_filter = None
    if options.years:
        year_filter = [str(y).strip() for y in options.years if str(y).strip()]

    logger.info("=" * 60)
    logger.info("RCLONE MANAGER")
    logger.info(f"Mode: {mode_label}")
    logger.info(f"Remote: {remote_name}:{root_folder}")
    if year_filter:
        logger.info(f"Year filter: {year_filter}")
    logger.info(f"Workers: {options.workers}")
    if options.report:
        logger.info(f"Incremental: {options.incremental}")
    if options.execute:
        logger.info(f"Dry run: {options.dry_run}")
    if options.execute_soft_delete:
        logger.info(f"Soft delete dry run: {options.dry_run}")
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
    if options.validate:
        logger.info("")
        logger.info("=" * 60)
        logger.info("VALIDATE PHASE — re-validating inventory against remote")
        logger.info(f"Prune orphans: {options.validate_prune}")
        logger.info("=" * 60)
        return run_validate_inventory(
            remote_name, root_folder,
            year_filter=year_filter,
            max_workers=options.workers,
            prune=options.validate_prune,
            session_id=session_id,
        )

    # ── Scan phase ───────────────────────────────────────────────────
    if options.scan:
        from javdb.infra.config import use_sqlite as _use_sqlite, use_csv as _use_csv

        session_repo = SessionLifecycleRepo()
        operations_repo = OperationsRepo()

        total_written = 0
        _sqlite_ok = False
        _staging_session_id: Optional[str] = None
        _created_local_staging_session = False
        if _use_sqlite():
            try:
                session_repo.init_storage()
                # ADR-046 D2: inherit the explicit session_id (never the global).
                _staging_session_id = session_id
                if _staging_session_id is None:
                    _staging_session_id = session_repo.create_report_session(
                        report_type="rclone_inventory",
                        report_date=datetime.now().strftime("%Y%m%d"),
                        csv_filename=os.path.basename(output_path),
                    )
                    _created_local_staging_session = True
                    logger.info(
                        "Created local rclone inventory staging session: id=%s",
                        _staging_session_id,
                    )
                # X3 staging-then-swap: rows go into a per-session staging
                # table; the live RcloneInventory only gets rewritten in a
                # single atomic swap at the end. A failed scan therefore
                # can't half-overwrite.
                operations_repo.open_rclone_staging(_staging_session_id)
                _sqlite_ok = True
            except Exception as e:
                logger.error(f"Failed initializing SQLite for rclone inventory; aborting scan: {e}")
                if _staging_session_id is not None:
                    if _created_local_staging_session:
                        try:
                            session_repo.mark_session_failed(
                                _staging_session_id,
                                reason="rclone_scan_error",
                            )
                        except Exception as mark_error:
                            logger.warning(
                                "Failed to mark rclone inventory staging session "
                                "failed after init error: %s",
                                mark_error,
                            )
                    try:
                        operations_repo.drop_rclone_staging(_staging_session_id)
                    except Exception as drop_error:
                        logger.error(
                            "Failed to drop rclone inventory staging table "
                            "for session %s after init error: %s",
                            _staging_session_id,
                            drop_error,
                            exc_info=True,
                        )
                _staging_session_id = None
                _created_local_staging_session = False
                return 1

        _csv_file = None
        _csv_writer = None
        _csv_tmp_path = None
        if _use_csv():
            output_dir = os.path.dirname(output_path) or '.'
            os.makedirs(output_dir, exist_ok=True)
            _csv_file = tempfile.NamedTemporaryFile(
                'w',
                newline='',
                encoding='utf-8',
                dir=output_dir,
                prefix=f"{os.path.basename(output_path)}.",
                suffix=".tmp",
                delete=False,
            )
            _csv_tmp_path = _csv_file.name
            _csv_writer = csv.DictWriter(_csv_file, fieldnames=INVENTORY_FIELDNAMES)
            _csv_writer.writeheader()

        def on_rows(rows: list):
            nonlocal total_written
            if _csv_writer is not None:
                for row in rows:
                    _csv_writer.writerow(row)
                _csv_file.flush()
            if _sqlite_ok:
                if _staging_session_id is not None:
                    operations_repo.append_rclone_staging(
                        rows, session_id=_staging_session_id
                    )
            total_written += len(rows)

        scan_failed = False
        scan_error_count = 0
        try:
            total_found, scan_error_count = scan_inventory(
                remote_name, root_folder,
                max_workers=options.workers,
                year_filter=year_filter,
                row_callback=on_rows,
            )
            if scan_error_count:
                scan_failed = True
                logger.error(
                    "Inventory scan had %s year-level error(s); dropping "
                    "staging and leaving live RcloneInventory untouched.",
                    scan_error_count,
                )
        except Exception:
            scan_failed = True
            raise
        finally:
            if scan_failed and _sqlite_ok and _staging_session_id is not None:
                if _created_local_staging_session:
                    try:
                        session_repo.mark_session_failed(
                            _staging_session_id,
                            reason="rclone_scan_error",
                        )
                    except Exception as mark_error:
                        logger.warning(
                            "Failed to mark rclone inventory staging session "
                            "failed after scan error: %s",
                            mark_error,
                        )
                try:
                    operations_repo.drop_rclone_staging(_staging_session_id)
                    logger.info(
                        "Dropped RcloneInventoryStaging_%s after scan failure; "
                        "live RcloneInventory left untouched.",
                        _staging_session_id,
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to drop staging table after scan error: {e}"
                    )
            if scan_failed and _csv_file is not None:
                _csv_file.close()
                if _csv_tmp_path is not None:
                    try:
                        os.remove(_csv_tmp_path)
                    except FileNotFoundError:
                        pass

        if _csv_file is not None and not _csv_file.closed:
            _csv_file.close()

        if scan_failed:
            return 1

        if _sqlite_ok and _staging_session_id is not None:
            cleanup_failed = False
            try:
                if year_filter:
                    committed = operations_repo.merge_rclone_inventory_from_stage(
                        _staging_session_id,
                        year_filter,
                    )
                else:
                    committed = operations_repo.swap_rclone_inventory(
                        _staging_session_id,
                    )
            except Exception as e:
                action = "merge" if year_filter else "swap"
                logger.error(
                    f"Failed to {action} RcloneInventory staging — "
                    f"main table left UNCHANGED: {e}"
                )
                if _created_local_staging_session:
                    try:
                        session_repo.mark_session_failed(
                            _staging_session_id,
                            reason="rclone_scan_error",
                        )
                    except Exception as mark_error:
                        logger.warning(
                            "Failed to mark rclone inventory staging session "
                            "failed after swap error: %s",
                            mark_error,
                        )
                try:
                    operations_repo.drop_rclone_staging(_staging_session_id)
                except Exception as drop_error:
                    logger.error(
                        "Failed to drop rclone inventory staging table "
                        "for session %s after swap error: %s",
                        _staging_session_id,
                        drop_error,
                        exc_info=True,
                    )
                if _csv_tmp_path is not None:
                    try:
                        os.remove(_csv_tmp_path)
                    except FileNotFoundError:
                        pass
                raise
            if _created_local_staging_session:
                cleanup_failed = True
                for attempt in range(1, 4):
                    try:
                        session_repo.mark_session_committed(_staging_session_id)
                    except Exception as e:
                        logger.warning(
                            "Failed to mark rclone inventory session committed "
                            "after successful swap (attempt %d/3): %s",
                            attempt, e,
                        )
                    else:
                        cleanup_failed = False
                        break
                if cleanup_failed:
                    logger.error(
                        "Rclone inventory swap succeeded, but post-success "
                        "session cleanup failed for session_id=%s; leaving "
                        "the scan output intact for inspection.",
                        _staging_session_id,
                    )
            action = "Merged" if year_filter else "Swapped"
            logger.info(
                "%s RcloneInventoryStaging_%s into RcloneInventory "
                "(committed=%s rows)", action, _staging_session_id, committed,
            )

        if _csv_tmp_path is not None:
            os.replace(_csv_tmp_path, output_path)

        if _sqlite_ok:
            csv_export_path = os.path.join(REPORTS_DIR, RCLONE_INVENTORY_CSV)
            os.makedirs(REPORTS_DIR, exist_ok=True)
            export_db_to_csv(csv_export_path)

        logger.info("=" * 60)
        logger.info("SCAN COMPLETE")
        logger.info(f"Total movies recorded: {total_written}")
        logger.info(f"Output: {output_path}")
        logger.info("=" * 60)

        if total_found == 0 and not options.report:
            logger.warning("No movie folders found")
            return 0

    # ── Report phase ─────────────────────────────────────────────────
    if options.report:
        logger.info("")
        logger.info("=" * 60)
        logger.info("REPORT PHASE — analysing inventory for duplicates")
        logger.info("=" * 60)

        rc = run_report_from_inventory(
            csv_path=output_path,
            max_workers=options.workers,
            incremental=options.incremental,
            session_id=session_id,
        )
        if rc != 0:
            return rc

    # ── Execute phase ────────────────────────────────────────────────
    if options.execute:
        logger.info("")
        logger.info("=" * 60)
        logger.info("EXECUTE PHASE — purging duplicates")
        logger.info("=" * 60)

        if options.dedup_csv:
            dedup_csv = options.dedup_csv
            from_file_only = True
        else:
            # Records were persisted to DB in the report phase above;
            # read from DB (authoritative source).
            dedup_csv = os.path.join(REPORTS_DIR, 'dedup_history.csv')
            from_file_only = False
        return run_execute_from_csv(
            dedup_csv, dry_run=options.dry_run,
            from_file_only=from_file_only, session_id=session_id,
        )

    if options.execute_soft_delete:
        logger.info("")
        logger.info("=" * 60)
        logger.info("EXECUTE SOFT DELETE PHASE — moving lower versions")
        logger.info("=" * 60)
        soft_delete_csv = options.soft_delete_csv or os.path.join(REPORTS_DIR, SOFT_DELETE_CSV)
        backup_prefix = options.soft_delete_backup_prefix or RCLONE_SOFT_DELETE_BACKUP_PREFIX
        return run_execute_soft_delete_from_csv(
            soft_delete_csv,
            dry_run=options.dry_run,
            backup_prefix=backup_prefix,
        )

    return 0
