"""
History Manager for JavDB Spider

Prefers the Rust implementation (``javdb_rust_core``) when available,
falling back to the pure-Python implementation otherwise.
"""

import csv
import os
import logging
from datetime import datetime

try:
    from config import LOG_LEVEL
except ImportError:
    LOG_LEVEL = 'INFO'

from utils.logging_config import get_logger, setup_logging
setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)

RUST_HISTORY_AVAILABLE = False


def load_parsed_movies_history(history_file, phase=None):
    """Load previously parsed movies from CSV file with phase filtering"""
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
                    existing_date = href_records[href].get('update_date', href_records[href].get('parsed_date', ''))
                    current_date = row.get('update_date', row.get('parsed_date', ''))
                    if current_date > existing_date:
                        href_records[href] = row

            for href, row in href_records.items():
                create_date = row.get('create_date', row.get('parsed_date', ''))
                update_date = row.get('update_date', row.get('parsed_date', ''))

                torrent_types = []
                if 'torrent_type' in row:
                    torrent_types_str = row.get('torrent_type', 'no_subtitle')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                else:
                    torrent_categories = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
                    for category in torrent_categories:
                        magnet_content = row.get(category, '').strip()
                        if magnet_content:
                            if magnet_content.startswith('[') and ']' in magnet_content:
                                magnet_link = magnet_content.split(']', 1)[1]
                                if magnet_link.startswith('magnet:'):
                                    torrent_types.append(category)
                            elif magnet_content.startswith('magnet:'):
                                torrent_types.append(category)

                if phase is None:
                    history[href] = {
                        'phase': row['phase'],
                        'video_code': row['video_code'],
                        'create_date': create_date,
                        'update_date': update_date,
                        'torrent_types': torrent_types,
                        'hacked_subtitle': row.get('hacked_subtitle', ''),
                        'hacked_no_subtitle': row.get('hacked_no_subtitle', ''),
                        'subtitle': row.get('subtitle', ''),
                        'no_subtitle': row.get('no_subtitle', '')
                    }
                elif phase == 1:
                    if row['phase'] != '2':
                        history[href] = {
                            'phase': row['phase'],
                            'video_code': row['video_code'],
                            'create_date': create_date,
                            'update_date': update_date,
                            'torrent_types': torrent_types,
                            'hacked_subtitle': row.get('hacked_subtitle', ''),
                            'hacked_no_subtitle': row.get('hacked_no_subtitle', ''),
                            'subtitle': row.get('subtitle', ''),
                            'no_subtitle': row.get('no_subtitle', '')
                        }
                elif phase == 2:
                    history[href] = {
                        'phase': row['phase'],
                        'video_code': row['video_code'],
                        'create_date': create_date,
                        'update_date': update_date,
                        'torrent_types': torrent_types,
                        'hacked_subtitle': row.get('hacked_subtitle', ''),
                        'hacked_no_subtitle': row.get('hacked_no_subtitle', ''),
                        'subtitle': row.get('subtitle', ''),
                        'no_subtitle': row.get('no_subtitle', '')
                    }

            if len(records) != len(href_records):
                logger.info(f"Found {len(records) - len(href_records)} duplicate records, cleaning up history file")
                cleanup_history_file(history_file, href_records)

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


def cleanup_history_file(history_file, href_records):
    """Clean up history file by removing duplicate records and keeping only the most recent for each href"""
    try:
        def get_update_date(record):
            return record.get('update_date', record.get('parsed_date', ''))
        
        sorted_records = sorted(href_records.values(), key=lambda x: get_update_date(x), reverse=True)

        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in sorted_records:
                if 'create_date' not in record and 'parsed_date' in record:
                    record['create_date'] = record['parsed_date']
                if 'update_date' not in record and 'parsed_date' in record:
                    record['update_date'] = record['parsed_date']
                
                if 'torrent_type' in record and 'hacked_subtitle' not in record:
                    torrent_types_str = record.get('torrent_type', '')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                    
                    for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                        record[category] = ''
                
                if 'torrent_type' in record:
                    del record['torrent_type']
                
                writer.writerow(record)

        logger.info(f"Cleaned up history file: removed duplicates, kept {len(sorted_records)} unique records")

    except Exception as e:
        logger.error(f"Error cleaning up history file: {e}")


