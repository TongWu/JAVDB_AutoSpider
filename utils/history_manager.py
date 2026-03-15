"""
History Manager for JavDB Spider

Storage backend is controlled by ``STORAGE_MODE`` in config:
  - ``db``  – SQLite only (via utils.db)
  - ``csv`` – CSV only (or Rust CSV override when available)
  - ``duo`` – SQLite first, then CSV

When the Rust extension ``javdb_rust_core`` is available **and** SQLite
mode is disabled, the Rust implementation takes precedence for CSV ops.
"""

import csv
import os
from datetime import datetime, timedelta

from utils.config_helper import use_sqlite, use_csv
from utils.logging_config import get_logger

logger = get_logger(__name__)

RUST_HISTORY_AVAILABLE = False

HISTORY_FIELDNAMES = [
    'href', 'phase', 'video_code', 'create_datetime', 'update_datetime',
    'last_visited_datetime',
    'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
    'size_hacked_subtitle', 'size_hacked_no_subtitle',
    'size_subtitle', 'size_no_subtitle',
]

_db_initialised = False


def _ensure_db():
    """Lazily initialise the database on first use."""
    global _db_initialised
    if not _db_initialised:
        from utils.db import init_db
        init_db()
        _db_initialised = True


# ── Column normalisation (shared by CSV helpers) ────────────────────────

def _normalize_record_columns(record):
    """Normalize a history record for writing: handle old column names and
    ensure all required columns exist."""
    if 'create_date' in record:
        record.setdefault('create_datetime', record.pop('create_date'))
    if 'update_date' in record:
        record.setdefault('update_datetime', record.pop('update_date'))

    if ('create_datetime' not in record or not record['create_datetime']) and 'parsed_date' in record:
        record['create_datetime'] = record['parsed_date']
    if ('update_datetime' not in record or not record['update_datetime']) and 'parsed_date' in record:
        record['update_datetime'] = record['parsed_date']

    if not record.get('last_visited_datetime'):
        record['last_visited_datetime'] = record.get('update_datetime', '')

    if 'torrent_type' in record:
        for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
            record.setdefault(category, '')
        del record['torrent_type']

    for cat in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
        record.setdefault(cat, '')
    for cat in ['size_hacked_subtitle', 'size_hacked_no_subtitle', 'size_subtitle', 'size_no_subtitle']:
        record.setdefault(cat, '')


# ── Public API (same signatures as before) ──────────────────────────────

def load_parsed_movies_history(history_file, phase=None):
    """Load previously parsed movies from history with phase filtering."""
    if use_sqlite():
        _ensure_db()
    if use_sqlite():
        from utils.db import db_load_history
        history = db_load_history(phase=phase)
        if history:
            logger.info(f"Loaded {len(history)} previously parsed movies from history")
        else:
            logger.info("No parsed movies history found, starting fresh")
        return history
    return _csv_load_parsed_movies_history(history_file, phase)


def cleanup_history_file(history_file, href_records):
    """Clean up history file by removing duplicate records."""
    if not use_csv():
        return
    _csv_cleanup_history_file(history_file, href_records)


def maintain_history_limit(history_file, max_records=1000):
    """Maintain maximum records in history file (DISABLED)."""
    if not use_csv():
        return
    _csv_maintain_history_limit(history_file, max_records)


