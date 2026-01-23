#!/usr/bin/env python3
"""
qBittorrent File Filter Script

This script filters out small files from recently added torrents in qBittorrent.
It sets the download priority to 0 (do not download) for files below the threshold.
Optionally, it can also delete local files that have already been downloaded.

Usage:
    python3 scripts/qb_file_filter.py --min-size 50  # Filter files smaller than 50MB
    python3 scripts/qb_file_filter.py --min-size 100 --days 2  # Filter files smaller than 100MB from last 2 days
    python3 scripts/qb_file_filter.py --min-size 50 --use-proxy  # With proxy
    python3 scripts/qb_file_filter.py --min-size 50 --delete-local-files  # Also delete downloaded files
"""

import requests
import logging
import argparse
import sys
import os
from datetime import datetime, timedelta

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Import unified configuration
try:
    from config import (
        QB_HOST, QB_PORT, QB_USERNAME, QB_PASSWORD,
        REQUEST_TIMEOUT, LOG_LEVEL,
        PROXY_HTTP, PROXY_HTTPS, PROXY_MODULES,
    )
except ImportError:
    # Fallback values if config.py doesn't exist
    QB_HOST = 'your_qbittorrent_ip'
    QB_PORT = 'your_qbittorrent_port'
    QB_USERNAME = 'your_qbittorrent_username'
    QB_PASSWORD = 'your_qbittorrent_password'
    REQUEST_TIMEOUT = 30
    LOG_LEVEL = 'INFO'
    PROXY_HTTP = None
    PROXY_HTTPS = None
    PROXY_MODULES = ['all']

# Import proxy pool configuration (with fallback)
try:
    from config import PROXY_MODE, PROXY_POOL, PROXY_POOL_COOLDOWN_SECONDS, PROXY_POOL_MAX_FAILURES
except ImportError:
    PROXY_MODE = 'single'
    PROXY_POOL = []
    PROXY_POOL_COOLDOWN_SECONDS = 691200
    PROXY_POOL_MAX_FAILURES = 3

# Import optional config for file filter
try:
    from config import QB_FILE_FILTER_MIN_SIZE_MB, QB_FILE_FILTER_LOG_FILE
except ImportError:
    QB_FILE_FILTER_MIN_SIZE_MB = 50  # Default 50MB threshold
    QB_FILE_FILTER_LOG_FILE = 'logs/qb_file_filter.log'

# Configure logging
from utils.logging_config import setup_logging, get_logger
setup_logging(QB_FILE_FILTER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Import masking utilities
from utils.masking import mask_ip_address, mask_username, mask_full

# Import proxy pool
from utils.proxy_pool import create_proxy_pool_from_config

# Import proxy helper from request handler
from utils.request_handler import create_proxy_helper_from_config

# Global proxy helper instance
global_proxy_helper = None

# qBittorrent configuration
QB_BASE_URL = f'http://{QB_HOST}:{QB_PORT}'


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Filter out small files from recently added torrents in qBittorrent'
    )
    parser.add_argument(
        '--min-size',
        type=float,
        default=QB_FILE_FILTER_MIN_SIZE_MB,
        help=f'Minimum file size in MB (files smaller than this will be skipped). Default: {QB_FILE_FILTER_MIN_SIZE_MB}MB'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=2,
        help='Number of days to look back for recently added torrents (default: 2 for today and yesterday)'
    )
    parser.add_argument(
        '--use-proxy',
        action='store_true',
        help='Enable proxy for qBittorrent API requests'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be filtered without actually making changes'
    )
    parser.add_argument(
        '--category',
        type=str,
        default=None,
        help='Filter only torrents in this category (default: all categories). Deprecated: use --categories instead.'
    )
    parser.add_argument(
        '--categories',
        type=str,
        default=None,
        help='JSON array of categories to filter (e.g., \'["Ad Hoc", "Daily Ingestion"]\'). If specified, overrides --category.'
    )
    parser.add_argument(
        '--delete-local-files',
        action='store_true',
        help='Delete local files that have already been downloaded but are below the size threshold'
    )
    return parser.parse_args()


