"""Inventory loading and persistence for skip-time dedup."""

import contextlib
import csv
import os
import tempfile
from typing import Dict, List, Optional, Set, Tuple

from javdb.infra.config import use_csv, use_sqlite
from javdb.infra.logging import get_logger
from javdb.parsing.common import normalise_code
from javdb.spider.services.dedup_types import DEDUP_FIELDNAMES, DedupRecord, RcloneEntry
from javdb.storage.repos.operations_repo import OperationsRepo

logger = get_logger(__name__)

_db_initialised = False


def _ensure_db():
    """Initialise the database, even in csv-only storage mode.

    Dedup records always use SQLite as the authoritative source, so we
    force database creation regardless of ``STORAGE_MODE``.
    """
    global _db_initialised
    if not _db_initialised:
        from javdb.storage.db import init_db
        init_db(force=True)
        _db_initialised = True


@contextlib.contextmanager
def _open_ledger_for_dedup():
    """Yield an OwnershipLedgerRepo, closing the DB connection on exit.

    A context manager (callers use ``with``) so the operations-DB connection is
    released after each dedup read instead of leaking — important because
    should_skip_from_ownership may be called per-movie in a scrape loop
    (CodeRabbit review on PR #179). Extracted as a module-level function so
    tests can monkeypatch it."""
    from javdb.ops.reconcile.persistence import open_ledger_repo
    with open_ledger_repo() as repo:
        yield repo


def _ledger_has_gdrive_rows(repo) -> bool:
    """Return True if the Ledger contains any gdrive rows (present or absent)."""
    return bool(repo.list_by_source("gdrive"))


def _split_glyph_category(category: str) -> tuple:
    """Split a gdrive Ledger composite '<sensor>|<subtitle>' (D-P2-1) back into
    (sensor_category, subtitle_category). Empty/malformed -> ('', '')."""
    sensor, sep, subtitle = (category or "").partition("|")
    return (sensor, subtitle) if sep else ("", "")


def _ledger_to_inventory(rows) -> Dict[str, List[RcloneEntry]]:
    """Synthesize a RcloneEntry inventory dict from OwnershipLedger rows."""
    inventory: Dict[str, List[RcloneEntry]] = {}
    for rec in rows:
        if rec.present != 1:
            continue
        code = normalise_code(rec.video_code)
        sensor, subtitle = _split_glyph_category(rec.category)
        inventory.setdefault(code, []).append(RcloneEntry(
            video_code=code, sensor_category=sensor, subtitle_category=subtitle,
            folder_path=rec.path or "", folder_size=int(rec.size or 0),
            file_count=0, scan_datetime=rec.observed_at or "",
        ))
    return inventory


def _legacy_load_rclone_inventory(csv_path: str) -> Dict[str, List[RcloneEntry]]:
    """Legacy load path: read from RcloneInventory (OperationsRepo) table."""
    from javdb.storage.db import current_backend
    raw = OperationsRepo().load_rclone_inventory()
    inventory: Dict[str, List[RcloneEntry]] = {}
    for code, entries in raw.items():
        normalised_code = normalise_code(code)
        inventory.setdefault(normalised_code, []).extend(
            RcloneEntry(
                video_code=normalise_code(
                    e.get('VideoCode', e.get('video_code', normalised_code))
                ),
                sensor_category=e.get('SensorCategory', e.get('sensor_category', '')),
                subtitle_category=e.get('SubtitleCategory', e.get('subtitle_category', '')),
                folder_path=e.get('FolderPath', e.get('folder_path', '')),
                folder_size=int(e.get('FolderSize', e.get('folder_size', 0)) or 0),
                file_count=int(e.get('FileCount', e.get('file_count', 0)) or 0),
                scan_datetime=e.get('DateTimeScanned', e.get('scan_datetime', '')),
            )
            for e in entries
        )
    backend = current_backend()
    if inventory:
        logger.info(f"Loaded rclone inventory: {len(inventory)} unique codes from {backend} backend")
    else:
        logger.info(f"Rclone inventory is empty in {backend} backend - dedup skipped")
    return inventory


