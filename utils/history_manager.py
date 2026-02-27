"""
History Manager — powered by javdb_rust_core.

All functions are provided by the high-performance Rust implementation.
The original Python implementations are preserved below as commented-out
reference code.
"""

import logging

try:
    from config import LOG_LEVEL
except ImportError:
    LOG_LEVEL = 'INFO'

from utils.logging_config import get_logger, setup_logging
setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)

# ── Rust core imports ────────────────────────────────────────────────────
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
        should_process_movie,
        check_torrent_in_history,
        add_downloaded_indicator_to_csv,
        is_downloaded_torrent,
        mark_torrent_as_downloaded,
    )
    RUST_HISTORY_AVAILABLE = True
    logger.info("Rust history manager loaded - using high-performance Rust implementation")
except Exception as e:
    RUST_HISTORY_AVAILABLE = False
    logger.error("Failed to import javdb_rust_core history functions: %s", e)
    logger.warning("Falling back to Python stub implementations — history operations will raise RuntimeError")

    _UNAVAILABLE_MSG = (
        "javdb_rust_core is not available. Install the Rust wheel to use history functions. "
        "Import error: %s" % e
    )

    def _rust_unavailable(*args, **kwargs):
        raise RuntimeError(_UNAVAILABLE_MSG)

    load_parsed_movies_history = _rust_unavailable
    cleanup_history_file = _rust_unavailable
    maintain_history_limit = _rust_unavailable
    save_parsed_movie_to_history = _rust_unavailable
    validate_history_file = _rust_unavailable
    determine_torrent_types = _rust_unavailable
    determine_torrent_type = _rust_unavailable
    get_missing_torrent_types = _rust_unavailable
    has_complete_subtitles = _rust_unavailable
    should_process_movie = _rust_unavailable
    check_torrent_in_history = _rust_unavailable
    add_downloaded_indicator_to_csv = _rust_unavailable
    is_downloaded_torrent = _rust_unavailable
    mark_torrent_as_downloaded = _rust_unavailable


# ═════════════════════════════════════════════════════════════════════════
# ORIGINAL PYTHON IMPLEMENTATIONS (commented out — kept as reference)
# ═════════════════════════════════════════════════════════════════════════