def save_parsed_movie_to_history(history_file, href, phase, video_code,
                                  magnet_links=None, size_links=None,
                                  file_count_links=None, resolution_links=None):
    """Save a parsed movie to the history, updating existing records with new magnet links."""
    if magnet_links is None:
        magnet_links = {'no_subtitle': ''}
    elif isinstance(magnet_links, list):
        magnet_links = {t: '' for t in magnet_links}
    if size_links is None:
        size_links = {}
    if file_count_links is None:
        file_count_links = {}
    if resolution_links is None:
        resolution_links = {}

    if use_sqlite():
        _ensure_db()
    if use_sqlite():
        from utils.db import db_upsert_history

        filtered = {}
        filtered_sizes = {}
        filtered_fc = {}
        filtered_res = {}
        if magnet_links.get('hacked_subtitle'):
            filtered['hacked_subtitle'] = magnet_links['hacked_subtitle']
            filtered_sizes['hacked_subtitle'] = size_links.get('hacked_subtitle', '')
            filtered_fc['hacked_subtitle'] = file_count_links.get('hacked_subtitle', 0)
            filtered_res['hacked_subtitle'] = resolution_links.get('hacked_subtitle')
        else:
            filtered['hacked_no_subtitle'] = magnet_links.get('hacked_no_subtitle', '')
            filtered_sizes['hacked_no_subtitle'] = size_links.get('hacked_no_subtitle', '')
            filtered_fc['hacked_no_subtitle'] = file_count_links.get('hacked_no_subtitle', 0)
            filtered_res['hacked_no_subtitle'] = resolution_links.get('hacked_no_subtitle')
        if magnet_links.get('subtitle'):
            filtered['subtitle'] = magnet_links['subtitle']
            filtered_sizes['subtitle'] = size_links.get('subtitle', '')
            filtered_fc['subtitle'] = file_count_links.get('subtitle', 0)
            filtered_res['subtitle'] = resolution_links.get('subtitle')
        else:
            filtered['no_subtitle'] = magnet_links.get('no_subtitle', '')
            filtered_sizes['no_subtitle'] = size_links.get('no_subtitle', '')
            filtered_fc['no_subtitle'] = file_count_links.get('no_subtitle', 0)
            filtered_res['no_subtitle'] = resolution_links.get('no_subtitle')

        db_upsert_history(href, video_code, filtered,
                          size_links=filtered_sizes,
                          file_count_links=filtered_fc,
                          resolution_links=filtered_res)
        logger.debug(f"Saved history for {href} with magnet links: {list(magnet_links.keys())}")

    if use_csv():
        _csv_save_parsed_movie_to_history(history_file, href, phase, video_code, magnet_links, size_links)


def validate_history_file(history_file):
    """Validate and fix history file format."""
    if use_sqlite():
        _ensure_db()
    if use_sqlite():
        if not use_csv():
            return True
    return _csv_validate_history_file(history_file)


def batch_update_last_visited(history_file, visited_hrefs):
    """Update last_visited_datetime for a set of hrefs."""
    if use_sqlite():
        _ensure_db()
    if use_sqlite():
        from utils.db import db_batch_update_last_visited
        updated = db_batch_update_last_visited(list(visited_hrefs))
        if updated:
            logger.debug(f"Updated last_visited_datetime for {updated} movies")

    if use_csv():
        _csv_batch_update_last_visited(history_file, visited_hrefs)


def check_torrent_in_history(history_file, href, torrent_type):
    """Check if the specified torrent is already in the history record."""
    if use_sqlite():
        _ensure_db()
    if use_sqlite():
        from utils.db import db_check_torrent_in_history
        return db_check_torrent_in_history(href, torrent_type)
    return _csv_check_torrent_in_history(history_file, href, torrent_type)


def add_downloaded_indicator_to_csv(csv_file, history_file):
    """Add downloaded indicators to torrents in CSV file.

    This function always reads/writes the *report* CSV file.  The history
    look-up goes through SQLite when available.
    """
    if not os.path.exists(csv_file):
        logger.error(f"CSV file not found: {csv_file}")
        return False

    try:
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)

        modified = False
        for row in rows:
            href = row['href']
            for column in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
                if row.get(column) and row[column].strip():
                    if check_torrent_in_history(history_file, href, column):
                        if row[column].strip() != '[DOWNLOADED PREVIOUSLY]':
                            row[column] = '[DOWNLOADED PREVIOUSLY]'
                            modified = True
                            logger.debug(f"Set downloaded indicator only for {href} - {column}")

        if modified:
            with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
            logger.info(f"Added downloaded indicators to {csv_file}")
            return True
        else:
            logger.info(f"No downloaded torrents found in {csv_file}")
            return True

    except Exception as e:
        logger.error(f"Error adding downloaded indicators to CSV: {e}")
        return False


