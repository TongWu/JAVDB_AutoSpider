#!/usr/bin/env python3
"""
Adhoc script to update existing history movie entries to new format
Updates existing date/time data columns with magnet links in [YYYY-MM-DD]magnet_link format
"""

import os
import sys
import csv
import time
import logging
import argparse
from datetime import datetime
from urllib.parse import urljoin

# Import existing modules
from utils.history_manager import (
    load_parsed_movies_history, 
    save_parsed_movie_to_history,
    validate_history_file
)
from utils.parser import parse_detail
from utils.magnet_extractor import extract_magnets
from utils.logging_config import setup_logging, get_logger

# Import configuration
try:
    from config import (
        BASE_URL, PARSED_MOVIES_CSV, SPIDER_LOG_FILE, LOG_LEVEL,
        DETAIL_PAGE_SLEEP, JAVDB_SESSION_COOKIE
    )
except ImportError:
    # Fallback values if config.py doesn't exist
    BASE_URL = 'https://javdb.com'
    PARSED_MOVIES_CSV = 'parsed_movies_history.csv'
    SPIDER_LOG_FILE = 'logs/Javdb_Spider.log'
    LOG_LEVEL = 'INFO'
    DETAIL_PAGE_SLEEP = 5
    JAVDB_SESSION_COOKIE = None

os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))