def maintain_history_limit(history_file, max_records=1000):
    """Maintain maximum records in history file by removing oldest entries
    NOTE: This function is currently DISABLED - no history size limit is enforced"""

    if not os.path.exists(history_file):
        return

    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)

        if len(records) > max_records:
            def get_update_date(record):
                return record.get('update_date', record.get('parsed_date', ''))
            
            records.sort(key=lambda x: get_update_date(x))
            records = records[-max_records:]

            with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
                fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                             'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for record in records:
                    if 'create_date' not in record and 'parsed_date' in record:
                        record['create_date'] = record['parsed_date']
                    if 'update_date' not in record and 'parsed_date' in record:
                        record['update_date'] = record['parsed_date']
                    
                    if 'torrent_type' in record and 'hacked_subtitle' not in record:
                        for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                            record[category] = ''
                    
                    if 'torrent_type' in record:
                        del record['torrent_type']
                    
                    writer.writerow(record)

            logger.info(f"Maintained history limit: kept {len(records)} newest records, removed oldest entries")

    except Exception as e:
        logger.error(f"Error maintaining history limit: {e}")


def save_parsed_movie_to_history(history_file, href, phase, video_code, magnet_links=None):
    """Save a parsed movie to the history CSV file, updating existing records with new magnet links"""

    if magnet_links is None:
        magnet_links = {'no_subtitle': ''}
    elif isinstance(magnet_links, list):
        magnet_links_dict = {}
        for torrent_type in magnet_links:
            magnet_links_dict[torrent_type] = ''
        magnet_links = magnet_links_dict

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
                            row['update_date'] = current_time
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
                                            except Exception:
                                                row[torrent_type] = f"[{current_date}]{magnet_link}"
                                        else:
                                            row[torrent_type] = f"[{current_date}]{magnet_link}"
                            
                            row['update_date'] = current_time
                            row['phase'] = phase
                        
                        if row.get('hacked_subtitle', '').strip():
                            row['hacked_no_subtitle'] = ''
                        if row.get('subtitle', '').strip():
                            row['no_subtitle'] = ''
                        
                        updated_record = row.copy()
                        logger.debug(f"Updated existing record for {href} with new magnet links: {list(magnet_links.keys())}")
                    else:
                        records.append(row)

            if existing_count > 1:
                logger.warning(f"Found {existing_count} existing records for {href}, keeping the updated one")
            elif existing_count == 1:
                logger.debug(f"Updated existing record for {href}")
            else:
                logger.debug(f"Adding new record for {href}")

        except Exception as e:
            logger.error(f"Error reading existing history: {e}")
            records = []

    if existing_count == 0:
        new_record = {
            'href': href,
            'phase': phase,
            'video_code': video_code,
            'create_date': current_time,
            'update_date': current_time,
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': '',
            'no_subtitle': ''
        }
        
        for torrent_type, magnet_link in magnet_links.items():
            if torrent_type in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                if magnet_link:
                    new_record[torrent_type] = f"[{current_date}]{magnet_link}"
                else:
                    new_record[torrent_type] = ''
        
        if new_record.get('hacked_subtitle', '').strip():
            new_record['hacked_no_subtitle'] = ''
        if new_record.get('subtitle', '').strip():
            new_record['no_subtitle'] = ''
        
        records.insert(0, new_record)
        logger.debug(f"Added new record for {href} with magnet links: {list(magnet_links.keys())}")
    else:
        if updated_record:
            records.insert(0, updated_record)

    try:
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                if 'torrent_type' in record:
                    torrent_types_str = record.get('torrent_type', '')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                    
                    for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                        if category in torrent_types:
                            record[category] = ''
                        else:
                            record[category] = ''
                    
                    del record['torrent_type']
                
                writer.writerow(record)

        logger.debug(f"Updated history for {href} with magnet links: {list(magnet_links.keys())} (total records: {len(records)})")
    except Exception as e:
        logger.error(f"Error writing to history file: {e}")


def validate_history_file(history_file):
    """Validate and fix history file format"""
    if not os.path.exists(history_file):
        return True

    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)

        needs_conversion = False
        for record in records:
            if 'torrent_type' in record and 'hacked_subtitle' not in record:
                needs_conversion = True
                break

        if needs_conversion:
            logger.info("Converting history file from old format to new format")
            
            converted_records = []
            for record in records:
                if 'create_date' not in record and 'parsed_date' in record:
                    record['create_date'] = record['parsed_date']
                if 'update_date' not in record and 'parsed_date' in record:
                    record['update_date'] = record['parsed_date']
                
                if 'torrent_type' in record:
                    torrent_types_str = record.get('torrent_type', '')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                    
                    for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                        if category in torrent_types:
                            record[category] = ''
                        else:
                            record[category] = ''
                    
                    del record['torrent_type']
                
                converted_records.append(record)

            with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
                fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                             'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for record in converted_records:
                    writer.writerow(record)

            logger.info("Successfully converted history file to new format")

        return True

    except Exception as e:
        logger.error(f"Error validating history file: {e}")
        return False