# ── Pure helper functions (no I/O, unchanged) ────────────────────────────

def determine_torrent_types(magnet_links):
    """Determine torrent types from magnet links dictionary."""
    torrent_types = []
    if magnet_links.get('hacked_subtitle', '').strip():
        torrent_types.append('hacked_subtitle')
    if magnet_links.get('hacked_no_subtitle', '').strip():
        torrent_types.append('hacked_no_subtitle')
    if magnet_links.get('subtitle', '').strip():
        torrent_types.append('subtitle')
    if magnet_links.get('no_subtitle', '').strip():
        torrent_types.append('no_subtitle')
    return torrent_types


def determine_torrent_type(magnet_links):
    """Legacy function - use determine_torrent_types instead."""
    types = determine_torrent_types(magnet_links)
    return types[0] if types else 'no_subtitle'


def get_missing_torrent_types(history_torrent_types, current_torrent_types):
    """Get missing torrent types that should be searched for."""
    missing_types = []

    has_hacked_subtitle_in_history = 'hacked_subtitle' in history_torrent_types
    has_hacked_no_subtitle_in_history = 'hacked_no_subtitle' in history_torrent_types
    has_subtitle_in_history = 'subtitle' in history_torrent_types
    has_no_subtitle_in_history = 'no_subtitle' in history_torrent_types

    has_hacked_subtitle_current = 'hacked_subtitle' in current_torrent_types
    has_hacked_no_subtitle_current = 'hacked_no_subtitle' in current_torrent_types
    has_subtitle_current = 'subtitle' in current_torrent_types
    has_no_subtitle_current = 'no_subtitle' in current_torrent_types

    if has_hacked_subtitle_current and not has_hacked_subtitle_in_history:
        missing_types.append('hacked_subtitle')
    elif has_hacked_no_subtitle_current and not has_hacked_no_subtitle_in_history and not has_hacked_subtitle_in_history:
        missing_types.append('hacked_no_subtitle')

    if has_subtitle_current and not has_subtitle_in_history:
        missing_types.append('subtitle')
    elif has_no_subtitle_current and not has_no_subtitle_in_history and not has_subtitle_in_history:
        missing_types.append('no_subtitle')

    return missing_types


def has_complete_subtitles(href, history_data):
    """Check if a movie already has both subtitle and hacked_subtitle in history."""
    if not history_data or href not in history_data:
        return False
    entry = history_data[href]
    if entry.get('PerfectMatchIndicator'):
        return True
    torrent_types = entry.get('torrent_types', [])
    return 'subtitle' in torrent_types and 'hacked_subtitle' in torrent_types


def _get_visited_datetime(entry):
    """Get the last visited datetime from a history entry (handles both key styles)."""
    return (entry.get('DateTimeVisited', '')
            or entry.get('last_visited_datetime', '')
            or entry.get('DateTimeUpdated', '')
            or entry.get('update_datetime', ''))


def should_skip_recent_yesterday_release(href, history_data, is_yesterday_release):
    """Skip a movie if it was visited recently and is tagged as yesterday's release."""
    if not is_yesterday_release:
        return False
    if not history_data or href not in history_data:
        return False
    visited_str = _get_visited_datetime(history_data[href])
    if not visited_str:
        return False
    cutoff = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    return visited_str[:10] >= cutoff


def should_skip_recent_today_release(href, history_data, is_today_release):
    """Skip a movie if it was already visited today and is tagged as today's release."""
    if not is_today_release:
        return False
    if not history_data or href not in history_data:
        return False
    visited_str = _get_visited_datetime(history_data[href])
    if not visited_str:
        return False
    cutoff = datetime.now().strftime('%Y-%m-%d')
    return visited_str[:10] >= cutoff


