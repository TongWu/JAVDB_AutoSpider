import csv
import requests
import logging
from datetime import datetime
import time
import os
import argparse
import sys

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Import unified configuration
try:
    from config import (
        QB_HOST, QB_PORT, QB_USERNAME, QB_PASSWORD,
        TORRENT_CATEGORY, TORRENT_CATEGORY_ADHOC, TORRENT_SAVE_PATH, AUTO_START, SKIP_CHECKING,
        REQUEST_TIMEOUT, DELAY_BETWEEN_ADDITIONS,
        UPLOADER_LOG_FILE, DAILY_REPORT_DIR, AD_HOC_DIR, LOG_LEVEL,
        PROXY_HTTP, PROXY_HTTPS, PROXY_MODULES,
        GIT_USERNAME, GIT_PASSWORD, GIT_REPO_URL, GIT_BRANCH
    )
except ImportError:
    # Fallback values if config.py doesn't exist
    QB_HOST = 'your_qbittorrent_ip'
    QB_PORT = 'your_qbittorrent_port'
    QB_USERNAME = 'your_qbittorrent_username'
    QB_PASSWORD = 'your_qbittorrent_password'
    
    TORRENT_CATEGORY = 'JavDB'
    TORRENT_CATEGORY_ADHOC = 'Ad Hoc'
    TORRENT_SAVE_PATH = ''
    AUTO_START = True
    SKIP_CHECKING = False
    
    REQUEST_TIMEOUT = 30
    DELAY_BETWEEN_ADDITIONS = 1
    
    UPLOADER_LOG_FILE = 'logs/qb_uploader.log'
    REPORTS_DIR = 'reports'
    DAILY_REPORT_DIR = 'reports/DailyReport'
    AD_HOC_DIR = 'reports/AdHoc'
    LOG_LEVEL = 'INFO'
    PROXY_HTTP = None
    PROXY_HTTPS = None
    PROXY_MODULES = ['all']
    GIT_USERNAME = 'github-actions'
    GIT_PASSWORD = ''
    GIT_REPO_URL = ''
    GIT_BRANCH = 'main'

# Import proxy pool configuration (with fallback)
try:
    from config import PROXY_MODE, PROXY_POOL, PROXY_POOL_COOLDOWN_SECONDS, PROXY_POOL_MAX_FAILURES
except ImportError:
    PROXY_MODE = 'single'
    PROXY_POOL = []
    PROXY_POOL_COOLDOWN_SECONDS = 691200  # 8 days (691200 seconds)
    PROXY_POOL_MAX_FAILURES = 3

# Import history manager functions
try:
    from utils.history_manager import is_downloaded_torrent
except ImportError:
    # Fallback function if import fails
    def is_downloaded_torrent(torrent_content):
        """Check if torrent content contains downloaded indicator"""
        return torrent_content.strip().startswith("[DOWNLOADED]")

# Import path helper for dated subdirectories
from utils.path_helper import get_dated_report_path, get_dated_subdir, find_latest_report_in_dated_dirs