# Configure logging
setup_logging(SPIDER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Import requests after logging setup
import requests
from bs4 import BeautifulSoup


def get_page(url, session=None, use_cookie=False):
    """Fetch a webpage with proper headers and age verification bypass"""
    if session is None:
        session = requests.Session()
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    if use_cookie and JAVDB_SESSION_COOKIE:
        session.cookies.set('over18', '1', domain='javdb.com')
        session.cookies.set('theme', 'auto', domain='javdb.com')
        if JAVDB_SESSION_COOKIE:
            session.cookies.set('session', JAVDB_SESSION_COOKIE, domain='javdb.com')
    
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Error fetching {url}: {e}")
        return None


def extract_magnet_links_from_detail(detail_html, video_code):
    """Extract magnet links from detail page HTML"""
    try:
        # Parse detail page
        magnets, actor_info, parsed_video_code = parse_detail(detail_html, video_code)
        
        if not magnets:
            logger.warning(f"No magnets found for {video_code}")
            return {}
        
        # Extract magnet links using existing function
        magnet_links = extract_magnets(magnets)
        
        # Filter out empty magnet links and size information
        filtered_links = {}
        for category, magnet_link in magnet_links.items():
            # Only include actual magnet link categories, not size categories
            if category in ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']:
                if magnet_link and magnet_link.strip():
                    filtered_links[category] = magnet_link
        
        return filtered_links
        
    except Exception as e:
        logger.error(f"Error extracting magnet links for {video_code}: {e}")
        return {}


def get_existing_torrent_types(record):
    """Get existing torrent types from history record"""
    torrent_types = []
    categories = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
    
    for category in categories:
        content = record.get(category, '').strip()
        if content:
            # Check if it's in old format (date) or new format (magnet link)
            if content.startswith('[') and ']' in content:
                # New format: [YYYY-MM-DD]magnet_link
                magnet_link = content.split(']', 1)[1]
                if magnet_link.startswith('magnet:'):
                    torrent_types.append(category)
            elif content.startswith('magnet:'):
                # Direct magnet link format
                torrent_types.append(category)
            elif len(content) > 10:  # Likely a date format
                # Old format: date string
                torrent_types.append(category)
    
    return torrent_types


def update_history_entry(href, record, session, history_file_path):
    """Update a single history entry with magnet links"""
    video_code = record['video_code']
    phase = record['phase']
    
    logger.info(f"Processing {video_code} ({href}) - Phase {phase}")
    
    # Get existing torrent types that need updating
    existing_types = get_existing_torrent_types(record)
    
    if not existing_types:
        logger.info(f"No existing torrent types found for {video_code}, skipping")
        return False
    
    logger.info(f"Found existing torrent types for {video_code}: {existing_types}")
    
    # Construct detail page URL
    detail_url = urljoin(BASE_URL, href)
    
    # Fetch detail page
    logger.info(f"Fetching detail page: {detail_url}")
    detail_html = get_page(detail_url, session, use_cookie=True)
    
    if not detail_html:
        logger.error(f"Failed to fetch detail page for {video_code}")
        return False
    
    # Extract magnet links
    magnet_links = extract_magnet_links_from_detail(detail_html, video_code)
    
    if not magnet_links:
        logger.warning(f"No magnet links found for {video_code}")
        return False
    
    logger.info(f"Found magnet links for {video_code}: {list(magnet_links.keys())}")
    
    # Filter magnet links to only include existing torrent types
    filtered_magnet_links = {}
    for torrent_type in existing_types:
        if torrent_type in magnet_links:
            filtered_magnet_links[torrent_type] = magnet_links[torrent_type]
    
    if not filtered_magnet_links:
        logger.warning(f"No matching magnet links found for existing types of {video_code}")
        return False
    
    logger.info(f"Updating {video_code} with magnet links: {list(filtered_magnet_links.keys())}")
    
    # Update history with new magnet links
    try:
        save_parsed_movie_to_history(
            history_file_path,
            href,
            phase,
            video_code,
            filtered_magnet_links
        )
        logger.info(f"Successfully updated {video_code} with magnet links")
        return True
    except Exception as e:
        logger.error(f"Error updating history for {video_code}: {e}")
        return False


def main():
    """Main function to update history format"""
    parser = argparse.ArgumentParser(description='Update existing history entries to new magnet link format')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be updated without making changes')
    parser.add_argument('--limit', type=int, default=0,
                        help='Limit number of entries to process (0 = all)')
    parser.add_argument('--start-from', type=int, default=0,
                        help='Start processing from this index')
    
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("HISTORY FORMAT UPDATE SCRIPT")
    logger.info("=" * 60)
    
    # Validate history file
    history_file_path = PARSED_MOVIES_CSV
    if not os.path.exists(history_file_path):
        # Try alternative locations
        alternative_paths = [
            'Daily Report/parsed_movies_history.csv',
            'Ad Hoc/parsed_movies_history.csv',
            'parsed_movies_history.csv'
        ]
        for alt_path in alternative_paths:
            if os.path.exists(alt_path):
                history_file_path = alt_path
                logger.info(f"Found history file at: {history_file_path}")
                break
        else:
            logger.error(f"History file not found. Tried: {PARSED_MOVIES_CSV} and alternatives")
            return False
    
    logger.info(f"Validating history file: {history_file_path}")
    if not validate_history_file(history_file_path):
        logger.error("History file validation failed")
        return False
    
    # Load existing history
    logger.info("Loading existing history...")
    history = load_parsed_movies_history(history_file_path)
    
    if not history:
        logger.warning("No history entries found")
        return True
    
    logger.info(f"Loaded {len(history)} history entries")
    
    # Filter entries that need updating (have old date format)
    entries_to_update = []
    for href, record in history.items():
        existing_types = get_existing_torrent_types(record)
        if existing_types:
            # Check if any of the existing types have old date format
            needs_update = False
            for torrent_type in existing_types:
                content = record.get(torrent_type, '').strip()
                if content and len(content) > 10 and not content.startswith('magnet:'):
                    # Likely old date format
                    needs_update = True
                    break
            
            if needs_update:
                entries_to_update.append((href, record))
    
    if not entries_to_update:
        logger.info("No entries need updating (all entries already in new format)")
        return True
    
    logger.info(f"Found {len(entries_to_update)} entries that need updating")
    
    # Apply limits
    if args.limit > 0:
        entries_to_update = entries_to_update[:args.limit]
        logger.info(f"Limited to {len(entries_to_update)} entries")
    
    if args.start_from > 0:
        entries_to_update = entries_to_update[args.start_from:]
        logger.info(f"Starting from index {args.start_from}")
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")
        for i, (href, record) in enumerate(entries_to_update):
            existing_types = get_existing_torrent_types(record)
            logger.info(f"[{i+1}/{len(entries_to_update)}] Would update {record['video_code']} - {existing_types}")
        return True
    
    # Create session for requests
    session = requests.Session()
    
    # Process entries
    success_count = 0
    error_count = 0
    
    for i, (href, record) in enumerate(entries_to_update):
        logger.info(f"\n[{i+1}/{len(entries_to_update)}] Processing entry {i+1}")
        
        try:
            if update_history_entry(href, record, session, history_file_path):
                success_count += 1
            else:
                error_count += 1
            
            # Sleep between requests to be respectful to the server
            if i < len(entries_to_update) - 1:  # Don't sleep after last request
                logger.info(f"Sleeping {DETAIL_PAGE_SLEEP} seconds...")
                time.sleep(DETAIL_PAGE_SLEEP)
                
        except KeyboardInterrupt:
            logger.info("\nInterrupted by user")
            break
        except Exception as e:
            logger.error(f"Unexpected error processing {record['video_code']}: {e}")
            error_count += 1
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("UPDATE SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total entries processed: {len(entries_to_update)}")
    logger.info(f"Successfully updated: {success_count}")
    logger.info(f"Errors: {error_count}")
    logger.info(f"Success rate: {(success_count/len(entries_to_update)*100):.1f}%")
    
    if success_count > 0:
        logger.info("History format update completed successfully!")
        return True
    else:
        logger.error("No entries were successfully updated")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1) 