def should_process_movie(href, history_data, phase, magnet_links):
    """Determine if a movie should be processed based on history and phase rules."""
    if href not in history_data:
        logger.debug(f"New movie {href}: should process")
        return True, None

    current_torrent_types = determine_torrent_types(magnet_links)
    history_torrent_types = history_data[href].get('torrent_types', ['no_subtitle'])

    logger.debug(f"Movie {href}: current={current_torrent_types}, history={history_torrent_types}, phase={phase}")

    missing_types = get_missing_torrent_types(history_torrent_types, current_torrent_types)

    if phase == 1:
        if missing_types:
            logger.debug(f"Phase 1: missing types {missing_types} -> should process")
            return True, history_torrent_types
        else:
            logger.debug(f"Phase 1: no missing types -> should not process")
            return False, history_torrent_types

    elif phase == 2:
        if 'no_subtitle' in history_torrent_types and 'hacked_no_subtitle' in current_torrent_types:
            logger.debug(f"Phase 2: upgrading no_subtitle to hacked_no_subtitle -> should process")
            return True, history_torrent_types
        elif missing_types:
            logger.debug(f"Phase 2: missing types {missing_types} -> should process")
            return True, history_torrent_types
        else:
            logger.debug(f"Phase 2: no upgrade possible -> should not process")
            return False, history_torrent_types

    return False, history_torrent_types


def check_redownload_upgrade(href, history_data, magnet_links, threshold=0.30):
    """Check if any same-category torrent qualifies for re-download (洗版).

    Compares the size of each torrent category in *magnet_links* against the
    size recorded in *history_data*.  If a new torrent is larger than the
    existing one by at least *threshold* (e.g. 0.30 = 30 %), that category
    is returned as an upgrade candidate.

    Returns:
        list of category names that qualify for re-download.
    """
    if not history_data or href not in history_data:
        return []

    from utils.magnet_extractor import _parse_size
    from utils.db import category_to_indicators

    entry = history_data[href]
    torrents = entry.get('torrents', {})
    upgrade_categories = []

    for cat in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
        new_magnet = magnet_links.get(cat, '')
        if not new_magnet:
            continue

        new_size_str = magnet_links.get(f'size_{cat}', '')
        if not new_size_str:
            continue

        # Look up old size from the torrents dict (SQLite) or flat keys (CSV)
        key = category_to_indicators(cat)
        old_torrent = torrents.get(key, {})
        old_size_str = old_torrent.get('Size', '') or entry.get(f'size_{cat}', '')
        if not old_size_str:
            continue

        old_bytes = _parse_size(old_size_str)
        new_bytes = _parse_size(new_size_str)
        if old_bytes <= 0:
            continue
        if new_bytes > old_bytes * (1 + threshold):
            logger.info(
                f"Re-download upgrade for {href} [{cat}]: "
                f"{old_size_str} -> {new_size_str} "
                f"(+{((new_bytes / old_bytes) - 1) * 100:.0f}%, threshold {threshold * 100:.0f}%)"
            )
            upgrade_categories.append(cat)

    return upgrade_categories


def is_downloaded_torrent(torrent_content):
    """Check if torrent content contains downloaded indicator."""
    return torrent_content.strip().startswith("[DOWNLOADED PREVIOUSLY]")


def mark_torrent_as_downloaded(history_file, href, video_code, torrent_type):
    """Mark a specific torrent type as downloaded in history."""
    try:
        save_parsed_movie_to_history(
            history_file, href, "2", video_code,
            {torrent_type: f'magnet:?dn=downloaded&vc={video_code}'},
        )
        logger.debug(f"Marked {torrent_type} as downloaded for {video_code} ({href})")
        return True
    except Exception as e:
        logger.error(f"Error marking torrent as downloaded: {e}")
        return False


# ── CSV fallback implementations (used when use_csv() is True) ───────────

