import csv
import requests
import logging
from datetime import datetime
import time
import os
import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[3]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import unified configuration
from packages.python.javdb_platform.config_helper import cfg

QB_HOST = cfg('QB_HOST', 'your_qbittorrent_ip')
QB_PORT = cfg('QB_PORT', 'your_qbittorrent_port')
QB_USERNAME = cfg('QB_USERNAME', 'your_qbittorrent_username')
QB_PASSWORD = cfg('QB_PASSWORD', 'your_qbittorrent_password')
TORRENT_CATEGORY = cfg('TORRENT_CATEGORY', 'JavDB')
TORRENT_CATEGORY_ADHOC = cfg('TORRENT_CATEGORY_ADHOC', 'Ad Hoc')
TORRENT_SAVE_PATH = cfg('TORRENT_SAVE_PATH', '')
AUTO_START = cfg('AUTO_START', True)
SKIP_CHECKING = cfg('SKIP_CHECKING', False)
REQUEST_TIMEOUT = cfg('REQUEST_TIMEOUT', 30)
DELAY_BETWEEN_ADDITIONS = cfg('DELAY_BETWEEN_ADDITIONS', 1)
UPLOADER_LOG_FILE = cfg('UPLOADER_LOG_FILE', 'logs/qb_uploader.log')
DAILY_REPORT_DIR = cfg('DAILY_REPORT_DIR', 'reports/DailyReport')
AD_HOC_DIR = cfg('AD_HOC_DIR', 'reports/AdHoc')
LOG_LEVEL = cfg('LOG_LEVEL', 'INFO')
PROXY_HTTP = cfg('PROXY_HTTP', None)
PROXY_HTTPS = cfg('PROXY_HTTPS', None)
PROXY_MODULES = cfg('PROXY_MODULES', ['spider'])
GIT_USERNAME = cfg('GIT_USERNAME', 'github-actions')
GIT_PASSWORD = cfg('GIT_PASSWORD', '')
GIT_REPO_URL = cfg('GIT_REPO_URL', '')
GIT_BRANCH = cfg('GIT_BRANCH', 'main')

# Proxy pool
PROXY_MODE = cfg('PROXY_MODE', 'pool')
PROXY_POOL = cfg('PROXY_POOL', [])
PROXY_POOL_MAX_FAILURES = cfg('PROXY_POOL_MAX_FAILURES', 3)

# Import history manager functions
try:
    from packages.python.javdb_platform.history_manager import is_downloaded_torrent
except ImportError:
    # Fallback function if import fails
    def is_downloaded_torrent(torrent_content):
        """Check if torrent content contains downloaded indicator"""
        return torrent_content.strip().startswith("[DOWNLOADED]")

# Import path helper for dated subdirectories
from packages.python.javdb_platform.path_helper import get_dated_report_path, get_dated_subdir, find_latest_report_in_dated_dirs
from packages.python.javdb_platform.proxy_policy import (
    add_proxy_arguments,
    describe_proxy_override,
    resolve_proxy_override,
    should_proxy_module,
)

