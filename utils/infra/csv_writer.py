"""CSV writing and merging utilities with Rust acceleration (Python fallback).

Handles incremental CSV writing with merge-on-write semantics: when
``append_mode=True``, existing rows keyed by ``video_code`` are merged
with new data so that no information is lost.

Also writes report rows to SQLite when a ``session_id`` is provided.
"""

import csv
import os
import logging

logger = logging.getLogger(__name__)

_active_session_id = None


def set_active_session(session_id):
    """Set the current session_id for SQLite report_rows writes."""
    global _active_session_id
    _active_session_id = session_id


def get_active_session():
    """Return the current active session_id (or None)."""
    return _active_session_id

try:
    from javdb_rust_core import merge_row_data as _rs_merge_row_data
    RUST_CSV_AVAILABLE = True
except ImportError:
    RUST_CSV_AVAILABLE = False

# ---------------------------------------------------------------------------
# merge_row_data
# ---------------------------------------------------------------------------

_DOWNLOADED_PLACEHOLDER = '[DOWNLOADED PREVIOUSLY]'


def _py_merge_row_data(existing_row, new_row):
    merged = existing_row.copy()
    for key, new_value in new_row.items():
        existing_value = merged.get(key, '')
        new_str = str(new_value) if new_value is not None else ''
        existing_str = str(existing_value) if existing_value is not None else ''

        if new_str == _DOWNLOADED_PLACEHOLDER:
            if not existing_str:
                merged[key] = new_value
        elif new_str:
            merged[key] = new_value
    return merged


def _rs_merge_row_data_safe(existing_row, new_row):
    return _rs_merge_row_data(
        {k: str(v) if v is not None else '' for k, v in existing_row.items()},
        {k: str(v) if v is not None else '' for k, v in new_row.items()},
    )


merge_row_data = _rs_merge_row_data_safe if RUST_CSV_AVAILABLE else _py_merge_row_data

# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------


def write_csv(rows, csv_path, fieldnames, dry_run=False, append_mode=False):
    """Write results to CSV file or print if dry-run.

    When *append_mode* is True and the file already exists, rows are merged
    by ``video_code``.  New data takes priority, but existing values are
    preserved when the new value is empty.

    Respects ``STORAGE_MODE``: CSV I/O is skipped in ``db``-only mode;
    SQLite writes are skipped in ``csv``-only mode.
    """
    from utils.infra.config_helper import use_csv as _use_csv

    if dry_run:
        logger.info(f"[DRY RUN] Would write {len(rows)} entries to {csv_path}")
        logger.info("[DRY RUN] Sample entries:")
        for i, row in enumerate(rows[:3]):
            logger.info(f"[DRY RUN] Entry {i + 1}: {row['video_code']} (Page {row['page']})")
        if len(rows) > 3:
            logger.info(f"[DRY RUN] ... and {len(rows) - 3} more entries")
        return

    if _use_csv() and append_mode and os.path.exists(csv_path):
        existing_rows = {}
        rows_without_key = []
        existing_fieldnames = []
        read_failed = False
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                existing_fieldnames = reader.fieldnames or []
                for row in reader:
                    video_code = row.get('video_code', '')
                    if video_code:
                        existing_rows[video_code] = row
                    else:
                        rows_without_key.append(row)
            if rows_without_key:
                logger.warning(f"[CSV] Found {len(rows_without_key)} existing rows without video_code - preserving them")
        except Exception as e:
            logger.error(f"Error reading existing CSV file: {e}. Falling back to append-only to avoid data loss.")
            read_failed = True

        if read_failed:
            with open(csv_path, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                for row in rows:
                    writer.writerow(row)
            logger.info(f"[CSV] Appended {len(rows)} rows to {csv_path} (merge skipped due to read error)")
        else:
            merged_count = 0
            added_count = 0
            for new_row in rows:
                video_code = new_row.get('video_code', '')
                if not video_code:
                    rows_without_key.append(new_row)
                    added_count += 1
                    logger.warning("[CSV] Added new entry without video_code (cannot merge)")
                elif video_code in existing_rows:
                    existing_rows[video_code] = merge_row_data(existing_rows[video_code], new_row)
                    merged_count += 1
                    logger.debug(f"[CSV] Merged existing entry: {video_code}")
                else:
                    existing_rows[video_code] = new_row
                    added_count += 1
                    logger.debug(f"[CSV] Added new entry: {video_code}")

            merged_fieldnames = list(fieldnames)
            for existing_field in existing_fieldnames:
                if existing_field not in merged_fieldnames:
                    merged_fieldnames.append(existing_field)
                    logger.debug(f"[CSV] Preserving extra column from existing CSV: {existing_field}")

            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=merged_fieldnames, extrasaction='ignore')
                writer.writeheader()
                for row in existing_rows.values():
                    writer.writerow(row)
                for row in rows_without_key:
                    writer.writerow(row)

            total_entries = len(existing_rows) + len(rows_without_key)
            if merged_count > 0 or added_count > 0:
                logger.debug(f"[CSV] Updated {csv_path}: {merged_count} merged, {added_count} added, {total_entries} total entries")
    elif _use_csv():
        logger.debug(f"[CSV] Writing new file: {csv_path}")
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        logger.debug(f"[CSV] Created {csv_path} with {len(rows)} entries")

    if _active_session_id is not None and rows:
        try:
            from utils.infra.config_helper import use_sqlite
            if use_sqlite():
                from utils.infra.db import db_insert_report_rows
                db_insert_report_rows(_active_session_id, rows)
        except Exception as e:
            logger.warning(f"[CSV] Failed to write rows to SQLite: {e}")