def _csv_load_parsed_movies_history(history_file, phase=None):
    history = {}
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                records = list(reader)

            href_records = {}
            for row in records:
                href = row['href']
                if href not in href_records:
                    href_records[href] = row
                else:
                    existing_date = href_records[href].get('update_datetime', href_records[href].get('update_date', href_records[href].get('parsed_date', '')))
                    current_date = row.get('update_datetime', row.get('update_date', row.get('parsed_date', '')))
                    if current_date > existing_date:
                        href_records[href] = row

            for href, row in href_records.items():
                create_datetime = row.get('create_datetime', row.get('create_date', row.get('parsed_date', '')))
                update_datetime = row.get('update_datetime', row.get('update_date', row.get('parsed_date', '')))
                last_visited_datetime = row.get('last_visited_datetime', '') or update_datetime

                torrent_types = []
                if 'torrent_type' in row:
                    torrent_types_str = row.get('torrent_type', 'no_subtitle')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                else:
                    for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                        magnet_content = row.get(category, '').strip()
                        if magnet_content:
                            if magnet_content.startswith('[') and ']' in magnet_content:
                                magnet_link = magnet_content.split(']', 1)[1]
                                if magnet_link.startswith('magnet:'):
                                    torrent_types.append(category)
                            elif magnet_content.startswith('magnet:'):
                                torrent_types.append(category)

                def _build_entry():
                    return {
                        'phase': row['phase'],
                        'video_code': row['video_code'],
                        'create_datetime': create_datetime,
                        'update_datetime': update_datetime,
                        'last_visited_datetime': last_visited_datetime,
                        'torrent_types': torrent_types,
                        'hacked_subtitle': row.get('hacked_subtitle', ''),
                        'hacked_no_subtitle': row.get('hacked_no_subtitle', ''),
                        'subtitle': row.get('subtitle', ''),
                        'no_subtitle': row.get('no_subtitle', ''),
                        'size_hacked_subtitle': row.get('size_hacked_subtitle', ''),
                        'size_hacked_no_subtitle': row.get('size_hacked_no_subtitle', ''),
                        'size_subtitle': row.get('size_subtitle', ''),
                        'size_no_subtitle': row.get('size_no_subtitle', ''),
                    }

                if phase is None:
                    history[href] = _build_entry()
                elif phase == 1:
                    if row['phase'] != '2':
                        history[href] = _build_entry()
                elif phase == 2:
                    history[href] = _build_entry()

            if len(records) != len(href_records):
                logger.info(f"Found {len(records) - len(href_records)} duplicate records, cleaning up history file")
                _csv_cleanup_history_file(history_file, href_records)

            phase_counts = {}
            for record in history.values():
                record_phase = record['phase']
                phase_counts[record_phase] = phase_counts.get(record_phase, 0) + 1

            if phase is None:
                phase_details = ", ".join([f"phase {p}: {c}" for p, c in sorted(phase_counts.items())])
                logger.info(f"Loaded {len(history)} previously parsed movies from history ({phase_details})")
        except Exception as e:
            logger.error(f"Error loading parsed movies history: {e}")
    else:
        logger.info("No parsed movies history found, starting fresh")

    return history


def _csv_cleanup_history_file(history_file, href_records):
    try:
        def _get_update_dt(record):
            return record.get('update_datetime', record.get('update_date', record.get('parsed_date', '')))

        sorted_records = sorted(href_records.values(), key=lambda x: _get_update_dt(x), reverse=True)

        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDNAMES)
            writer.writeheader()
            for record in sorted_records:
                _normalize_record_columns(record)
                writer.writerow(record)

        logger.info(f"Cleaned up history file: removed duplicates, kept {len(sorted_records)} unique records")
    except Exception as e:
        logger.error(f"Error cleaning up history file: {e}")


def _csv_maintain_history_limit(history_file, max_records=1000):
    if not os.path.exists(history_file):
        return
    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)

        if len(records) > max_records:
            def _get_update_dt(record):
                return record.get('update_datetime', record.get('update_date', record.get('parsed_date', '')))

            records.sort(key=lambda x: _get_update_dt(x))
            records = records[-max_records:]

            with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDNAMES)
                writer.writeheader()
                for record in records:
                    _normalize_record_columns(record)
                    writer.writerow(record)

            logger.info(f"Maintained history limit: kept {len(records)} newest records, removed oldest entries")
    except Exception as e:
        logger.error(f"Error maintaining history limit: {e}")