def load_rclone_inventory(csv_path: str) -> Dict[str, List[RcloneEntry]]:
    """Load rclone inventory and return dict keyed by video_code.

    A single video_code may map to multiple entries (multiple GDrive copies).
    Returns an empty dict when the data source is empty.

    Preferred path (D-P2-6): reads OwnershipLedger rows where source='gdrive'
    and synthesizes RcloneEntry objects by splitting the glyph composite
    category ('<sensor>|<subtitle>') back into sensor_category/subtitle_category.

    Transitional fallback (D-P2-9): if the Ledger has zero gdrive rows AND
    RcloneInventory is non-empty, falls back to the legacy OperationsRepo path.
    Remove this fallback once the Ledger is proven populated in production.
    """
    if use_sqlite():
        _ensure_db()
        with _open_ledger_for_dedup() as repo:
            if _ledger_has_gdrive_rows(repo):
                gdrive_rows = repo.list_by_source("gdrive")
                inventory = _ledger_to_inventory(gdrive_rows)
                if inventory:
                    logger.info(
                        "Loaded rclone inventory: %d unique codes from OwnershipLedger (gdrive)",
                        len(inventory),
                    )
                else:
                    logger.info("Rclone inventory is empty in OwnershipLedger (gdrive) - dedup skipped")
                return inventory
            # D-P2-9 transitional fallback: Ledger unpopulated, try legacy table.
            legacy = _legacy_load_rclone_inventory(csv_path)
            if legacy:
                logger.warning(
                    "OwnershipLedger has no gdrive rows — falling back to legacy "
                    "RcloneInventory table (%d codes). Populate the Ledger to remove this fallback.",
                    len(legacy),
                )
            else:
                logger.info("OwnershipLedger has no gdrive rows and RcloneInventory is empty - dedup skipped")
            return legacy

    return _csv_load_rclone_inventory(csv_path)


def should_skip_from_ownership(video_code: str) -> bool:
    # TODO(ADR-049): zero prod callers — dead-code candidate
    """Skip a video_code already owned in a *persistent* source (gdrive/nas).

    Unlike should_skip_from_rclone (gdrive only), this consults the multi-source
    Ledger but deliberately ignores qb/pikpak (in-transit / mirror) so a
    downloading qB torrent never suppresses upgrade detection (D-P2-7)."""
    from javdb.ops.reconcile.models import PERSISTENT_OWNERSHIP_SOURCES
    with _open_ledger_for_dedup() as repo:
        owned = repo.list_present_video_codes(PERSISTENT_OWNERSHIP_SOURCES)
    return normalise_code(video_code) in owned


def _csv_load_rclone_inventory(csv_path: str) -> Dict[str, List[RcloneEntry]]:
    """CSV fallback for load_rclone_inventory."""
    if not os.path.exists(csv_path):
        logger.info(f"Rclone inventory not found: {csv_path} - dedup skipped")
        return {}

    inventory: Dict[str, List[RcloneEntry]] = {}
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = normalise_code(row.get('video_code', ''))
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
    except Exception:
        logger.exception("Failed to load rclone inventory")
    return inventory


_pending_paths_cache: Optional[Set[str]] = None


def _load_pending_paths_cache() -> Set[str]:
    """Build the in-memory set of pending (not-yet-deleted) gdrive paths."""
    global _pending_paths_cache
    if _pending_paths_cache is not None:
        return _pending_paths_cache
    paths: Set[str] = set()
    try:
        _ensure_db()
        for r in OperationsRepo().load_dedup_records():
            is_del = r.get('IsDeleted', r.get('is_deleted'))
            if is_del not in (1, True, 'True', '1'):
                p = r.get('ExistingGdrivePath', r.get('existing_gdrive_path', ''))
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
        rows = OperationsRepo().load_dedup_records()
        for r in rows:
            r.pop('Id', None)
            r.pop('id', None)
            r['is_deleted'] = 'True' if r.get('IsDeleted', r.get('is_deleted')) in (1, True, 'True', '1') else 'False'
            r['existing_folder_size'] = str(r.get('ExistingFolderSize', r.get('existing_folder_size', 0)))

    if not rows and use_csv() and csv_path and os.path.exists(csv_path):
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = [dict(r) for r in reader]
            logger.debug(f"Loaded {len(rows)} dedup records from CSV fallback: {csv_path}")
        except Exception:
            logger.exception("Failed to read dedup CSV %s", csv_path)
            rows = []

    return rows


