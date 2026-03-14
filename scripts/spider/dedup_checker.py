"""Dedup checker module for Spider.

Loads rclone inventory and compares torrent categories against existing
GDrive entries to detect upgrade opportunities (e.g. subtitle or sensor
priority upgrades).  Storage backend is controlled by ``STORAGE_MODE``.
"""

import csv
import os
import tempfile
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, NamedTuple

from utils.config_helper import use_sqlite, use_csv, cfg
from utils.logging_config import get_logger

logger = get_logger(__name__)

_db_initialised = False


def _ensure_db():
    """Initialise the database, even in csv-only storage mode.

    Dedup records always use SQLite as the authoritative source, so we
    force database creation regardless of ``STORAGE_MODE``.
    """
    global _db_initialised
    if not _db_initialised:
        from utils.db import init_db
        init_db(force=True)
        _db_initialised = True


class RcloneEntry(NamedTuple):
    """A single record from rclone_inventory.csv."""
    video_code: str
    sensor_category: str
    subtitle_category: str
    folder_path: str
    folder_size: int
    file_count: int
    scan_datetime: str


class DedupRecord(NamedTuple):
    """A record to be written to dedup.csv."""
    video_code: str
    existing_sensor: str
    existing_subtitle: str
    existing_gdrive_path: str
    existing_folder_size: int
    new_torrent_category: str
    deletion_reason: str
    detect_datetime: str
    is_deleted: str      # "True" / "False"
    delete_datetime: str  # empty or timestamp


DEDUP_FIELDNAMES = [
    'video_code',
    'existing_sensor',
    'existing_subtitle',
    'existing_gdrive_path',
    'existing_folder_size',
    'new_torrent_category',
    'deletion_reason',
    'detect_datetime',
    'is_deleted',
    'delete_datetime',
]

# Priority maps (replicating rclone_dedup.SensorCategory logic to avoid heavy import)
WUMA_PRIORITY: Dict[str, int] = {
    '无码流出': 3,
    '无码': 2,
    '无码破解': 1,
}


def _is_wuma_category(cat: str) -> bool:
    return cat in WUMA_PRIORITY


def _get_wuma_priority(cat: str) -> int:
    return WUMA_PRIORITY.get(cat, 0)


# ---------------------------------------------------------------------------
# Inventory loading
# ---------------------------------------------------------------------------

def load_rclone_inventory(csv_path: str) -> Dict[str, List[RcloneEntry]]:
    """Load rclone inventory and return dict keyed by video_code.

    A single video_code may map to multiple entries (multiple GDrive copies).
    Returns an empty dict when the data source is empty.
    """
    if use_sqlite():
        _ensure_db()
    if use_sqlite():
        from utils.db import db_load_rclone_inventory
        raw = db_load_rclone_inventory()
        inventory: Dict[str, List[RcloneEntry]] = {}
        for code, entries in raw.items():
            inventory[code] = [
                RcloneEntry(
                    video_code=e.get('video_code', code).upper(),
                    sensor_category=e.get('sensor_category', ''),
                    subtitle_category=e.get('subtitle_category', ''),
                    folder_path=e.get('folder_path', ''),
                    folder_size=int(e.get('folder_size', 0) or 0),
                    file_count=int(e.get('file_count', 0) or 0),
                    scan_datetime=e.get('scan_datetime', ''),
                )
                for e in entries
            ]
        if inventory:
            logger.info(f"Loaded rclone inventory: {len(inventory)} unique codes from SQLite")
        else:
            logger.info("Rclone inventory is empty in SQLite – dedup skipped")
        return inventory

    return _csv_load_rclone_inventory(csv_path)