def _csv_save_parsed_movie_to_history(history_file, href, phase, video_code, magnet_links=None, size_links=None):
    if magnet_links is None:
        magnet_links = {'no_subtitle': ''}
    elif isinstance(magnet_links, list):
        magnet_links = {t: '' for t in magnet_links}
    if size_links is None:
        size_links = {}

    records = []
    file_exists = os.path.exists(history_file)
    existing_count = 0
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_date = datetime.now().strftime("%Y-%m-%d")
    updated_record = None

    if file_exists:
        try:
            with open(history_file, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['href'] == href:
                        existing_count += 1

                        if 'torrent_type' in row:
                            existing_torrent_types = row.get('torrent_type', '').split(',')
                            existing_torrent_types = [t.strip() for t in existing_torrent_types if t.strip()]
                            all_torrent_types = list(set(existing_torrent_types + list(magnet_links.keys())))
                            all_torrent_types.sort()
                            row['torrent_type'] = ','.join(all_torrent_types)
                            row['update_datetime'] = current_time
                            row['last_visited_datetime'] = current_time
                            row['phase'] = phase
                        else:
                            filtered_links = {}
                            if magnet_links.get('hacked_subtitle'):
                                filtered_links['hacked_subtitle'] = magnet_links['hacked_subtitle']
                                filtered_links['hacked_no_subtitle'] = ''
                            else:
                                filtered_links['hacked_subtitle'] = ''
                                filtered_links['hacked_no_subtitle'] = magnet_links.get('hacked_no_subtitle', '')
                            if magnet_links.get('subtitle'):
                                filtered_links['subtitle'] = magnet_links['subtitle']
                                filtered_links['no_subtitle'] = ''
                            else:
                                filtered_links['subtitle'] = ''
                                filtered_links['no_subtitle'] = magnet_links.get('no_subtitle', '')

                            for torrent_type, magnet_link in filtered_links.items():
                                if torrent_type in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                                    old_content = row.get(torrent_type, '').strip()
                                    old_date = None
                                    if old_content.startswith('[') and ']' in old_content:
                                        try:
                                            old_date = old_content[1:old_content.index(']')]
                                        except Exception:
                                            old_date = None
                                    if magnet_link:
                                        if old_date:
                                            try:
                                                old_dt = datetime.strptime(old_date, "%Y-%m-%d")
                                                new_dt = datetime.strptime(current_date, "%Y-%m-%d")
                                                if new_dt > old_dt:
                                                    row[torrent_type] = f"[{current_date}]{magnet_link}"
                                                    row[f'size_{torrent_type}'] = size_links.get(torrent_type, '')
                                            except Exception:
                                                row[torrent_type] = f"[{current_date}]{magnet_link}"
                                                row[f'size_{torrent_type}'] = size_links.get(torrent_type, '')
                                        else:
                                            row[torrent_type] = f"[{current_date}]{magnet_link}"
                                            row[f'size_{torrent_type}'] = size_links.get(torrent_type, '')

                            row['update_datetime'] = current_time
                            row['last_visited_datetime'] = current_time
                            row['phase'] = phase

                        if row.get('hacked_subtitle', '').strip():
                            row['hacked_no_subtitle'] = ''
                            row['size_hacked_no_subtitle'] = ''
                        if row.get('subtitle', '').strip():
                            row['no_subtitle'] = ''
                            row['size_no_subtitle'] = ''

                        updated_record = row.copy()
                    else:
                        records.append(row)
        except Exception as e:
            logger.error(f"Error reading existing history: {e}")
            records = []

    if existing_count == 0:
        new_record = {
            'href': href, 'phase': phase, 'video_code': video_code,
            'create_datetime': current_time, 'update_datetime': current_time,
            'last_visited_datetime': current_time,
            'hacked_subtitle': '', 'hacked_no_subtitle': '',
            'subtitle': '', 'no_subtitle': '',
            'size_hacked_subtitle': '', 'size_hacked_no_subtitle': '',
            'size_subtitle': '', 'size_no_subtitle': '',
        }
        for torrent_type, magnet_link in magnet_links.items():
            if torrent_type in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                new_record[torrent_type] = f"[{current_date}]{magnet_link}" if magnet_link else ''
                new_record[f'size_{torrent_type}'] = size_links.get(torrent_type, '')
        if new_record.get('hacked_subtitle', '').strip():
            new_record['hacked_no_subtitle'] = ''
            new_record['size_hacked_no_subtitle'] = ''
        if new_record.get('subtitle', '').strip():
            new_record['no_subtitle'] = ''
            new_record['size_no_subtitle'] = ''
        records.insert(0, new_record)
    else:
        if updated_record:
            records.insert(0, updated_record)

    try:
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDNAMES)
            writer.writeheader()
            for record in records:
                _normalize_record_columns(record)
                writer.writerow(record)
    except Exception as e:
        logger.error(f"Error writing to history file: {e}")