def append_dedup_record(
    dedup_csv_path: str,
    record: DedupRecord,
    session_id: Optional[str] = None,
) -> bool:
    """Append a single DedupRecord to persistent storage (DB only).

    Returns ``True`` if the record was appended, ``False`` if a pending
    record for the same ``existing_gdrive_path`` already exists.

    The *dedup_csv_path* parameter is kept for API compatibility but is
    no longer written to.  Use :func:`export_dedup_db_to_csv` to produce
    a CSV snapshot from the DB when needed.

    *session_id* (ADR-046 D2 — never ambient) tags the write. DedupRecords
    has a nullable SessionId, so a ``None`` session_id persists the row
    untagged without raising (Phase-2 contract).
    """
    gdrive_path = record.existing_gdrive_path

    # Fast in-memory duplicate check
    cache = _load_pending_paths_cache()
    if gdrive_path and gdrive_path in cache:
        logger.debug(f"Skipped duplicate dedup for path: {gdrive_path}")
        return False

    _ensure_db()
    # ADR-046 D2: the write session is passed in explicitly (the global is
    # never read). DedupRecords.SessionId is nullable, so a None session_id
    # persists the row untagged without raising (Phase-2 contract).
    row_id = OperationsRepo(session_id=session_id).append_dedup_record(record._asdict())

    if row_id == -1:
        logger.debug(f"Skipped duplicate dedup for path: {gdrive_path}")
        return False

    if gdrive_path:
        cache.add(gdrive_path)
    logger.debug(f"Appended dedup record: {record.video_code} - {record.deletion_reason}")
    return True


def mark_records_deleted(
    csv_path: str,
    path_datetime_pairs: List[Tuple[str, str]],
    session_id: Optional[str] = None,
) -> int:
    """Mark specific dedup records as deleted (DB only).

    The *csv_path* parameter is kept for API compatibility but is no
    longer written to.  Use :func:`export_dedup_db_to_csv` to produce
    a CSV snapshot from the DB when needed.

    *session_id* (ADR-046 D2 — never ambient) tags the update; ``None``
    leaves the row untagged without raising (DedupRecords.SessionId is
    nullable — the Phase-2 contract).
    """
    _ensure_db()
    # ADR-046 D2: bind the explicit session on the repo (the global is never
    # read). A None session_id is valid for this nullable-SessionId table.
    updated = OperationsRepo(session_id=session_id).mark_records_deleted(path_datetime_pairs)

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
    removed = OperationsRepo().cleanup_deleted_records(older_than_days)

    logger.info(f"Cleaned up {removed} old deleted dedup records (retention={older_than_days}d)")
    return removed


def save_dedup_csv(csv_path: str, rows: List[Dict[str, str]]) -> None:
    """Overwrite all dedup records.

    .. deprecated::
        Use :func:`mark_records_deleted` for targeted updates instead.
    """
    logger.warning(
        "save_dedup_csv is deprecated - use mark_records_deleted "
        "for targeted updates instead"
    )
    if use_sqlite():
        _ensure_db()
        OperationsRepo().save_dedup_records(rows)

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
    rows = OperationsRepo().load_dedup_records()
    if not rows:
        logger.warning("No dedup records in DB to export to CSV")
        return 0

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=DEDUP_FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            r.pop('Id', None)
            r.pop('id', None)
            r['video_code'] = r.get('VideoCode', r.get('video_code', ''))
            r['existing_sensor'] = r.get('ExistingSensor', r.get('existing_sensor', ''))
            r['existing_subtitle'] = r.get('ExistingSubtitle', r.get('existing_subtitle', ''))
            r['existing_gdrive_path'] = r.get('ExistingGdrivePath', r.get('existing_gdrive_path', ''))
            r['existing_folder_size'] = str(r.get('ExistingFolderSize', r.get('existing_folder_size', 0)))
            r['new_torrent_category'] = r.get('NewTorrentCategory', r.get('new_torrent_category', ''))
            r['deletion_reason'] = r.get('DeletionReason', r.get('deletion_reason', ''))
            r['detect_datetime'] = r.get('DateTimeDetected', r.get('detect_datetime', ''))
            r['is_deleted'] = 'True' if r.get('IsDeleted', r.get('is_deleted')) in (1, True, 'True', '1') else 'False'
            r['delete_datetime'] = r.get('DateTimeDeleted', r.get('delete_datetime', ''))
            writer.writerow(r)

    logger.info(f"Exported {len(rows)} dedup records from DB to {output_path}")
    return len(rows)