def determine_torrent_types(magnet_links):
    """Determine torrent types from magnet links dictionary"""
    torrent_types = []
    
    if magnet_links.get('hacked_subtitle', '').strip():
        torrent_types.append('hacked_subtitle')
    
    if magnet_links.get('hacked_no_subtitle', '').strip():
        torrent_types.append('hacked_no_subtitle')
    
    if magnet_links.get('subtitle', '').strip():
        torrent_types.append('subtitle')
    
    if magnet_links.get('no_subtitle', '').strip():
        torrent_types.append('no_subtitle')
    
    return sorted(list(set(torrent_types)))


def determine_torrent_type(magnet_links):
    """Legacy function - use determine_torrent_types instead"""
    types = determine_torrent_types(magnet_links)
    return types[0] if types else 'no_subtitle'


def get_missing_torrent_types(history_torrent_types, current_torrent_types):
    """Get missing torrent types that should be searched for"""
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
    """
    Check if a movie already has both subtitle and hacked_subtitle in history.
    
    This is used for early skip check before fetching detail page, to avoid
    unnecessary network requests for movies that already have all required torrents.
    """
    if not history_data or href not in history_data:
        return False
    
    torrent_types = history_data[href].get('torrent_types', [])
    has_subtitle = 'subtitle' in torrent_types
    has_hacked_subtitle = 'hacked_subtitle' in torrent_types
    
    return has_subtitle and has_hacked_subtitle


def should_process_movie(href, history_data, phase, magnet_links):
    """Determine if a movie should be processed based on history and phase rules"""
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


def check_torrent_in_history(history_file, href, torrent_type):
    """
    Check if the specified torrent is already in the history record
    
    Args:
        history_file: History file path
        href: Video link
        torrent_type: Torrent type (hacked_subtitle, hacked_no_subtitle, subtitle, no_subtitle)
    
    Returns:
        bool: True if torrent is in history and contains the specified type
    """
    if not os.path.exists(history_file):
        return False
    
    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['href'] == href:
                    if 'torrent_type' in row:
                        recorded_types = row.get('torrent_type', '').split(',')
                        recorded_types = [t.strip() for t in recorded_types if t.strip()]
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


def add_downloaded_indicator_to_csv(csv_file, history_file):
    """
    Add downloaded indicators to torrents in CSV file.
    For already downloaded torrents, only keep [DOWNLOADED PREVIOUSLY] in the column.
    """
    if not os.path.exists(csv_file):
        logger.error(f"CSV file not found: {csv_file}")
        return False
    
    try:
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        modified = False
        for row in rows:
            href = row['href']
            
            torrent_columns = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            
            for column in torrent_columns:
                if row.get(column) and row[column].strip():
                    if check_torrent_in_history(history_file, href, column):
                        if row[column].strip() != '[DOWNLOADED PREVIOUSLY]':
                            row[column] = '[DOWNLOADED PREVIOUSLY]'
                            modified = True
                            logger.debug(f"Set downloaded indicator only for {href} - {column}")
        
        if modified:
            with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
                fieldnames = reader.fieldnames or []
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


def is_downloaded_torrent(torrent_content):
    """Check if torrent content contains downloaded indicator"""
    return torrent_content.strip().startswith("[DOWNLOADED PREVIOUSLY]") 


def mark_torrent_as_downloaded(history_file, href, video_code, torrent_type):
    """
    Mark a specific torrent type as downloaded in history
    
    Args:
        history_file: History file path
        href: Video link
        video_code: Video code
        torrent_type: Torrent type to mark as downloaded
    
    Returns:
        bool: Whether the operation was successful
    """
    try:
        save_parsed_movie_to_history(
            history_file, 
            href, 
            "2",
            video_code, 
            {torrent_type: ''}
        )
        
        logger.debug(f"Marked {torrent_type} as downloaded for {video_code} ({href})")
        return True
        
    except Exception as e:
        logger.error(f"Error marking torrent as downloaded: {e}")
        return False


# ── Rust-first override ─────────────────────────────────────────────────
# When javdb_rust_core is available, replace pure-Python implementations
# with high-performance Rust equivalents. Falls back silently on ImportError.
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
    logger.debug("✅ Rust history manager loaded - using high-performance Rust implementation")
except ImportError as e:
    logger.warning(f"⚠️  Rust history manager not available (ImportError: {e}) - using pure-Python implementation")
