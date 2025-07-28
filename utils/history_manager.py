import csv
import os
import logging
from datetime import datetime

# Import configuration
try:
    from config import LOG_LEVEL
except ImportError:
    # Fallback value if config.py doesn't exist
    LOG_LEVEL = 'INFO'

from utils.logging_config import get_logger, setup_logging
setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)


def load_parsed_movies_history(history_file, phase=None):
    """Load previously parsed movies from CSV file with phase filtering"""
    history = {}  # Changed from set to dict to store full data

    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                records = list(reader)

            # Handle potential duplicates by keeping only the most recent record for each href
            href_records = {}
            for row in records:
                href = row['href']
                if href not in href_records:
                    href_records[href] = row
                else:
                    # If we already have a record for this href, keep the one with the most recent update_date
                    existing_date = href_records[href].get('update_date', href_records[href].get('parsed_date', ''))
                    current_date = row.get('update_date', row.get('parsed_date', ''))
                    if current_date > existing_date:
                        href_records[href] = row

            # Now process the deduplicated records
            for href, row in href_records.items():
                # Handle backward compatibility for old format (parsed_date)
                create_date = row.get('create_date', row.get('parsed_date', ''))
                update_date = row.get('update_date', row.get('parsed_date', ''))

                # Parse torrent types from new format (individual columns) or old format (comma-separated string)
                torrent_types = []
                if 'torrent_type' in row:
                    # Old format: comma-separated string
                    torrent_types_str = row.get('torrent_type', 'no_subtitle')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                else:
                    # New format: individual columns - check if magnet links exist (with or without date format)
                    torrent_categories = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
                    for category in torrent_categories:
                        magnet_content = row.get(category, '').strip()
                        if magnet_content:
                            # Check if it's in [YYYY-MM-DD]magnet_link format or just magnet link
                            if magnet_content.startswith('[') and ']' in magnet_content:
                                # Extract magnet link from [YYYY-MM-DD]magnet_link format
                                magnet_link = magnet_content.split(']', 1)[1]
                                if magnet_link.startswith('magnet:'):
                                    torrent_types.append(category)
                            elif magnet_content.startswith('magnet:'):
                                # Direct magnet link format
                                torrent_types.append(category)

                if phase is None:
                    # Load all records (for general checking)
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
                    # For phase 1, ignore records that were processed in phase 2
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
                    # For phase 2, load all history records (same as phase 1)
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

            # If we found duplicates, clean up the file
            if len(records) != len(href_records):
                logger.info(f"Found {len(records) - len(href_records)} duplicate records, cleaning up history file")
                cleanup_history_file(history_file, href_records)

            # Count records by phase for detailed logging
            phase_counts = {}
            for record in history.values():
                record_phase = record['phase']
                phase_counts[record_phase] = phase_counts.get(record_phase, 0) + 1

            if phase is None:
                # Log detailed breakdown for all phases
                phase_details = ", ".join([f"phase {p}: {c}" for p, c in sorted(phase_counts.items())])
                logger.info(f"Loaded {len(history)} previously parsed movies from history ({phase_details})")
            else:
                # Log specific phase info
                phase_details = ", ".join([f"phase {p}: {c}" for p, c in sorted(phase_counts.items())])
                logger.info(f"Loaded {len(history)} previously parsed movies from history for phase {phase} ({phase_details})")
        except Exception as e:
            logger.error(f"Error loading parsed movies history: {e}")
    else:
        logger.info("No parsed movies history found, starting fresh")

    return history


