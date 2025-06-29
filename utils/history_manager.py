import csv
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

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
                    # If we already have a record for this href, keep the most recent one
                    existing_date = href_records[href]['parsed_date']
                    current_date = row['parsed_date']
                    if current_date > existing_date:
                        href_records[href] = row
            
            # Now process the deduplicated records
            for href, row in href_records.items():
                # Parse torrent types from comma-separated string
                torrent_types_str = row.get('torrent_type', 'no_subtitle')
                torrent_types = [t.strip() for t in torrent_types_str.split(',') if t.strip()]
                
                if phase is None:
                    # Load all records (for general checking)
                    history[href] = {
                        'phase': row['phase'],
                        'video_title': row['video_title'],
                        'parsed_date': row['parsed_date'],
                        'torrent_types': torrent_types
                    }
                elif phase == 1:
                    # For phase 1, ignore records that were processed in phase 2
                    if row['phase'] != '2':
                        history[href] = {
                            'phase': row['phase'],
                            'video_title': row['video_title'],
                            'parsed_date': row['parsed_date'],
                            'torrent_types': torrent_types
                        }
                elif phase == 2:
                    # For phase 2, load all history records (same as phase 1)
                    history[href] = {
                        'phase': row['phase'],
                        'video_title': row['video_title'],
                        'parsed_date': row['parsed_date'],
                        'torrent_types': torrent_types
                    }
            
            # If we found duplicates, clean up the file
            if len(records) != len(href_records):
                logger.info(f"Found {len(records) - len(href_records)} duplicate records, cleaning up history file")
                cleanup_history_file(history_file, href_records)
            
            logger.info(f"Loaded {len(history)} previously parsed movies from history for phase {phase if phase else 'all'}")
        except Exception as e:
            logger.error(f"Error loading parsed movies history: {e}")
    else:
        logger.info("No parsed movies history found, starting fresh")
    
    return history

def cleanup_history_file(history_file, href_records):
    """Clean up history file by removing duplicate records and keeping only the most recent for each href"""
    try:
        # Sort records by parsed_date (most recent first)
        sorted_records = sorted(href_records.values(), key=lambda x: x['parsed_date'], reverse=True)
        
        # Write cleaned records back to file
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_title', 'parsed_date', 'torrent_type']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in sorted_records:
                writer.writerow(record)
        
        logger.info(f"Cleaned up history file: removed duplicates, kept {len(sorted_records)} unique records")
        
    except Exception as e:
        logger.error(f"Error cleaning up history file: {e}")

def maintain_history_limit(history_file, max_records=1000):
    """Maintain maximum records in history file by removing oldest entries"""
    
    if not os.path.exists(history_file):
        return
    
    try:
        # Read all records
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)
        
        # If we have more than max_records, remove oldest
        if len(records) > max_records:
            # Sort by parsed_date to find oldest records
            records.sort(key=lambda x: x['parsed_date'])
            
            # Keep only the newest max_records
            records = records[-max_records:]
            
            # Rewrite the file with remaining records
            with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
                fieldnames = ['href', 'phase', 'video_title', 'parsed_date', 'torrent_type']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for record in records:
                    writer.writerow(record)
            
            logger.info(f"Maintained history limit: kept {len(records)} newest records, removed oldest entries")
    
    except Exception as e:
        logger.error(f"Error maintaining history limit: {e}")

def save_parsed_movie_to_history(history_file, href, phase, video_title, torrent_types=None):
    """Save a parsed movie to the history CSV file, updating existing records"""
    
    if torrent_types is None:
        torrent_types = ['no_subtitle']
    elif isinstance(torrent_types, str):
        torrent_types = [torrent_types]
    
    # Convert list to comma-separated string for storage
    torrent_types_str = ','.join(sorted(set(torrent_types)))  # Remove duplicates and sort
    
    # Read existing records
    records = []
    file_exists = os.path.exists(history_file)
    existing_count = 0
    
    if file_exists:
        try:
            with open(history_file, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['href'] == href:
                        existing_count += 1
                        # Skip this record - we'll replace it with the new one
                        continue
                    records.append(row)
            
            if existing_count > 1:
                logger.warning(f"Found {existing_count} existing records for {href}, removing all and adding new one")
            elif existing_count == 1:
                logger.debug(f"Replacing existing record for {href}")
            else:
                logger.debug(f"Adding new record for {href}")
                
        except Exception as e:
            logger.error(f"Error reading existing history: {e}")
            records = []
    
    # Add new record at the beginning (most recent)
    new_record = {
        'href': href,
        'phase': phase,
        'video_title': video_title,
        'parsed_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'torrent_type': torrent_types_str
    }
    records.insert(0, new_record)
    
    # Write all records back to file
    try:
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_title', 'parsed_date', 'torrent_type']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        
        # Maintain history limit after adding new record
        maintain_history_limit(history_file)
        
        logger.debug(f"Updated history for {href} with torrent types: {torrent_types_str} (total records: {len(records)})")
        
    except Exception as e:
        logger.error(f"Error saving to parsed movies history: {e}")

def validate_history_file(history_file):
    """Validate history file integrity and check for duplicates"""
    if not os.path.exists(history_file):
        logger.info("History file does not exist")
        return True
    
    try:
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)
        
        # Check for duplicates
        href_count = {}
        for record in records:
            href = record['href']
            href_count[href] = href_count.get(href, 0) + 1
        
        duplicates = {href: count for href, count in href_count.items() if count > 1}
        
        if duplicates:
            logger.warning(f"Found {len(duplicates)} hrefs with duplicate records:")
            for href, count in duplicates.items():
                logger.warning(f"  {href}: {count} records")
            return False
        else:
            logger.debug(f"History file validation passed: {len(records)} unique records")
            return True
            
    except Exception as e:
        logger.error(f"Error validating history file: {e}")
        return False

def determine_torrent_types(magnet_links):
    """Determine all available torrent types based on available magnet links"""
    has_subtitle = bool(magnet_links.get('subtitle', '').strip())
    has_hacked_subtitle = bool(magnet_links.get('hacked_subtitle', '').strip())
    has_hacked_no_subtitle = bool(magnet_links.get('hacked_no_subtitle', '').strip())
    has_no_subtitle = bool(magnet_links.get('no_subtitle', '').strip())
    
    torrent_types = []
    
    # Add all available torrent types (not just one)
    if has_hacked_subtitle:
        torrent_types.append('hacked_subtitle')
    if has_hacked_no_subtitle:
        torrent_types.append('hacked_no_subtitle')
    if has_subtitle:
        torrent_types.append('subtitle')
    if has_no_subtitle:
        torrent_types.append('no_subtitle')
    
    # If no torrents found, default to no_subtitle
    if not torrent_types:
        torrent_types.append('no_subtitle')
    
    return torrent_types

def determine_torrent_type(magnet_links):
    """Determine the primary torrent type based on available magnet links (for backward compatibility)"""
    types = determine_torrent_types(magnet_links)
    return types[0] if types else 'no_subtitle'

def get_missing_torrent_types(history_torrent_types, current_torrent_types):
    """Return the preferred types (hacked_subtitle, subtitle) that are missing, but if either is present, only look for the other one."""
    all_types = set(history_torrent_types) | set(current_torrent_types)
    has_hacked_subtitle = 'hacked_subtitle' in all_types
    has_subtitle = 'subtitle' in all_types
    if has_hacked_subtitle and has_subtitle:
        return []
    elif has_hacked_subtitle:
        return ['subtitle']
    elif has_subtitle:
        return ['hacked_subtitle']
    else:
        return ['hacked_subtitle', 'subtitle']

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