def _csv_load_rclone_inventory(csv_path: str) -> Dict[str, List[RcloneEntry]]:
    """CSV fallback for load_rclone_inventory."""
    if not os.path.exists(csv_path):
        logger.info(f"Rclone inventory not found: {csv_path} – dedup skipped")
        return {}

    inventory: Dict[str, List[RcloneEntry]] = {}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get('video_code', '').strip().upper()
                if not code:
                    continue
                entry = RcloneEntry(
                    video_code=code,
                    sensor_category=row.get('sensor_category', ''),
                    subtitle_category=row.get('subtitle_category', ''),
                    folder_path=row.get('folder_path', ''),
                    folder_size=int(row.get('folder_size', 0) or 0),
                    file_count=int(row.get('file_count', 0) or 0),
                    scan_datetime=row.get('scan_datetime', ''),
                )
                inventory.setdefault(code, []).append(entry)
        logger.info(f"Loaded rclone inventory: {len(inventory)} unique codes from {csv_path}")
    except Exception as e:
        logger.error(f"Failed to load rclone inventory: {e}")
    return inventory


# ---------------------------------------------------------------------------
# Skip logic
# ---------------------------------------------------------------------------

def is_in_rclone_inventory(video_code: str, inventory: Dict[str, List[RcloneEntry]]) -> bool:
    """Check whether a video_code exists in the rclone inventory."""
    return video_code.upper() in inventory


def should_skip_from_rclone(
    video_code: str,
    inventory: Dict[str, List[RcloneEntry]],
    enable_dedup: bool = False,
) -> bool:
    """Determine if the spider should skip processing this video_code based
    on the rclone inventory.

    When dedup is enabled, we never skip purely based on rclone inventory
    because we still want to detect potential upgrades.  When dedup is
    disabled, we skip if any entry for this code already has 中字.
    """
    code = video_code.upper()
    entries = inventory.get(code)
    if not entries:
        return False

    if enable_dedup:
        return False

    for entry in entries:
        if entry.subtitle_category == '中字':
            return True
    return False


# ---------------------------------------------------------------------------
# Dedup upgrade detection
# ---------------------------------------------------------------------------

def check_dedup_upgrade(
    video_code: str,
    new_torrent_types: Dict[str, bool],
    rclone_entries: List[RcloneEntry],
) -> List[DedupRecord]:
    """Compare a newly found torrent against existing GDrive entries and
    return a list of DedupRecords for entries that should be replaced.

    ``new_torrent_types`` is a dict like:
        {'subtitle': True, 'hacked_subtitle': False, ...}

    Upgrade rules:
      - Subtitle upgrade: GDrive has 无字, spider found 中字 torrent
      - Sensor upgrade: GDrive has 无码破解, spider found 无码 or 无码流出
    """
    records: List[DedupRecord] = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    has_subtitle = new_torrent_types.get('subtitle', False) or new_torrent_types.get('hacked_subtitle', False)

    for entry in rclone_entries:
        reason: Optional[str] = None

        # Subtitle upgrade
        if has_subtitle and entry.subtitle_category == '无字':
            reason = "Subtitle upgrade (中字 found, replacing 无字)"

        # Sensor upgrade: only relevant within 无码 family
        if _is_wuma_category(entry.sensor_category):
            existing_prio = _get_wuma_priority(entry.sensor_category)
            # Check if the new torrent indicates a higher-priority sensor category
            # Spider torrent types don't directly encode the sensor category,
            # but we can infer: non-hacked subtitle/no_subtitle = 无码 or higher
            if not new_torrent_types.get('hacked_subtitle', False) and not new_torrent_types.get('hacked_no_subtitle', False):
                inferred_prio = _get_wuma_priority('无码')
                if inferred_prio > existing_prio:
                    sensor_reason = f"Sensor upgrade (无码 > {entry.sensor_category})"
                    if reason:
                        reason = f"{reason}; {sensor_reason}"
                    else:
                        reason = sensor_reason

        if reason:
            new_cat_parts = []
            if new_torrent_types.get('subtitle') or new_torrent_types.get('hacked_subtitle'):
                new_cat_parts.append('中字')
            else:
                new_cat_parts.append('无字')
            if new_torrent_types.get('hacked_subtitle') or new_torrent_types.get('hacked_no_subtitle'):
                new_cat_parts.append('破解')

            records.append(DedupRecord(
                video_code=video_code.upper(),
                existing_sensor=entry.sensor_category,
                existing_subtitle=entry.subtitle_category,
                existing_gdrive_path=entry.folder_path,
                existing_folder_size=entry.folder_size,
                new_torrent_category='-'.join(new_cat_parts),
                deletion_reason=reason,
                detect_datetime=now_str,
                is_deleted='False',
                delete_datetime='',
            ))

    return records