def cleanup_history_file(history_file, href_records):
    """Clean up history file by removing duplicate records and keeping only the most recent for each href"""
    try:
        # Sort records by update_date (most recent first), with backward compatibility
        def get_update_date(record):
            return record.get('update_date', record.get('parsed_date', ''))
        
        sorted_records = sorted(href_records.values(), key=lambda x: get_update_date(x), reverse=True)

        # Write cleaned records back to file with new format
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in sorted_records:
                # Handle backward compatibility for old format
                if 'create_date' not in record and 'parsed_date' in record:
                    record['create_date'] = record['parsed_date']
                if 'update_date' not in record and 'parsed_date' in record:
                    record['update_date'] = record['parsed_date']
                
                # Convert old torrent_type format to new individual columns
                if 'torrent_type' in record and 'hacked_subtitle' not in record:
                    torrent_types_str = record.get('torrent_type', '')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                    
                    # Set magnet links for existing torrent types (empty for old format)
                    for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                        if category in torrent_types:
                            record[category] = ''  # Empty magnet link for old format
                        else:
                            record[category] = ''
                
                # Remove old torrent_type column
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
        # Read all records
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)

        # If we have more than max_records, remove oldest
        if len(records) > max_records:
            # Sort by update_date to find oldest records, with backward compatibility
            def get_update_date(record):
                return record.get('update_date', record.get('parsed_date', ''))
            
            records.sort(key=lambda x: get_update_date(x))

            # Keep only the newest max_records
            records = records[-max_records:]

            # Rewrite the file with remaining records
            with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
                fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                             'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for record in records:
                    # Handle backward compatibility for old format
                    if 'create_date' not in record and 'parsed_date' in record:
                        record['create_date'] = record['parsed_date']
                    if 'update_date' not in record and 'parsed_date' in record:
                        record['update_date'] = record['parsed_date']
                    
                    # Convert old torrent_type format to new individual columns
                    if 'torrent_type' in record and 'hacked_subtitle' not in record:
                        torrent_types_str = record.get('torrent_type', '')
                        torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                        
                        # Set magnet links for existing torrent types (empty for old format)
                        for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                            if category in torrent_types:
                                record[category] = ''  # Empty magnet link for old format
                            else:
                                record[category] = ''
                    
                    # Remove old torrent_type column
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
        # Convert list of torrent types to magnet_links dict
        magnet_links_dict = {}
        for torrent_type in magnet_links:
            magnet_links_dict[torrent_type] = ''
        magnet_links = magnet_links_dict

    # Read existing records
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
                        
                        # Handle both old and new formats
                        if 'torrent_type' in row:
                            # Old format: update torrent_type column
                            existing_torrent_types = row.get('torrent_type', '').split(',')
                            existing_torrent_types = [t.strip() for t in existing_torrent_types if t.strip()]
                            
                            # Merge existing and new torrent types
                            all_torrent_types = list(set(existing_torrent_types + list(magnet_links.keys())))
                            all_torrent_types.sort()
                            
                            # Update the record
                            row['torrent_type'] = ','.join(all_torrent_types)
                            row['update_date'] = current_time
                            row['phase'] = phase
                        else:
                            # New format: update individual columns with magnet links
                            for torrent_type, magnet_link in magnet_links.items():
                                if torrent_type in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                                    # Store magnet link with date format: [YYYY-MM-DD]magnet_link
                                    if magnet_link:
                                        row[torrent_type] = f"[{current_date}]{magnet_link}"
                                    else:
                                        row[torrent_type] = ''
                            
                            row['update_date'] = current_time
                            row['phase'] = phase
                        
                        # Store the updated record separately to move it to first position
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

    # Add new record if it doesn't exist
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
        
        # Set magnet links for the torrent types with date format
        for torrent_type, magnet_link in magnet_links.items():
            if torrent_type in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                if magnet_link:
                    new_record[torrent_type] = f"[{current_date}]{magnet_link}"
                else:
                    new_record[torrent_type] = ''
        
        records.insert(0, new_record)
        logger.debug(f"Added new record for {href} with magnet links: {list(magnet_links.keys())}")
    else:
        # Move updated record to first position
        if updated_record:
            records.insert(0, updated_record)

    # Write all records back to file
    try:
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                # Handle old format conversion
                if 'torrent_type' in record:
                    torrent_types_str = record.get('torrent_type', '')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                    
                    # Set magnet links for existing torrent types (empty for old format)
                    for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                        if category in torrent_types:
                            record[category] = ''  # Empty magnet link for old format
                        else:
                            record[category] = ''
                    
                    # Remove old torrent_type column
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

        # Check if file needs conversion from old format
        needs_conversion = False
        for record in records:
            if 'torrent_type' in record and 'hacked_subtitle' not in record:
                needs_conversion = True
                break

        if needs_conversion:
            logger.info("Converting history file from old format to new format")
            
            # Convert records to new format
            converted_records = []
            for record in records:
                # Handle backward compatibility for old format
                if 'create_date' not in record and 'parsed_date' in record:
                    record['create_date'] = record['parsed_date']
                if 'update_date' not in record and 'parsed_date' in record:
                    record['update_date'] = record['parsed_date']
                
                # Convert old torrent_type format to new individual columns
                if 'torrent_type' in record:
                    torrent_types_str = record.get('torrent_type', '')
                    torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                    
                    # Set magnet links for existing torrent types (empty for old format)
                    for category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                        if category in torrent_types:
                            record[category] = ''  # Empty magnet link for old format
                        else:
                            record[category] = ''
                    
                    # Remove old torrent_type column
                    del record['torrent_type']
                
                converted_records.append(record)

            # Write converted records back to file
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
    
    # Check each torrent category in the magnet_links dictionary
    if magnet_links.get('hacked_subtitle', '').strip():
        torrent_types.append('hacked_subtitle')
    
    if magnet_links.get('hacked_no_subtitle', '').strip():
        torrent_types.append('hacked_no_subtitle')
    
    if magnet_links.get('subtitle', '').strip():
        torrent_types.append('subtitle')
    
    if magnet_links.get('no_subtitle', '').strip():
        torrent_types.append('no_subtitle')
    
    # Remove duplicates and sort
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

    # Check for missing hacked category (prefer hacked_subtitle over hacked_no_subtitle)
    if has_hacked_subtitle_current and not has_hacked_subtitle_in_history:
        # Current has hacked_subtitle but history doesn't - add it
        missing_types.append('hacked_subtitle')
    elif has_hacked_no_subtitle_current and not has_hacked_no_subtitle_in_history and not has_hacked_subtitle_in_history:
        # Current has hacked_no_subtitle but history doesn't have any hacked version
        missing_types.append('hacked_no_subtitle')

    # Check for missing subtitle category (prefer subtitle over no_subtitle)
    if has_subtitle_current and not has_subtitle_in_history:
        # Current has subtitle but history doesn't - add it
        missing_types.append('subtitle')
    elif has_no_subtitle_current and not has_no_subtitle_in_history and not has_subtitle_in_history:
        # Current has no_subtitle but history doesn't have any subtitle version
        missing_types.append('no_subtitle')

    return missing_types