def get_proxies_dict(module_name, use_proxy_flag):
    """
    Get proxies dictionary for requests if module should use proxy.
    
    Args:
        module_name: Name of the module
        use_proxy_flag: Whether --use-proxy flag is enabled
    
    Returns:
        dict or None: Proxies dictionary for requests, or None
    """
    if global_proxy_helper is None:
        logger.warning(f"[{module_name}] Proxy helper not initialized")
        return None
    
    return global_proxy_helper.get_proxies_dict(module_name, use_proxy_flag)


def initialize_proxy_helper(use_proxy):
    """Initialize global proxy pool and proxy helper."""
    global global_proxy_helper
    
    if not use_proxy:
        global_proxy_helper = create_proxy_helper_from_config(
            proxy_pool=None,
            proxy_modules=PROXY_MODULES,
            proxy_mode=PROXY_MODE,
            proxy_http=None,
            proxy_https=None
        )
        return
    
    # Check if we have PROXY_POOL configuration
    proxy_pool = None
    if PROXY_POOL and len(PROXY_POOL) > 0:
        if PROXY_MODE == 'pool':
            logger.info(f"Initializing proxy pool with {len(PROXY_POOL)} proxies...")
            proxy_pool = create_proxy_pool_from_config(
                PROXY_POOL,
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES
            )
        elif PROXY_MODE == 'single':
            logger.info(f"Initializing single proxy mode...")
            proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES
            )
    elif PROXY_HTTP or PROXY_HTTPS:
        logger.info("Using legacy PROXY_HTTP/PROXY_HTTPS configuration")
        legacy_proxy = {
            'name': 'Legacy-Proxy',
            'http': PROXY_HTTP,
            'https': PROXY_HTTPS
        }
        proxy_pool = create_proxy_pool_from_config(
            [legacy_proxy],
            cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
            max_failures=PROXY_POOL_MAX_FAILURES
        )
    
    global_proxy_helper = create_proxy_helper_from_config(
        proxy_pool=proxy_pool,
        proxy_modules=PROXY_MODULES,
        proxy_mode=PROXY_MODE,
        proxy_http=PROXY_HTTP,
        proxy_https=PROXY_HTTPS
    )
    logger.info("Proxy helper initialized successfully")


def test_qbittorrent_connection(use_proxy=False):
    """Test if qBittorrent is accessible"""
    try:
        proxies = get_proxies_dict('qbittorrent', use_proxy)
        masked_url = f"http://{mask_ip_address(QB_HOST)}:{QB_PORT}"
        logger.info(f"Testing connection to qBittorrent at {masked_url}")
        response = requests.get(f'{QB_BASE_URL}/api/v2/app/version', timeout=10, proxies=proxies)
        if response.status_code == 200 or response.status_code == 403:
            logger.info("qBittorrent is accessible")
            return True
        else:
            logger.warning(f"qBittorrent responded with status code: {response.status_code}")
            return False
    except requests.RequestException as e:
        logger.error(f"Cannot connect to qBittorrent: {e}")
        return False


def login_to_qbittorrent(session, use_proxy=False):
    """Login to qBittorrent web UI"""
    login_url = f'{QB_BASE_URL}/api/v2/auth/login'
    login_data = {
        'username': QB_USERNAME,
        'password': QB_PASSWORD
    }
    
    proxies = get_proxies_dict('qbittorrent', use_proxy)
    masked_url = f"http://{mask_ip_address(QB_HOST)}:{QB_PORT}"
    
    try:
        logger.info(f"Attempting to login to qBittorrent at {masked_url} as {mask_username(QB_USERNAME)}")
        response = session.post(login_url, data=login_data, timeout=REQUEST_TIMEOUT, proxies=proxies)
        
        if response.status_code == 200:
            logger.info("Successfully logged in to qBittorrent")
            return True
        else:
            logger.error(f"Login failed with status code: {response.status_code}")
            return False
            
    except requests.RequestException as e:
        logger.error(f"Login error: {e}")
        return False