# ---------------------------------------------------------------------------
# Persistent dedup.csv I/O
# ---------------------------------------------------------------------------

_pending_paths_cache: Optional[Set[str]] = None


def _load_pending_paths_cache() -> Set[str]:
    """Build the in-memory set of pending (not-yet-deleted) gdrive paths."""
    global _pending_paths_cache
    if _pending_paths_cache is not None:
        return _pending_paths_cache
    paths: Set[str] = set()
    try:
        _ensure_db()
        from utils.db import db_load_dedup_records
        for r in db_load_dedup_records():
            is_del = r.get('is_deleted')
            if is_del not in (1, True, 'True', '1'):
                p = r.get('existing_gdrive_path', '')
                if p:
                    paths.add(p)
        _pending_paths_cache = paths
    except Exception as e:
        logger.warning(f"Failed to load pending paths cache from DB: {e}")
    return _pending_paths_cache if _pending_paths_cache is not None else paths


def _raw_csv_read(csv_path: str) -> List[Dict[str, str]]:
    """Read all rows from a CSV file (no storage-mode dispatch)."""
    if not os.path.exists(csv_path):
        return []
    rows: List[Dict[str, str]] = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def _atomic_csv_write(csv_path: str, rows: List[Dict[str, str]]) -> None:
    """Write *rows* to *csv_path* atomically via a temp file + os.replace."""
    parent = os.path.dirname(csv_path) or '.'
    os.makedirs(parent, exist_ok=True)
    fd = tempfile.NamedTemporaryFile(
        mode='w', newline='', encoding='utf-8',
        dir=parent, suffix='.tmp', delete=False,
    )
    try:
        writer = csv.DictWriter(fd, fieldnames=DEDUP_FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        fd.close()
        os.replace(fd.name, csv_path)
    except BaseException:
        fd.close()
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise


def load_dedup_csv(csv_path: str, from_file_only: bool = False) -> List[Dict[str, str]]:
    """Load all dedup records from persistent storage.

    When from_file_only is True, reads only from *csv_path* (for per-run execute).
    Otherwise: uses SQLite as the authoritative source when available; falls back
    to reading *csv_path* when the database is empty or unavailable and
    CSV mode is active.  Returns an empty list when no data exists.
    """
    rows: List[Dict[str, str]] = []

    if from_file_only:
        if csv_path and os.path.exists(csv_path):
            try:
                with open(csv_path, 'r', newline='', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    rows = [dict(r) for r in reader]
                logger.debug(f"Loaded {len(rows)} dedup records from CSV: {csv_path}")
            except Exception as e:
                logger.warning(f"Failed to read dedup CSV {csv_path}: {e}")
                rows = []
        return rows

    if use_sqlite():
        _ensure_db()
        from utils.db import db_load_dedup_records
        rows = db_load_dedup_records()
        for r in rows:
            r.pop('id', None)
            r['is_deleted'] = 'True' if r.get('is_deleted') in (1, True, 'True', '1') else 'False'
            r['existing_folder_size'] = str(r.get('existing_folder_size', 0))

    if not rows and use_csv() and csv_path and os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = [dict(r) for r in reader]
            logger.debug(f"Loaded {len(rows)} dedup records from CSV fallback: {csv_path}")
        except Exception as e:
            logger.warning(f"Failed to read dedup CSV {csv_path}: {e}")
            rows = []

    return rows


def append_dedup_record(dedup_csv_path: str, record: DedupRecord) -> bool:
    """Append a single DedupRecord to persistent storage (DB only).

    Returns ``True`` if the record was appended, ``False`` if a pending
    record for the same ``existing_gdrive_path`` already exists.

    The *dedup_csv_path* parameter is kept for API compatibility but is
    no longer written to.  Use :func:`export_dedup_db_to_csv` to produce
    a CSV snapshot from the DB when needed.
    """
    gdrive_path = record.existing_gdrive_path

    # Fast in-memory duplicate check
    cache = _load_pending_paths_cache()
    if gdrive_path and gdrive_path in cache:
        logger.debug(f"Skipped duplicate dedup for path: {gdrive_path}")
        return False

    _ensure_db()
    from utils.db import db_append_dedup_record
    row_id = db_append_dedup_record(record._asdict())

    if row_id == -1:
        logger.debug(f"Skipped duplicate dedup for path: {gdrive_path}")
        return False

    if gdrive_path:
        cache.add(gdrive_path)
    logger.debug(f"Appended dedup record: {record.video_code} – {record.deletion_reason}")
    return True


def mark_records_deleted(
    csv_path: str,
    path_datetime_pairs: List[Tuple[str, str]],
) -> int:
    """Mark specific dedup records as deleted (DB only).

    The *csv_path* parameter is kept for API compatibility but is no
    longer written to.  Use :func:`export_dedup_db_to_csv` to produce
    a CSV snapshot from the DB when needed.
    """
    _ensure_db()
    from utils.db import db_mark_records_deleted
    updated = db_mark_records_deleted(path_datetime_pairs)

    # Invalidate cache so next append sees the new state
    if _pending_paths_cache is not None:
        for path, _ in path_datetime_pairs:
            _pending_paths_cache.discard(path)

    return updated


def cleanup_deleted_records(
    csv_path: str,
    older_than_days: int = 30,
) -> int:
    """Remove dedup records deleted more than *older_than_days* ago (DB only).

    Records with empty ``delete_datetime`` are skipped even when
    ``is_deleted`` is true (data anomaly).

    The *csv_path* parameter is kept for API compatibility but is no
    longer written to.  Use :func:`export_dedup_db_to_csv` to produce
    a CSV snapshot from the DB when needed.
    """
    _ensure_db()
    from utils.db import db_cleanup_deleted_records
    removed = db_cleanup_deleted_records(older_than_days)

    logger.info(f"Cleaned up {removed} old deleted dedup records (retention={older_than_days}d)")
    return removed


def save_dedup_csv(csv_path: str, rows: List[Dict[str, str]]) -> None:
    """Overwrite all dedup records.

    .. deprecated::
        Use :func:`mark_records_deleted` for targeted updates instead.
    """
    logger.warning(
        "save_dedup_csv is deprecated — use mark_records_deleted "
        "for targeted updates instead"
    )
    if use_sqlite():
        _ensure_db()
        from utils.db import db_save_dedup_records
        db_save_dedup_records(rows)

    if use_csv():
        os.makedirs(os.path.dirname(csv_path) or '.', exist_ok=True)
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=DEDUP_FIELDNAMES, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


def export_dedup_db_to_csv(output_path: str) -> int:
    """Export the dedup_records table from SQLite to a CSV file.

    Mirrors the pattern used by ``rclone_manager.export_db_to_csv`` for
    the rclone_inventory table.
    """
    _ensure_db()
    from utils.db import db_load_dedup_records

    rows = db_load_dedup_records()
    if not rows:
        logger.warning("No dedup records in DB to export to CSV")
        return 0

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=DEDUP_FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            r.pop('id', None)
            r['is_deleted'] = 'True' if r.get('is_deleted') in (1, True, 'True', '1') else 'False'
            r['existing_folder_size'] = str(r.get('existing_folder_size', 0))
            writer.writerow(r)

    logger.info(f"Exported {len(rows)} dedup records from DB to {output_path}")
    return len(rows)