def should_process_movie(href, history_data, phase, magnet_links):
    """Determine if a movie should be processed based on history and phase rules"""
    if href not in history_data:
        logger.debug(f"New movie {href}: should process")
        return True, None  # New movie, should process

    current_torrent_types = determine_torrent_types(magnet_links)
    history_torrent_types = history_data[href].get('torrent_types', ['no_subtitle'])

    logger.debug(f"Movie {href}: current={current_torrent_types}, history={history_torrent_types}, phase={phase}")

    # Get missing torrent types that should be searched for
    missing_types = get_missing_torrent_types(history_torrent_types, current_torrent_types)

    if phase == 1:
        # Phase 1: Process if we can find missing torrent types
        if missing_types:
            logger.debug(f"Phase 1: missing types {missing_types} -> should process")
            return True, history_torrent_types
        else:
            logger.debug(f"Phase 1: no missing types -> should not process")
            return False, history_torrent_types

    elif phase == 2:
        # Phase 2: Only process if we can upgrade from no_subtitle to hacked_no_subtitle
        # or if we can find any missing torrent types
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
        bool: Returns True if torrent is in history and contains the specified type, False otherwise
    """
    if not os.path.exists(history_file):
        return False
    
    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['href'] == href:
                    # Check if torrent_type field contains the specified type (old format)
                    if 'torrent_type' in row:
                        recorded_types = row.get('torrent_type', '').split(',')
                        recorded_types = [t.strip() for t in recorded_types if t.strip()]
                        return torrent_type in recorded_types
                    else:
                        # New format: check individual column for magnet link (with or without date format)
                        magnet_content = row.get(torrent_type, '').strip()
                        if magnet_content:
                            # Check if it's in [YYYY-MM-DD]magnet_link format or just magnet link
                            if magnet_content.startswith('[') and ']' in magnet_content:
                                # Extract magnet link from [YYYY-MM-DD]magnet_link format
                                magnet_link = magnet_content.split(']', 1)[1]
                                return magnet_link.startswith('magnet:')
                            elif magnet_content.startswith('magnet:'):
                                # Direct magnet link format
                                return True
                        return False
        return False
    except Exception as e:
        logger.error(f"Error checking torrent in history: {e}")
        return False


def add_downloaded_indicator_to_csv(csv_file, history_file):
    """
    Add downloaded indicators to torrents in CSV file
    For already downloaded torrents, only keep [DOWNLOADED PREVIOUSLY] in the column (remove magnet link)
    Args:
        csv_file: Daily report CSV file path
        history_file: History file path
    Returns:
        bool: Whether the operation was successful
    """
    if not os.path.exists(csv_file):
        logger.error(f"CSV file not found: {csv_file}")
        return False
    
    try:
        # Read CSV file
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        # Check and add indicators
        modified = False
        for row in rows:
            href = row['href']
            
            # Check each torrent type column
            torrent_columns = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            
            for column in torrent_columns:
                if row.get(column) and row[column].strip():
                    # Check if this torrent is already in history
                    if check_torrent_in_history(history_file, href, column):
                        # Only keep DOWNLOADED PREVIOUSLY in the column
                        if row[column].strip() != '[DOWNLOADED PREVIOUSLY]':
                            row[column] = '[DOWNLOADED PREVIOUSLY]'
                            modified = True
                            logger.debug(f"Set downloaded indicator only for {href} - {column}")
        
        # If file was modified, write back to file
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
    """
    Check if torrent content contains downloaded indicator
    
    Args:
        torrent_content: Torrent content string
    
    Returns:
        bool: Returns True if contains downloaded indicator, False otherwise
    """
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
        # Use the existing save function to update history
        # This will add the torrent type to the history record with empty magnet link
        save_parsed_movie_to_history(
            history_file, 
            href, 
            "2",  # Phase 2 for qBittorrent uploads
            video_code, 
            {torrent_type: ''}  # Empty magnet link for downloaded torrents
        )
        
        logger.debug(f"Marked {torrent_type} as downloaded for {video_code} ({href})")
        return True
        
    except Exception as e:
        logger.error(f"Error marking torrent as downloaded: {e}")
        return False 
