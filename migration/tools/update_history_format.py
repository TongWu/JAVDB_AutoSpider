#!/usr/bin/env python3
"""
Adhoc script to update existing history movie entries to new format
Updates existing date/time data columns with magnet links in [YYYY-MM-DD]magnet_link format
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import sys
import time

import requests
from datetime import datetime
from urllib.parse import urljoin

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(_project_root)
sys.path.insert(0, _project_root)

# Import existing modules
from utils.history_manager import (
    load_parsed_movies_history, 
    save_parsed_movie_to_history,
    validate_history_file
)
from utils.parser import parse_detail
from utils.domain.magnet_extractor import extract_magnets
from utils.infra.logging_config import setup_logging, get_logger
from utils.spider_gateway import create_gateway

# Import configuration
from utils.infra.config_helper import cfg

BASE_URL = cfg('BASE_URL', 'https://javdb.com')
PARSED_MOVIES_CSV = cfg('PARSED_MOVIES_CSV', 'parsed_movies_history.csv')
SPIDER_LOG_FILE = cfg('SPIDER_LOG_FILE', 'logs/spider.log')
LOG_LEVEL = cfg('LOG_LEVEL', 'INFO')
MOVIE_SLEEP_MIN = cfg('MOVIE_SLEEP_MIN', 5)
MOVIE_SLEEP_MAX = cfg('MOVIE_SLEEP_MAX', 15)

# Configure logging
setup_logging(SPIDER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

_gateway = create_gateway(use_proxy=True, use_cf_bypass=True, use_cookie=True)


def get_page(url, session=None, use_cookie=False):
    """Fetch a webpage via the unified gateway (proxy + CF bypass)."""
    return _gateway.fetch_html(url)


def extract_magnet_links_from_detail(detail_html, video_code):
    """Extract magnet links from detail page HTML"""
    try:
        # Parse detail page (ignore parse_success flag for migration script)
        # Note: video_code is passed for logging purposes only, not extracted from detail page
        magnets, actor_info, _ag, _al, _sup, _ = parse_detail(
            detail_html, video_code, skip_sleep=True)
        
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
                sleep_time = round(random.uniform(MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX), 1)
                logger.info(f"Sleeping {sleep_time} seconds...")
                time.sleep(sleep_time)
                
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