# Configure logging
from utils.logging_config import setup_logging, get_logger
setup_logging(UPLOADER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Import git helper
from utils.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials

# Import masking utilities
from utils.masking import mask_ip_address, mask_username, mask_full

# Import proxy pool
from utils.proxy_pool import ProxyPool, create_proxy_pool_from_config

# Import proxy helper from request handler
from utils.request_handler import ProxyHelper, create_proxy_helper_from_config

# Global proxy pool instance
global_proxy_pool = None

# Global proxy helper instance
global_proxy_helper = None

# qBittorrent configuration
QB_BASE_URL = f'http://{QB_HOST}:{QB_PORT}'

def parse_arguments():
    parser = argparse.ArgumentParser(description='qBittorrent Uploader')
    parser.add_argument('--mode', choices=['adhoc', 'daily'], default='daily', help='Upload mode: adhoc (Ad Hoc folder) or daily (Daily Report folder)')
    parser.add_argument('--input-file', type=str, help='Specify input CSV file name (overrides default date-based name)')
    parser.add_argument('--use-proxy', action='store_true', help='Enable proxy for qBittorrent API requests (proxy settings from config.py)')
    parser.add_argument('--from-pipeline', action='store_true', help='Running from pipeline.py - use GIT_USERNAME for commits')
    return parser.parse_args()


def get_proxies_dict(module_name, use_proxy_flag):
    """
    Get proxies dictionary for requests if module should use proxy.
    Delegated to global_proxy_helper.
    
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

def find_latest_adhoc_csv_today():
    """
    Find the most recently created/modified AdHoc CSV file from today's dated directory.
    
    This function handles the case where spider generates a custom-named CSV file
    (e.g., Javdb_AdHoc_actors_森日向子_20251224.csv) and qb_uploader needs to find it.
    
    Returns:
        str or None: Path to the most recent AdHoc CSV file, or None if not found
    """
    current_date = datetime.now().strftime("%Y%m%d")
    
    # Look for any AdHoc CSV file from today using pattern matching
    # Pattern: Javdb_AdHoc_*_{date}.csv
    adhoc_pattern = f'Javdb_AdHoc_*_{current_date}.csv'
    
    latest_file = find_latest_report_in_dated_dirs(AD_HOC_DIR, adhoc_pattern)
    
    if latest_file:
        return latest_file
    
    # Fallback: try the legacy pattern without AdHoc prefix
    legacy_pattern = f'*_{current_date}.csv'
    return find_latest_report_in_dated_dirs(AD_HOC_DIR, legacy_pattern)


def get_csv_filename(mode='daily'):
    """
    Get the CSV filename for current date and mode with dated subdirectory (YYYY/MM).
    
    For adhoc mode, this function first tries to auto-discover the most recent
    adhoc CSV file generated today by the spider. This handles custom-named files
    like 'Javdb_AdHoc_actors_ActorName_20251224.csv'.
    
    Args:
        mode: 'daily' or 'adhoc'
    
    Returns:
        str: Path to the CSV file
    """
    current_date = datetime.now().strftime("%Y%m%d")
    
    if mode == 'adhoc':
        # Try to auto-discover the latest adhoc CSV from today
        latest_adhoc = find_latest_adhoc_csv_today()
        if latest_adhoc:
            logger.info(f"Auto-discovered adhoc CSV: {latest_adhoc}")
            return latest_adhoc
        else:
            # Fallback to default naming if no file found
            logger.warning("No adhoc CSV found for today, using default naming pattern")
            csv_filename = f'Javdb_TodayTitle_{current_date}.csv'
            return get_dated_report_path(AD_HOC_DIR, csv_filename)
    else:
        csv_filename = f'Javdb_TodayTitle_{current_date}.csv'
        return get_dated_report_path(DAILY_REPORT_DIR, csv_filename)

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
            logger.error("Please check your username and password in config.py")
            return False
            
    except requests.RequestException as e:
        logger.error(f"Login error: {e}")
        return False


def extract_hash_from_magnet(magnet_link):
    """
    Extract info hash from magnet link.
    
    Args:
        magnet_link: Magnet URI string
        
    Returns:
        str: Info hash in lowercase, or None if not found
    """
    import re
    # Magnet link format: magnet:?xt=urn:btih:HASH&...
    match = re.search(r'xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', magnet_link)
    if match:
        hash_value = match.group(1)
        # Convert base32 to hex if necessary (32 chars = base32, 40 chars = hex)
        if len(hash_value) == 32:
            try:
                import base64
                decoded = base64.b32decode(hash_value.upper())
                hash_value = decoded.hex()
            except Exception:
                pass
        return hash_value.lower()
    return None


def get_existing_torrents(session, use_proxy=False):
    """
    Get all existing torrents from qBittorrent.
    
    Args:
        session: Requests session with login cookies
        use_proxy: Whether to use proxy
        
    Returns:
        set: Set of torrent hashes (lowercase) that are not in error state
    """
    info_url = f'{QB_BASE_URL}/api/v2/torrents/info'
    proxies = get_proxies_dict('qbittorrent', use_proxy)
    
    try:
        response = session.get(info_url, timeout=REQUEST_TIMEOUT, proxies=proxies)
        
        if response.status_code == 200:
            torrents = response.json()
            # Exclude torrents in error state
            existing_hashes = set()
            for t in torrents:
                state = t.get('state', '')
                # Skip torrents in error state
                if state not in ('error', 'missingFiles'):
                    hash_value = t.get('hash', '').lower()
                    if hash_value:
                        existing_hashes.add(hash_value)
            
            logger.info(f"Found {len(existing_hashes)} existing torrents in qBittorrent (excluding errors)")
            return existing_hashes
        else:
            logger.warning(f"Failed to get torrent list: {response.status_code}")
            return set()
            
    except requests.RequestException as e:
        logger.error(f"Error getting torrent list: {e}")
        return set()


def is_torrent_exists(magnet_link, existing_hashes):
    """
    Check if a torrent already exists in qBittorrent.
    
    Args:
        magnet_link: Magnet URI string
        existing_hashes: Set of existing torrent hashes
        
    Returns:
        bool: True if torrent already exists
    """
    torrent_hash = extract_hash_from_magnet(magnet_link)
    if torrent_hash and torrent_hash in existing_hashes:
        return True
    return False


def add_torrent_to_qbittorrent(session, magnet_link, title, mode='daily', use_proxy=False):
    """Add a torrent to qBittorrent"""
    add_url = f'{QB_BASE_URL}/api/v2/torrents/add'
    
    # Choose category based on mode
    category = TORRENT_CATEGORY_ADHOC if mode == 'adhoc' else TORRENT_CATEGORY
    
    # Prepare the data for adding torrent
    torrent_data = {
        'urls': magnet_link,
        'name': title,
        'category': category,
        'autoTMM': 'true',
        'savepath': TORRENT_SAVE_PATH,
        'downloadPath': '',
        'skip_checking': str(SKIP_CHECKING).lower(),
        'contentLayout': 'Original',
        'ratioLimit': '-2',
        'seedingTimeLimit': '-2',
        'addPaused': str(not AUTO_START).lower()
    }
    
    proxies = get_proxies_dict('qbittorrent', use_proxy)
    
    try:
        logger.debug(f"Adding torrent: {title} with category: {category}")
        response = session.post(add_url, data=torrent_data, timeout=REQUEST_TIMEOUT, proxies=proxies)
        
        if response.status_code == 200:
            logger.debug(f"Successfully added torrent: {title} to category: {category}")
            return True
        else:
            logger.error(f"Failed to add torrent '{title}' with status code: {response.status_code}")
            return False
            
    except requests.RequestException as e:
        logger.error(f"Error adding torrent '{title}': {e}")
        return False

def read_csv_file(filename):
    """Read the CSV file and extract all magnet links, skipping downloaded torrents.
    
    Returns:
        tuple: (torrents_list, file_exists_bool)
            - torrents_list: List of torrent dictionaries
            - file_exists_bool: True if file was found, False if file not found
    """
    torrents = []
    skipped_count = 0
    
    if not os.path.exists(filename):
        logger.error(f"CSV file not found: {filename}")
        logger.info("Make sure you have run the spider script first to generate the CSV file")
        return torrents, False  # Return tuple indicating file not found
    
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                href = row.get('href', '')
                video_code = row.get('video_code', '')
                
                # Extract magnet links from all four columns, skipping downloaded ones
                if row.get('hacked_subtitle') and row['hacked_subtitle'].strip():
                    if is_downloaded_torrent(row['hacked_subtitle']):
                        logger.debug(f"Skipping downloaded torrent: {video_code} [Hacked+Subtitle]")
                        skipped_count += 1
                        continue
                    torrents.append({
                        'magnet': row['hacked_subtitle'].strip(),
                        'title': f"{video_code} [Hacked+Subtitle]",
                        'page': row.get('page', 'N/A'),
                        'type': 'hacked_subtitle',
                        'href': href,
                        'video_code': video_code
                    })
                
                if row.get('hacked_no_subtitle') and row['hacked_no_subtitle'].strip():
                    if is_downloaded_torrent(row['hacked_no_subtitle']):
                        logger.debug(f"Skipping downloaded torrent: {video_code} [Hacked-NoSubtitle]")
                        skipped_count += 1
                        continue
                    torrents.append({
                        'magnet': row['hacked_no_subtitle'].strip(),
                        'title': f"{video_code} [Hacked-NoSubtitle]",
                        'page': row.get('page', 'N/A'),
                        'type': 'hacked_no_subtitle',
                        'href': href,
                        'video_code': video_code
                    })
                
                if row.get('subtitle') and row['subtitle'].strip():
                    if is_downloaded_torrent(row['subtitle']):
                        logger.debug(f"Skipping downloaded torrent: {video_code} [Subtitle]")
                        skipped_count += 1
                        continue
                    torrents.append({
                        'magnet': row['subtitle'].strip(),
                        'title': f"{video_code} [Subtitle]",
                        'page': row.get('page', 'N/A'),
                        'type': 'subtitle',
                        'href': href,
                        'video_code': video_code
                    })
                
                if row.get('no_subtitle') and row['no_subtitle'].strip():
                    if is_downloaded_torrent(row['no_subtitle']):
                        logger.debug(f"Skipping downloaded torrent: {video_code} [NoSubtitle]")
                        skipped_count += 1
                        continue
                    torrents.append({
                        'magnet': row['no_subtitle'].strip(),
                        'title': f"{video_code} [NoSubtitle]",
                        'page': row.get('page', 'N/A'),
                        'type': 'no_subtitle',
                        'href': href,
                        'video_code': video_code
                    })
        
        logger.info(f"Found {len(torrents)} torrent links in {filename}")
        if skipped_count > 0:
            logger.info(f"Skipped {skipped_count} already downloaded torrents")
        return torrents, True  # File exists
        
    except Exception as e:
        logger.error(f"Error reading CSV file: {e}")
        return torrents, True  # File exists but had read error

def initialize_proxy_helper(use_proxy):
    """Initialize global proxy pool and proxy helper."""
    global global_proxy_pool, global_proxy_helper
    
    if not use_proxy:
        global_proxy_pool = None
        # When use_proxy=False, don't pass proxy configs to ensure no proxy is used
        global_proxy_helper = create_proxy_helper_from_config(
            proxy_pool=None,
            proxy_modules=PROXY_MODULES,
            proxy_mode=PROXY_MODE,
            proxy_http=None,
            proxy_https=None
        )
        return
    
    # Check if we have PROXY_POOL configuration
    if PROXY_POOL and len(PROXY_POOL) > 0:
        if PROXY_MODE == 'pool':
            logger.info(f"Initializing proxy pool with {len(PROXY_POOL)} proxies...")
            global_proxy_pool = create_proxy_pool_from_config(
                PROXY_POOL,
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Proxy pool initialized successfully")
        elif PROXY_MODE == 'single':
            logger.info(f"Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Single proxy initialized: {PROXY_POOL[0].get('name', 'Main-Proxy')}")
    elif PROXY_HTTP or PROXY_HTTPS:
        logger.info("Using legacy PROXY_HTTP/PROXY_HTTPS configuration")
        legacy_proxy = {
            'name': 'Legacy-Proxy',
            'http': PROXY_HTTP,
            'https': PROXY_HTTPS
        }
        global_proxy_pool = create_proxy_pool_from_config(
            [legacy_proxy],
            cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
            max_failures=PROXY_POOL_MAX_FAILURES
        )
    else:
        logger.warning("Proxy enabled but no proxy configuration found")
        global_proxy_pool = None
    
    # Create proxy helper with the initialized pool
    global_proxy_helper = create_proxy_helper_from_config(
        proxy_pool=global_proxy_pool,
        proxy_modules=PROXY_MODULES,
        proxy_mode=PROXY_MODE,
        proxy_http=PROXY_HTTP,
        proxy_https=PROXY_HTTPS
    )
    logger.info("Proxy helper initialized successfully")


def main():
    args = parse_arguments()
    mode = args.mode
    use_proxy = args.use_proxy
    logger.info("Starting qBittorrent uploader...")
    
    # Initialize proxy helper
    initialize_proxy_helper(use_proxy)
    
    if use_proxy:
        if global_proxy_helper is not None:
            stats = global_proxy_helper.get_statistics()
            
            if PROXY_MODE == 'pool':
                logger.info(f"PROXY POOL MODE for qBittorrent: {stats['total_proxies']} proxies with automatic failover")
            elif PROXY_MODE == 'single':
                logger.info(f"SINGLE PROXY MODE for qBittorrent: Using main proxy only")
                if stats['total_proxies'] > 0 and stats['proxies']:
                    main_proxy_name = stats['proxies'][0]['name']
                    logger.info(f"Main proxy: {main_proxy_name}")
        else:
            logger.warning("PROXY ENABLED: But no proxy configured")
    
    # Test qBittorrent connection first
    if not test_qbittorrent_connection(use_proxy):
        logger.error("Cannot connect to qBittorrent. Please check:")
        logger.error("1. qBittorrent is running")
        logger.error("2. Web UI is enabled")
        logger.error("3. Host and port settings in config.py")
        sys.exit(1)
    
    # Get CSV filename
    if args.input_file:
        # Check if input_file is already a full path (contains directory separator)
        if os.path.sep in args.input_file or args.input_file.startswith('reports'):
            # Already a full path, use as-is
            csv_filename = args.input_file
        else:
            # Just a filename, build full path with dated subdirectory
            if mode == 'adhoc':
                csv_filename = get_dated_report_path(AD_HOC_DIR, args.input_file)
            else:
                csv_filename = get_dated_report_path(DAILY_REPORT_DIR, args.input_file)
        logger.info(f"Using specified input file: {csv_filename}")
    else:
        csv_filename = get_csv_filename(mode)
        logger.info(f"Looking for CSV file: {csv_filename}")
    
    # Read torrent links from CSV
    torrents, file_exists = read_csv_file(csv_filename)
    
    if not file_exists:
        logger.error("CSV file not found - this is a critical error!")
        logger.error("The spider script may have failed to generate the report file.")
        sys.exit(1)
    
    if not torrents:
        logger.warning("No torrent links found in CSV file")
        # File exists but no torrents to add - this is not an error, just no work to do
        return
    
    # Create session for qBittorrent
    session = requests.Session()
    
    # Login to qBittorrent
    if not login_to_qbittorrent(session, use_proxy):
        logger.error("Failed to login to qBittorrent. Please check username and password.")
        sys.exit(1)
    
    # Get existing torrents to check for duplicates
    existing_hashes = get_existing_torrents(session, use_proxy)
    
    # Add torrents to qBittorrent
    hacked_subtitle_count = 0
    hacked_no_subtitle_count = 0
    subtitle_count = 0
    no_subtitle_count = 0
    failed_count = 0
    duplicate_count = 0
    total_torrents = len(torrents)
    
    logger.info(f"Starting to add {total_torrents} torrents to qBittorrent...")
    
    for i, torrent in enumerate(torrents, 1):
        # Check if torrent already exists in qBittorrent
        if is_torrent_exists(torrent['magnet'], existing_hashes):
            logger.info(f"[{i}/{total_torrents}] Skipping (already in qBittorrent): {torrent['title']}")
            duplicate_count += 1
            continue
        
        logger.info(f"[{i}/{total_torrents}] Adding: {torrent['title']}")
        
        success = add_torrent_to_qbittorrent(session, torrent['magnet'], torrent['title'], mode, use_proxy)
        
        if success:            
            if torrent['type'] == 'hacked_subtitle':
                hacked_subtitle_count += 1
            elif torrent['type'] == 'hacked_no_subtitle':
                hacked_no_subtitle_count += 1
            elif torrent['type'] == 'subtitle':
                subtitle_count += 1
            elif torrent['type'] == 'no_subtitle':
                no_subtitle_count += 1
            
            # Add newly added torrent hash to existing set to avoid re-adding in same session
            new_hash = extract_hash_from_magnet(torrent['magnet'])
            if new_hash:
                existing_hashes.add(new_hash)
        else:
            failed_count += 1
        
        # Small delay between additions
        time.sleep(DELAY_BETWEEN_ADDITIONS)
    
    # Generate summary
    successfully_added = hacked_subtitle_count + hacked_no_subtitle_count + subtitle_count + no_subtitle_count
    attempted = total_torrents - duplicate_count
    
    logger.info("=" * 50)
    logger.info("UPLOAD SUMMARY")
    logger.info("=" * 50)
    logger.info(f"CSV file: {csv_filename}")
    logger.info(f"Total torrents in CSV: {total_torrents}")
    logger.info(f"Skipped (already in qBittorrent): {duplicate_count}")
    logger.info(f"Attempted to add: {attempted}")
    logger.info(f"Successfully added: {successfully_added}")
    logger.info(f"  - Hacked subtitle torrents: {hacked_subtitle_count}")
    logger.info(f"  - Hacked no subtitle torrents: {hacked_no_subtitle_count}")
    logger.info(f"  - Subtitle torrents: {subtitle_count}")
    logger.info(f"  - No subtitle torrents: {no_subtitle_count}")
    logger.info(f"Failed to add: {failed_count}")
    if attempted > 0:
        logger.info(f"Success rate: {(successfully_added/attempted*100):.1f}%")
    else:
        logger.info("Success rate: N/A (all torrents already existed)")
    logger.info("=" * 50)
    
    # Git commit uploader results (only if credentials are available)
    from_pipeline = args.from_pipeline if hasattr(args, 'from_pipeline') else False
    
    if has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing uploader results...")
        # Flush log handlers to ensure all logs are written before commit
        flush_log_handlers()
        
        files_to_commit = ['logs/']
        commit_message = f"Auto-commit: Uploader results {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        git_commit_and_push(
            files_to_add=files_to_commit,
            commit_message=commit_message,
            from_pipeline=from_pipeline,
            git_username=GIT_USERNAME,
            git_password=GIT_PASSWORD,
            git_repo_url=GIT_REPO_URL,
            git_branch=GIT_BRANCH
        )
    else:
        logger.info("Skipping git commit - no credentials provided (commit will be handled by workflow)")
    
    # Exit with error code if all torrent additions failed (when there were attempts)
    if attempted > 0 and successfully_added == 0:
        logger.error("All torrent additions failed!")
        sys.exit(1)

if __name__ == '__main__':
    main() 