def get_recent_torrents(session, days=2, category=None, categories=None, use_proxy=False):
    """
    Get torrents added within the specified number of days.
    
    Args:
        session: Requests session with login cookies
        days: Number of days to look back (default 2 for today and yesterday)
        category: Optional single category filter (deprecated, use categories instead)
        categories: Optional list of categories to filter
        use_proxy: Whether to use proxy
        
    Returns:
        list: List of torrent info dictionaries
    """
    info_url = f'{QB_BASE_URL}/api/v2/torrents/info'
    proxies = get_proxies_dict('qbittorrent', use_proxy)
    
    # Build category filter list
    category_filter = None
    if categories:
        # Use categories list if provided
        category_filter = categories
    elif category:
        # Fallback to single category for backward compatibility
        category_filter = [category]
    
    # qBittorrent API only supports single category filter, so we get all and filter in code
    params = {}
    
    try:
        response = session.get(info_url, params=params, timeout=REQUEST_TIMEOUT, proxies=proxies)
        
        if response.status_code == 200:
            torrents = response.json()
            
            # Calculate cutoff timestamp (start of the first included day)
            # days=1 means today only, days=2 means today and yesterday, etc.
            cutoff_date = datetime.now() - timedelta(days=days - 1)
            cutoff_timestamp = int(cutoff_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            
            # Filter torrents by added_on timestamp and category
            recent_torrents = []
            for torrent in torrents:
                added_on = torrent.get('added_on', 0)
                torrent_category = torrent.get('category', '')
                
                # Check time filter
                if added_on < cutoff_timestamp:
                    continue
                
                # Check category filter if specified
                if category_filter:
                    if torrent_category not in category_filter:
                        continue
                
                recent_torrents.append(torrent)
            
            if category_filter:
                logger.info(f"Found {len(recent_torrents)} torrents in categories {category_filter} added in the last {days} day(s)")
            else:
                logger.info(f"Found {len(recent_torrents)} torrents added in the last {days} day(s)")
            return recent_torrents
        else:
            logger.warning(f"Failed to get torrent list: {response.status_code}")
            return []
            
    except requests.RequestException as e:
        logger.error(f"Error getting torrent list: {e}")
        return []


def get_torrent_files(session, torrent_hash, use_proxy=False):
    """
    Get list of files in a torrent.
    
    Args:
        session: Requests session with login cookies
        torrent_hash: Hash of the torrent
        use_proxy: Whether to use proxy
        
    Returns:
        list: List of file info dictionaries on success (may be empty if metadata not ready)
        None: On API failure or exception (to distinguish from empty metadata)
    """
    files_url = f'{QB_BASE_URL}/api/v2/torrents/files'
    proxies = get_proxies_dict('qbittorrent', use_proxy)
    
    try:
        response = session.get(
            files_url,
            params={'hash': torrent_hash},
            timeout=REQUEST_TIMEOUT,
            proxies=proxies
        )
        
        if response.status_code == 200:
            return response.json()  # Returns [] if metadata not ready, or list of files
        else:
            logger.warning(f"Failed to get files for torrent {torrent_hash}: {response.status_code}")
            return None  # API failure - return None to distinguish from empty metadata
            
    except requests.RequestException as e:
        logger.error(f"Error getting files for torrent {torrent_hash}: {e}")
        return None  # Request exception - return None to distinguish from empty metadata


def set_file_priority(session, torrent_hash, file_ids, priority, use_proxy=False):
    """
    Set download priority for specific files in a torrent.
    
    Args:
        session: Requests session with login cookies
        torrent_hash: Hash of the torrent
        file_ids: List of file IDs to set priority for
        priority: Priority value (0=do not download, 1=normal, 6=high, 7=max)
        use_proxy: Whether to use proxy
        
    Returns:
        bool: True if successful
    """
    prio_url = f'{QB_BASE_URL}/api/v2/torrents/filePrio'
    proxies = get_proxies_dict('qbittorrent', use_proxy)
    
    # Convert file_ids list to pipe-separated string
    file_ids_str = '|'.join(str(fid) for fid in file_ids)
    
    try:
        response = session.post(
            prio_url,
            data={
                'hash': torrent_hash,
                'id': file_ids_str,
                'priority': priority
            },
            timeout=REQUEST_TIMEOUT,
            proxies=proxies
        )
        
        if response.status_code == 200:
            return True
        else:
            logger.warning(f"Failed to set file priority for torrent {torrent_hash}: {response.status_code}")
            return False
            
    except requests.RequestException as e:
        logger.error(f"Error setting file priority for torrent {torrent_hash}: {e}")
        return False


def get_torrent_properties(session, torrent_hash, use_proxy=False):
    """
    Get detailed properties of a torrent, including save_path.
    
    Args:
        session: Requests session with login cookies
        torrent_hash: Hash of the torrent
        use_proxy: Whether to use proxy
        
    Returns:
        dict: Torrent properties or empty dict on failure
    """
    props_url = f'{QB_BASE_URL}/api/v2/torrents/properties'
    proxies = get_proxies_dict('qbittorrent', use_proxy)
    
    try:
        response = session.get(
            props_url,
            params={'hash': torrent_hash},
            timeout=REQUEST_TIMEOUT,
            proxies=proxies
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.warning(f"Failed to get properties for torrent {torrent_hash}: {response.status_code}")
            return {}
            
    except requests.RequestException as e:
        logger.error(f"Error getting properties for torrent {torrent_hash}: {e}")
        return {}


def delete_local_file(file_path):
    """
    Delete a local file if it exists.
    
    Args:
        file_path: Full path to the file
        
    Returns:
        tuple: (success: bool, size_deleted: int)
    """
    try:
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            os.remove(file_path)
            logger.info(f"  [DELETED] {file_path} ({format_size(file_size)})")
            return True, file_size
        else:
            logger.debug(f"  File not found (not downloaded yet): {file_path}")
            return False, 0
    except OSError as e:
        logger.error(f"  Failed to delete file {file_path}: {e}")
        return False, 0


def format_size(size_bytes):
    """Format size in bytes to human readable string"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def filter_small_files(session, torrents, min_size_mb, dry_run=False, use_proxy=False, delete_local_files_flag=False):
    """
    Filter out small files from torrents.
    
    Args:
        session: Requests session with login cookies
        torrents: List of torrent info dictionaries
        min_size_mb: Minimum file size in MB
        dry_run: If True, don't make actual changes
        use_proxy: Whether to use proxy
        delete_local_files_flag: If True, delete local files that have been downloaded
        
    Returns:
        dict: Statistics about the filtering operation
    """
    min_size_bytes = min_size_mb * 1024 * 1024  # Convert MB to bytes
    
    stats = {
        'torrents_processed': 0,
        'torrents_with_filtered_files': 0,
        'files_filtered': 0,
        'files_kept': 0,
        'size_saved': 0,  # Total size of filtered files
        'local_files_deleted': 0,
        'local_size_deleted': 0,  # Total size of deleted local files
        'pending_metadata': 0,  # Torrents waiting for metadata (not an error)
        'errors': 0,  # Actual errors (API failures, etc.)
        'details': []  # List of (torrent_name, filtered_files_count, filtered_size, deleted_count, deleted_size)
    }
    
    for torrent in torrents:
        torrent_hash = torrent.get('hash', '')
        torrent_name = torrent.get('name', 'Unknown')
        torrent_save_path = torrent.get('save_path', '')  # Save path from torrent info
        added_on = torrent.get('added_on', 0)
        added_date = datetime.fromtimestamp(added_on).strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"Processing torrent: {torrent_name} (added: {added_date})")
        
        files = get_torrent_files(session, torrent_hash, use_proxy)
        
        # Distinguish between API failure (None) and metadata not ready (empty list)
        if files is None:
            # API failure - this is an actual error
            logger.warning(f"  Failed to get file list for: {torrent_name}")
            stats['errors'] += 1
            continue
        
        if len(files) == 0:
            # Metadata not yet available is a normal condition for recently added torrents
            # This should not be counted as an error
            logger.info(f"  Metadata not yet available for: {torrent_name} (will be processed on next run)")
            stats['pending_metadata'] += 1
            continue
        
        stats['torrents_processed'] += 1
        
        # Find files to filter (smaller than threshold)
        files_to_filter = []
        for idx, file_info in enumerate(files):
            file_size = file_info.get('size', 0)
            file_name = file_info.get('name', f'file_{idx}')
            current_priority = file_info.get('priority', 1)
            file_progress = file_info.get('progress', 0)  # 0.0 to 1.0
            
            # Only filter files that are currently set to download (priority > 0)
            if file_size < min_size_bytes and current_priority > 0:
                files_to_filter.append({
                    'id': idx,
                    'name': file_name,
                    'size': file_size,
                    'progress': file_progress
                })
            elif current_priority > 0:
                stats['files_kept'] += 1
        
        if files_to_filter:
            filtered_size = sum(f['size'] for f in files_to_filter)
            stats['files_filtered'] += len(files_to_filter)
            stats['size_saved'] += filtered_size
            stats['torrents_with_filtered_files'] += 1
            
            torrent_deleted_count = 0
            torrent_deleted_size = 0
            
            # Log filtered files
            for f in files_to_filter:
                progress_str = f" - {f['progress']*100:.1f}% downloaded" if f['progress'] > 0 else ""
                logger.info(f"  [FILTER] {f['name']} ({format_size(f['size'])}){progress_str}")
            
            # Set priority to 0 (do not download) for filtered files
            if not dry_run:
                file_ids = [f['id'] for f in files_to_filter]
                priority_set_success = set_file_priority(session, torrent_hash, file_ids, priority=0, use_proxy=use_proxy)
                
                if priority_set_success:
                    logger.info(f"  Successfully filtered {len(files_to_filter)} files from: {torrent_name}")
                    
                    # Only delete local files after successfully setting priority to prevent data loss
                    # If priority setting fails, qBittorrent may still download these files
                    if delete_local_files_flag:
                        # Validate save_path to prevent accidental deletion of files in wrong location
                        if not torrent_save_path or not os.path.isabs(torrent_save_path):
                            logger.warning(f"  Skipping local file deletion: invalid save_path '{torrent_save_path}'")
                        else:
                            for f in files_to_filter:
                                if f['progress'] > 0:  # File has been partially or fully downloaded
                                    # Construct full file path: save_path + file_name
                                    full_path = os.path.join(torrent_save_path, f['name'])
                                    deleted, size_deleted = delete_local_file(full_path)
                                    if deleted:
                                        torrent_deleted_count += 1
                                        torrent_deleted_size += size_deleted
                                        stats['local_files_deleted'] += 1
                                        stats['local_size_deleted'] += size_deleted
                else:
                    logger.error(f"  Failed to filter files from: {torrent_name}")
                    stats['errors'] += 1
            else:
                logger.info(f"  [DRY-RUN] Would filter {len(files_to_filter)} files from: {torrent_name}")
                if delete_local_files_flag:
                    # Validate save_path in dry-run mode too
                    if not torrent_save_path or not os.path.isabs(torrent_save_path):
                        logger.warning(f"  [DRY-RUN] Would skip local file deletion: invalid save_path '{torrent_save_path}'")
                    else:
                        # In dry-run mode, show what would be deleted
                        files_to_delete = [f for f in files_to_filter if f['progress'] > 0]
                        if files_to_delete:
                            logger.info(f"  [DRY-RUN] Would delete {len(files_to_delete)} local files")
                            for f in files_to_delete:
                                full_path = os.path.join(torrent_save_path, f['name'])
                                logger.info(f"    [DRY-RUN] Would delete: {full_path}")
            
            stats['details'].append((torrent_name, len(files_to_filter), filtered_size, torrent_deleted_count, torrent_deleted_size))
        else:
            logger.debug(f"  No files to filter in: {torrent_name}")
    
    return stats


def print_summary(stats, min_size_mb, days, dry_run=False, delete_local_files_flag=False):
    """Print a summary of the filtering operation"""
    logger.info("=" * 70)
    logger.info("FILE FILTER SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Mode: {'DRY-RUN (no changes made)' if dry_run else 'LIVE'}")
    logger.info(f"Filter threshold: {min_size_mb} MB")
    logger.info(f"Days lookback: {days}")
    logger.info(f"Delete local files: {'Yes' if delete_local_files_flag else 'No'}")
    logger.info("-" * 70)
    logger.info(f"Torrents processed: {stats['torrents_processed']}")
    logger.info(f"Torrents pending metadata: {stats.get('pending_metadata', 0)}")
    logger.info(f"Torrents with filtered files: {stats['torrents_with_filtered_files']}")
    logger.info(f"Files filtered (set to not download): {stats['files_filtered']}")
    logger.info(f"Files kept (above threshold): {stats['files_kept']}")
    logger.info(f"Total size saved: {format_size(stats['size_saved'])}")
    
    if delete_local_files_flag:
        logger.info(f"Local files deleted: {stats.get('local_files_deleted', 0)}")
        logger.info(f"Local disk space freed: {format_size(stats.get('local_size_deleted', 0))}")
    
    if stats['errors'] > 0:
        logger.info(f"Errors encountered: {stats['errors']}")
    
    if stats['details']:
        logger.info("-" * 70)
        logger.info("Torrents with filtered files:")
        for detail in stats['details']:
            # Handle both old format (3 items) and new format (5 items)
            if len(detail) == 5:
                name, count, size, deleted_count, deleted_size = detail
                deleted_info = f", deleted {deleted_count} local ({format_size(deleted_size)})" if deleted_count > 0 else ""
            else:
                name, count, size = detail
                deleted_info = ""
            # Truncate long names
            display_name = name if len(name) <= 45 else name[:42] + "..."
            logger.info(f"  - {display_name}: {count} files ({format_size(size)}){deleted_info}")
    
    logger.info("=" * 70)


def main():
    args = parse_arguments()
    
    logger.info("Starting qBittorrent File Filter...")
    
    # Parse categories from JSON array string
    categories_list = None
    if args.categories:
        import json
        try:
            categories_list = json.loads(args.categories)
            if not isinstance(categories_list, list):
                logger.error(f"--categories must be a JSON array, got: {type(categories_list).__name__}")
                sys.exit(1)
            # Filter out empty strings
            categories_list = [c for c in categories_list if c]
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse --categories as JSON array: {e}")
            logger.error(f"Expected format: '[\"Category 1\", \"Category 2\"]'")
            sys.exit(1)
        logger.info(f"Configuration: min_size={args.min_size}MB, days={args.days}, categories={categories_list}, dry_run={args.dry_run}, delete_local_files={args.delete_local_files}")
    else:
        logger.info(f"Configuration: min_size={args.min_size}MB, days={args.days}, category={args.category}, dry_run={args.dry_run}, delete_local_files={args.delete_local_files}")
    
    # Initialize proxy helper
    initialize_proxy_helper(args.use_proxy)
    
    if args.use_proxy:
        logger.info("Proxy enabled for qBittorrent API requests")
    
    # Test qBittorrent connection
    if not test_qbittorrent_connection(args.use_proxy):
        logger.error("Cannot connect to qBittorrent. Please check your configuration.")
        sys.exit(1)
    
    # Create session and login
    session = requests.Session()
    if not login_to_qbittorrent(session, args.use_proxy):
        logger.error("Failed to login to qBittorrent.")
        sys.exit(1)
    
    # Get recent torrents
    torrents = get_recent_torrents(
        session,
        days=args.days,
        category=args.category,
        categories=categories_list,
        use_proxy=args.use_proxy
    )
    
    if not torrents:
        logger.info("No recent torrents found to process.")
        print_summary({
            'torrents_processed': 0,
            'torrents_with_filtered_files': 0,
            'files_filtered': 0,
            'files_kept': 0,
            'size_saved': 0,
            'local_files_deleted': 0,
            'local_size_deleted': 0,
            'pending_metadata': 0,
            'errors': 0,
            'details': []
        }, args.min_size, args.days, args.dry_run, args.delete_local_files)
        return
    
    # Filter small files
    stats = filter_small_files(
        session,
        torrents,
        min_size_mb=args.min_size,
        dry_run=args.dry_run,
        use_proxy=args.use_proxy,
        delete_local_files_flag=args.delete_local_files
    )
    
    # Print summary
    print_summary(stats, args.min_size, args.days, args.dry_run, args.delete_local_files)
    
    # Exit with error code only if there were actual errors (not pending metadata)
    # Pending metadata is a normal condition for recently added torrents
    if stats['errors'] > 0 and stats['torrents_processed'] == 0:
        logger.error("All torrent processing failed due to errors!")
        sys.exit(1)
    
    # Log info if all torrents are pending metadata (this is normal, not an error)
    if stats.get('pending_metadata', 0) > 0 and stats['torrents_processed'] == 0 and stats['errors'] == 0:
        logger.info("All torrents are waiting for metadata. They will be processed on the next run.")


if __name__ == '__main__':
    main()