# import csv
# import os
# from datetime import datetime
#
#
# def load_parsed_movies_history(history_file, phase=None):
#     """Load previously parsed movies from CSV file with phase filtering"""
#     history = {}
#
#     if os.path.exists(history_file):
#         try:
#             with open(history_file, 'r', encoding='utf-8-sig') as f:
#                 reader = csv.DictReader(f)
#                 records = list(reader)
#
#             href_records = {}
#             for row in records:
#                 href = row['href']
#                 if href not in href_records:
#                     href_records[href] = row
#                 else:
#                     existing_date = href_records[href].get('update_date', href_records[href].get('parsed_date', ''))
#                     current_date = row.get('update_date', row.get('parsed_date', ''))
#                     if current_date > existing_date:
#                         href_records[href] = row
#
#             for href, row in href_records.items():
#                 create_date = row.get('create_date', row.get('parsed_date', ''))
#                 update_date = row.get('update_date', row.get('parsed_date', ''))
#
#                 torrent_types = []
#                 if 'torrent_type' in row:
#                     torrent_types_str = row.get('torrent_type', 'no_subtitle')
#                     torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
#                 else:
#                     torrent_categories = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
#                     for category in torrent_categories:
#                         magnet_content = row.get(category, '').strip()
#                         if magnet_content:
#                             if magnet_content.startswith('[') and ']' in magnet_content:
#                                 magnet_link = magnet_content.split(']', 1)[1]
#                                 if magnet_link.startswith('magnet:'):
#                                     torrent_types.append(category)
#                             elif magnet_content.startswith('magnet:'):
#                                 torrent_types.append(category)
#
#                 if phase is None:
#                     history[href] = {
#                         'phase': row['phase'],
#                         'video_code': row['video_code'],
#                         'create_date': create_date,
#                         'update_date': update_date,
#                         'torrent_types': torrent_types,
#                         'hacked_subtitle': row.get('hacked_subtitle', ''),
#                         'hacked_no_subtitle': row.get('hacked_no_subtitle', ''),
#                         'subtitle': row.get('subtitle', ''),
#                         'no_subtitle': row.get('no_subtitle', '')
#                     }
#                 elif phase == 1:
#                     if row['phase'] != '2':
#                         history[href] = {
#                             'phase': row['phase'],
#                             'video_code': row['video_code'],
#                             'create_date': create_date,
#                             'update_date': update_date,
#                             'torrent_types': torrent_types,
#                             'hacked_subtitle': row.get('hacked_subtitle', ''),
#                             'hacked_no_subtitle': row.get('hacked_no_subtitle', ''),
#                             'subtitle': row.get('subtitle', ''),
#                             'no_subtitle': row.get('no_subtitle', '')
#                         }
#                 elif phase == 2:
#                     history[href] = {
#                         'phase': row['phase'],
#                         'video_code': row['video_code'],
#                         'create_date': create_date,
#                         'update_date': update_date,
#                         'torrent_types': torrent_types,
#                         'hacked_subtitle': row.get('hacked_subtitle', ''),
#                         'hacked_no_subtitle': row.get('hacked_no_subtitle', ''),
#                         'subtitle': row.get('subtitle', ''),
#                         'no_subtitle': row.get('no_subtitle', '')
#                     }
#
#             if len(records) != len(href_records):
#                 logger.info(f"Found {len(records) - len(href_records)} duplicate records, cleaning up history file")
#                 cleanup_history_file(history_file, href_records)
#
#             phase_counts = {}
#             for record in history.values():
#                 record_phase = record['phase']
#                 phase_counts[record_phase] = phase_counts.get(record_phase, 0) + 1
#
#             if phase is None:
#                 phase_details = ", ".join([f"phase {p}: {c}" for p, c in sorted(phase_counts.items())])
#                 logger.info(f"Loaded {len(history)} previously parsed movies from history ({phase_details})")
#         except Exception as e:
#             logger.error(f"Error loading parsed movies history: {e}")
#     else:
#         logger.info("No parsed movies history found, starting fresh")
#
#     return history
#
#
# def cleanup_history_file(history_file, href_records):
#     """Clean up history file by removing duplicate records"""
#     try:
#         def get_update_date(record):
#             return record.get('update_date', record.get('parsed_date', ''))
#
#         sorted_records = sorted(href_records.values(), key=lambda x: get_update_date(x), reverse=True)
#
#         with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
#             fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date',
#                          'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
#             writer = csv.DictWriter(f, fieldnames=fieldnames)
#             writer.writeheader()
#             for record in sorted_records:
#                 if 'create_date' not in record and 'parsed_date' in record:
#                     record['create_date'] = record['parsed_date']
#                 if 'update_date' not in record and 'parsed_date' in record:
#                     record['update_date'] = record['parsed_date']
#                 if 'torrent_type' in record and 'hacked_subtitle' not in record:
#                     torrent_types_str = record.get('torrent_type', '')
#                     torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
#                     for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
#                         record[category] = ''
#                 if 'torrent_type' in record:
#                     del record['torrent_type']
#                 writer.writerow(record)
#
#         logger.info(f"Cleaned up history file: removed duplicates, kept {len(sorted_records)} unique records")
#     except Exception as e:
#         logger.error(f"Error cleaning up history file: {e}")
#
#
# def maintain_history_limit(history_file, max_records=1000):
#     """Maintain maximum records in history file by removing oldest entries"""
#     if not os.path.exists(history_file):
#         return
#     try:
#         with open(history_file, 'r', encoding='utf-8-sig') as f:
#             reader = csv.DictReader(f)
#             records = list(reader)
#         if len(records) > max_records:
#             def get_update_date(record):
#                 return record.get('update_date', record.get('parsed_date', ''))
#             records.sort(key=lambda x: get_update_date(x))
#             records = records[-max_records:]
#             with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
#                 fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date',
#                              'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
#                 writer = csv.DictWriter(f, fieldnames=fieldnames)
#                 writer.writeheader()
#                 for record in records:
#                     if 'create_date' not in record and 'parsed_date' in record:
#                         record['create_date'] = record['parsed_date']
#                     if 'update_date' not in record and 'parsed_date' in record:
#                         record['update_date'] = record['parsed_date']
#                     if 'torrent_type' in record and 'hacked_subtitle' not in record:
#                         for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
#                             record[category] = ''
#                     if 'torrent_type' in record:
#                         del record['torrent_type']
#                     writer.writerow(record)
#             logger.info(f"Maintained history limit: kept {len(records)} newest records")
#     except Exception as e:
#         logger.error(f"Error maintaining history limit: {e}")
#
#
# def save_parsed_movie_to_history(history_file, href, phase, video_code, magnet_links=None):
#     """Save a parsed movie to the history CSV file"""
#     ...  # ~170 lines omitted for brevity — see git history for full implementation
#
#
# def validate_history_file(history_file):
#     """Validate and fix history file format"""
#     ...  # ~60 lines omitted — see git history
#
#
# def determine_torrent_types(magnet_links):
#     """Determine torrent types from magnet links dictionary"""
#     torrent_types = []
#     if magnet_links.get('hacked_subtitle', '').strip():
#         torrent_types.append('hacked_subtitle')
#     if magnet_links.get('hacked_no_subtitle', '').strip():
#         torrent_types.append('hacked_no_subtitle')
#     if magnet_links.get('subtitle', '').strip():
#         torrent_types.append('subtitle')
#     if magnet_links.get('no_subtitle', '').strip():
#         torrent_types.append('no_subtitle')
#     return sorted(list(set(torrent_types)))
#
#
# def determine_torrent_type(magnet_links):
#     """Legacy function - use determine_torrent_types instead"""
#     types = determine_torrent_types(magnet_links)
#     return types[0] if types else 'no_subtitle'
#
#
# def get_missing_torrent_types(history_torrent_types, current_torrent_types):
#     """Get missing torrent types that should be searched for"""
#     ...  # ~30 lines omitted — see git history
#
#
# def has_complete_subtitles(href, history_data):
#     """Check if a movie already has both subtitle and hacked_subtitle in history."""
#     if not history_data or href not in history_data:
#         return False
#     torrent_types = history_data[href].get('torrent_types', [])
#     return 'subtitle' in torrent_types and 'hacked_subtitle' in torrent_types
#
#
# def should_process_movie(href, history_data, phase, magnet_links):
#     """Determine if a movie should be processed based on history and phase rules"""
#     ...  # ~35 lines omitted — see git history
#
#
# def check_torrent_in_history(history_file, href, torrent_type):
#     """Check if the specified torrent is already in the history record"""
#     ...  # ~30 lines omitted — see git history
#
#
# def add_downloaded_indicator_to_csv(csv_file, history_file):
#     """Add downloaded indicators to torrents in CSV file"""
#     ...  # ~45 lines omitted — see git history
#
#
# def is_downloaded_torrent(torrent_content):
#     """Check if torrent content contains downloaded indicator"""
#     return torrent_content.strip().startswith("[DOWNLOADED PREVIOUSLY]")
#
#
# def mark_torrent_as_downloaded(history_file, href, video_code, torrent_type):
#     """Mark a specific torrent type as downloaded in history"""
#     ...  # ~15 lines omitted — see git history