def _csv_validate_history_file(history_file):
    if not os.path.exists(history_file):
        return True
    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)

        needs_conversion = any('torrent_type' in r and 'hacked_subtitle' not in r for r in records)

        if needs_conversion:
            logger.info("Converting history file from old format to new format")
            for record in records:
                _normalize_record_columns(record)
            with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDNAMES)
                writer.writeheader()
                for record in records:
                    writer.writerow(record)
            logger.info("Successfully converted history file to new format")
        return True
    except Exception as e:
        logger.error(f"Error validating history file: {e}")
        return False


def _csv_batch_update_last_visited(history_file, visited_hrefs):
    if not visited_hrefs or not os.path.exists(history_file):
        return
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    visited_set = set(visited_hrefs)
    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)
        updated = 0
        for record in records:
            if record.get('href') in visited_set:
                record['last_visited_datetime'] = current_time
                updated += 1
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDNAMES)
            writer.writeheader()
            for record in records:
                _normalize_record_columns(record)
                writer.writerow(record)
        if updated:
            logger.debug(f"Updated last_visited_datetime for {updated} movies")
    except Exception as e:
        logger.error(f"Error batch-updating last_visited_datetime: {e}")


def _csv_check_torrent_in_history(history_file, href, torrent_type):
    if not os.path.exists(history_file):
        return False
    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['href'] == href:
                    if 'torrent_type' in row:
                        recorded_types = [t.strip() for t in row.get('torrent_type', '').split(',') if t.strip()]
                        return torrent_type in recorded_types
                    else:
                        magnet_content = row.get(torrent_type, '').strip()
                        if magnet_content:
                            if magnet_content.startswith('[') and ']' in magnet_content:
                                magnet_link = magnet_content.split(']', 1)[1]
                                return magnet_link.startswith('magnet:')
                            elif magnet_content.startswith('magnet:'):
                                return True
                        return False
        return False
    except Exception as e:
        logger.error(f"Error checking torrent in history: {e}")
        return False


# ── Rust-first override (CSV-only mode) ──────────────────────────────────
# When SQLite is enabled (db / duo), we use the Python implementation above.
# The Rust extension only overrides when running in pure-CSV mode.

if not use_sqlite():
    try:
        from javdb_rust_core import (
            load_parsed_movies_history,
            cleanup_history_file,
            maintain_history_limit,
            save_parsed_movie_to_history,
            validate_history_file,
            determine_torrent_types,
            determine_torrent_type,
            get_missing_torrent_types,
            has_complete_subtitles,
            should_skip_recent_yesterday_release,
            should_skip_recent_today_release,
            batch_update_last_visited,
            should_process_movie,
            check_torrent_in_history,
            add_downloaded_indicator_to_csv,
            is_downloaded_torrent,
            mark_torrent_as_downloaded,
        )
        RUST_HISTORY_AVAILABLE = True
        logger.debug("Rust history manager loaded - using high-performance Rust implementation")
    except ImportError as e:
        logger.warning(f"Rust history manager not available (ImportError: {e}) - using pure-Python implementation")