# Configure logging
from packages.python.javdb_platform.logging_config import setup_logging, get_logger
setup_logging(UPLOADER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Import git helper
from packages.python.javdb_platform.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials

# Import masking utilities
from packages.python.javdb_core.masking import mask_error, mask_username

# Import proxy pool
from packages.python.javdb_platform.proxy_pool import ProxyPool, create_proxy_pool_from_config

# Import proxy helper from request handler
from packages.python.javdb_platform.request_handler import ProxyHelper, create_proxy_helper_from_config
from packages.python.javdb_platform.qb_config import (
    qb_allow_insecure_http,
    qb_base_url_candidates,
    masked_qb_base_url,
    qb_verify_tls,
)

# Global proxy pool instance
global_proxy_pool = None

# Global proxy helper instance
global_proxy_helper = None

# qBittorrent configuration
QB_ALLOW_INSECURE_HTTP = qb_allow_insecure_http()
QB_BASE_URL_CANDIDATES = qb_base_url_candidates(
    allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
)
QB_BASE_URL = QB_BASE_URL_CANDIDATES[0]
QB_MASKED_URL = masked_qb_base_url(
    QB_BASE_URL,
    allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
)
QB_VERIFY_TLS = qb_verify_tls()


def _set_active_qb_base_url(base_url):
    """Persist the qBittorrent endpoint that proved reachable."""
    global QB_BASE_URL, QB_MASKED_URL, QB_ALLOW_INSECURE_HTTP
    QB_BASE_URL = base_url.rstrip('/')
    # HTTPS primary may fail (e.g. self-signed); HTTP fallback is still plain HTTP — align flag for masking and later calls.
    if urlsplit(QB_BASE_URL).scheme == 'http':
        QB_ALLOW_INSECURE_HTTP = True
    QB_MASKED_URL = masked_qb_base_url(
        QB_BASE_URL,
        allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
    )


def _ordered_qb_base_urls():
    """Try the last known-good URL first, then the remaining candidates."""
    ordered = []
    if QB_BASE_URL:
        ordered.append(QB_BASE_URL)
    for candidate in QB_BASE_URL_CANDIDATES:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered

def parse_arguments():
    parser = argparse.ArgumentParser(description='qBittorrent Uploader')
    parser.add_argument('--mode', choices=['adhoc', 'daily'], default='daily', help='Upload mode: adhoc (Ad Hoc folder) or daily (Daily Report folder)')
    parser.add_argument('--input-file', type=str, help='Specify input CSV file name (overrides default date-based name)')
    add_proxy_arguments(
        parser,
        use_help='Force-enable proxy for qBittorrent API requests',
        no_help='Force-disable proxy for qBittorrent API requests',
    )
    parser.add_argument('--from-pipeline', action='store_true', help='Running from pipeline.py - use GIT_USERNAME for commits')
    parser.add_argument('--category', type=str, help='Override qBittorrent category (defaults to TORRENT_CATEGORY_ADHOC for adhoc mode, TORRENT_CATEGORY for daily mode)')
    parser.add_argument('--session-id', type=int, default=None, help='Report session ID for saving uploader stats to SQLite')
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

def find_latest_adhoc_csv():
    """
    Find the most recently created/modified AdHoc CSV file.
    
    This function handles the case where spider generates a custom-named CSV file
    (e.g., Javdb_AdHoc_actors_森日向子_20251224.csv) and qb_uploader needs to find it.
    
    Note: This function uses wildcard patterns (not date-specific) to handle 
    cross-midnight scenarios where spider runs before midnight but qb_uploader 
    runs after midnight. It relies on file modification time to find the most 
    recent file.
    
    Returns:
        str or None: Path to the most recent AdHoc CSV file, or None if not found
    """
    # Use wildcard pattern to find the most recent AdHoc CSV file
    # Pattern: Javdb_AdHoc_*.csv (any date)
    # This handles cross-midnight scenarios where spider generates file on day N
    # but qb_uploader runs on day N+1
    adhoc_pattern = 'Javdb_AdHoc_*.csv'
    
    latest_file = find_latest_report_in_dated_dirs(AD_HOC_DIR, adhoc_pattern)
    
    if latest_file:
        return latest_file
    
    # Fallback: try to find any CSV file (legacy pattern)
    legacy_pattern = 'Javdb_*.csv'
    return find_latest_report_in_dated_dirs(AD_HOC_DIR, legacy_pattern)


def find_latest_daily_csv():
    """
    Find the most recently created/modified Daily CSV file.
    
    This function uses wildcard patterns (not date-specific) to handle 
    cross-midnight scenarios where spider runs before midnight but qb_uploader 
    runs after midnight. It relies on file modification time to find the most 
    recent file.
    
    Returns:
        str or None: Path to the most recent Daily CSV file, or None if not found
    """
    # Use wildcard pattern to find the most recent Daily CSV file
    # Pattern: Javdb_TodayTitle_*.csv (any date)
    daily_pattern = 'Javdb_TodayTitle_*.csv'
    
    return find_latest_report_in_dated_dirs(DAILY_REPORT_DIR, daily_pattern)


def get_csv_filename(mode='daily'):
    """
    Get the CSV filename for the specified mode with dated subdirectory (YYYY/MM).
    
    This function auto-discovers the most recent CSV file using wildcard patterns,
    which handles cross-midnight scenarios where spider runs before midnight but
    qb_uploader runs after midnight.
    
    For adhoc mode: looks for Javdb_AdHoc_*.csv files
    For daily mode: looks for Javdb_TodayTitle_*.csv files
    
    Args:
        mode: 'daily' or 'adhoc'
    
    Returns:
        str: Path to the CSV file (auto-discovered or fallback to date-based naming)
    """
    current_date = datetime.now().strftime("%Y%m%d")
    
    if mode == 'adhoc':
        # Try to auto-discover the latest adhoc CSV
        latest_adhoc = find_latest_adhoc_csv()
        if latest_adhoc:
            logger.info(f"Auto-discovered adhoc CSV: {latest_adhoc}")
            return latest_adhoc
        else:
            # Fallback to default naming if no file found
            logger.warning("No adhoc CSV found, using default naming pattern with current date")
            csv_filename = f'Javdb_TodayTitle_{current_date}.csv'
            return get_dated_report_path(AD_HOC_DIR, csv_filename)
    else:
        # Try to auto-discover the latest daily CSV
        latest_daily = find_latest_daily_csv()
        if latest_daily:
            logger.info(f"Auto-discovered daily CSV: {latest_daily}")
            return latest_daily
        else:
            # Fallback to default naming if no file found
            logger.warning("No daily CSV found, using default naming pattern with current date")
            csv_filename = f'Javdb_TodayTitle_{current_date}.csv'
            return get_dated_report_path(DAILY_REPORT_DIR, csv_filename)

def test_qbittorrent_connection(use_proxy=False):
    """Test if qBittorrent is accessible.

    Thin wrapper around ``qb_client.try_ping_base_urls``. ``requests.get``
    is passed in as the HTTP callable so tests that patch
    ``scripts.qb_uploader.requests.get`` continue to work. On success the
    resolved base URL is persisted via ``_set_active_qb_base_url`` (which
    also flips ``QB_ALLOW_INSECURE_HTTP`` when an HTTP fallback succeeds)."""
    from packages.python.javdb_integrations.qb_client import try_ping_base_urls

    proxies = get_proxies_dict('qbittorrent', use_proxy)
    url, _ = try_ping_base_urls(
        _ordered_qb_base_urls(),
        get_fn=requests.get,
        allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
        proxies=proxies,
        timeout=10,
        verify=QB_VERIFY_TLS,
    )
    if url:
        _set_active_qb_base_url(url)
        return True
    return False


def login_to_qbittorrent(session, use_proxy=False):
    """Login to qBittorrent web UI.

    Thin wrapper around ``qb_client.try_login_base_urls``. The ``session``
    argument stays in the signature for backwards compatibility — we pass
    its ``post`` method as the HTTP callable so existing tests that mock
    ``session.post`` continue to work."""
    from packages.python.javdb_integrations.qb_client import (
        LOGIN_SUCCESS,
        LOGIN_REJECTED,
        try_login_base_urls,
    )

    proxies = get_proxies_dict('qbittorrent', use_proxy)
    outcome, url, _ = try_login_base_urls(
        _ordered_qb_base_urls(),
        QB_USERNAME,
        QB_PASSWORD,
        post_fn=session.post,
        allow_insecure_http=QB_ALLOW_INSECURE_HTTP,
        proxies=proxies,
        timeout=REQUEST_TIMEOUT,
        verify=QB_VERIFY_TLS,
    )
    if outcome == LOGIN_SUCCESS and url:
        _set_active_qb_base_url(url)
        return True
    if outcome == LOGIN_REJECTED:
        logger.error("Please check your username and password in config.py")
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


def _wrap_session_as_client(session, use_proxy=False):
    """Wrap ``qb_uploader``'s already-logged-in ``requests.Session`` into
    a :class:`QBittorrentClient` so it can reuse the shared helpers
    without doing a second login."""
    from packages.python.javdb_integrations.qb_client import QBittorrentClient
    return QBittorrentClient.from_existing_session(
        session,
        base_url=QB_BASE_URL,
        proxies=get_proxies_dict('qbittorrent', use_proxy),
        request_timeout=REQUEST_TIMEOUT,
    )


def get_existing_torrents(session, use_proxy=False):
    """
    Get all existing torrents from qBittorrent.

    Thin wrapper around :meth:`QBittorrentClient.get_existing_hashes`.

    Args:
        session: Requests session with login cookies
        use_proxy: Whether to use proxy

    Returns:
        set: Set of torrent hashes (lowercase) that are not in error state
    """
    client = _wrap_session_as_client(session, use_proxy=use_proxy)
    existing_hashes = client.get_existing_hashes()
    logger.info(
        f"Found {len(existing_hashes)} existing torrents in qBittorrent "
        f"(excluding errors)"
    )
    return existing_hashes


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


def add_torrent_to_qbittorrent(
    session, magnet_link, title, mode='daily', use_proxy=False, category_override=None
):
    """Add a torrent to qBittorrent.

    Thin wrapper around :meth:`QBittorrentClient.add_torrent` that wires
    in the uploader's global defaults (save path, auto-TMM, skip
    checking, content layout, ratio/seed-time limits, paused state).
    """
    if category_override:
        category = category_override
    else:
        category = TORRENT_CATEGORY_ADHOC if mode == 'adhoc' else TORRENT_CATEGORY

    client = _wrap_session_as_client(session, use_proxy=use_proxy)

    try:
        logger.debug(f"Adding torrent: {title} with category: {category}")
        ok = client.add_torrent(
            magnet_link=magnet_link,
            name=title,
            category=category,
            save_path=TORRENT_SAVE_PATH,
            auto_tmm=True,
            skip_checking=SKIP_CHECKING,
            content_layout='Original',
            ratio_limit='-2',
            seeding_time_limit='-2',
            paused=not AUTO_START,
        )
        if ok:
            logger.debug(
                f"Successfully added torrent: {title} to category: {category}"
            )
            return True
        logger.error(f"Failed to add torrent '{title}'")
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

def initialize_proxy_helper(proxy_override):
    """Initialize global proxy pool and proxy helper."""
    global global_proxy_pool, global_proxy_helper
    
    if proxy_override is False:
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
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Proxy pool initialized successfully")
        elif PROXY_MODE == 'single':
            logger.info(f"Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
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
    import atexit
    from packages.python.javdb_platform.db import close_db
    atexit.register(close_db)

    args = parse_arguments()
    mode = args.mode
    proxy_override = resolve_proxy_override(args.use_proxy, args.no_proxy)
    use_proxy = proxy_override
    proxy_active = should_proxy_module('qbittorrent', proxy_override, PROXY_MODULES, proxy_mode=PROXY_MODE)
    category_override = args.category
    logger.info("Starting qBittorrent uploader...")
    if category_override:
        logger.info(f"Using custom category: {category_override}")
    
    # Initialize proxy helper
    initialize_proxy_helper(proxy_override)
    
    logger.info(f"Proxy policy for qBittorrent: {describe_proxy_override(proxy_override)}")

    if proxy_active:
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
    else:
        logger.info("Proxy disabled for qBittorrent requests")
    
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
        
        success = add_torrent_to_qbittorrent(session, torrent['magnet'], torrent['title'], mode, use_proxy, category_override)
        
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

    # Save uploader stats to SQLite if session_id provided
    _session_id = getattr(args, 'session_id', None)
    if _session_id:
        try:
            from packages.python.javdb_platform.config_helper import use_sqlite as _use_sqlite
            if _use_sqlite():
                from packages.python.javdb_platform.db import init_db, db_save_uploader_stats
                init_db()
                _rate = (successfully_added / attempted * 100) if attempted > 0 else 0.0
                db_save_uploader_stats(_session_id, {
                    'total_torrents': total_torrents,
                    'duplicate_count': duplicate_count,
                    'attempted': attempted,
                    'successfully_added': successfully_added,
                    'failed_count': failed_count,
                    'hacked_sub': hacked_subtitle_count,
                    'hacked_nosub': hacked_no_subtitle_count,
                    'subtitle_count': subtitle_count,
                    'no_subtitle_count': no_subtitle_count,
                    'success_rate': _rate,
                })
                logger.info(f"Uploader stats saved to SQLite (session_id={_session_id})")
        except Exception as e:
            logger.warning(f"Failed to save uploader stats to SQLite: {e}")

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
