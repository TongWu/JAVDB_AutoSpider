import requests
import csv
import time
import re
import logging
import os
import argparse
import sys
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from bs4.element import Tag
from urllib.parse import urljoin, urlparse, quote
from datetime import datetime

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Import utility functions
from utils.history_manager import load_parsed_movies_history, save_parsed_movie_to_history, should_process_movie, \
    determine_torrent_types, get_missing_torrent_types, validate_history_file, has_complete_subtitles
from utils.parser import parse_index, parse_detail
from utils.magnet_extractor import extract_magnets
from utils.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials
from utils.path_helper import get_dated_report_path, ensure_dated_dir, get_dated_subdir

# Import unified configuration
try:
    from config import (
        BASE_URL, START_PAGE, END_PAGE,
        REPORTS_DIR, DAILY_REPORT_DIR, AD_HOC_DIR, PARSED_MOVIES_CSV,
        SPIDER_LOG_FILE, LOG_LEVEL, DETAIL_PAGE_SLEEP, PAGE_SLEEP, MOVIE_SLEEP,
        JAVDB_SESSION_COOKIE, PHASE2_MIN_RATE, PHASE2_MIN_COMMENTS,
        PROXY_HTTP, PROXY_HTTPS, PROXY_MODULES,
        CF_TURNSTILE_COOLDOWN, PHASE_TRANSITION_COOLDOWN, FALLBACK_COOLDOWN,
        GIT_USERNAME, GIT_PASSWORD, GIT_REPO_URL, GIT_BRANCH
    )
except ImportError:
    # Fallback values if config.py doesn't exist
    BASE_URL = 'https://javdb.com'
    START_PAGE = 1
    END_PAGE = 20
    REPORTS_DIR = 'reports'
    DAILY_REPORT_DIR = 'reports/DailyReport'
    AD_HOC_DIR = 'reports/AdHoc'
    PARSED_MOVIES_CSV = 'parsed_movies_history.csv'
    SPIDER_LOG_FILE = 'logs/spider.log'
    LOG_LEVEL = 'INFO'
    DETAIL_PAGE_SLEEP = 5
    PAGE_SLEEP = 2
    MOVIE_SLEEP = 1
    JAVDB_SESSION_COOKIE = None
    PHASE2_MIN_RATE = 4.0
    PHASE2_MIN_COMMENTS = 100
    PROXY_HTTP = None
    PROXY_HTTPS = None
    PROXY_MODULES = ['all']
    CF_TURNSTILE_COOLDOWN = 10
    PHASE_TRANSITION_COOLDOWN = 30
    FALLBACK_COOLDOWN = 30
    GIT_USERNAME = 'github-actions'
    GIT_PASSWORD = ''
    GIT_REPO_URL = ''
    GIT_BRANCH = 'main'

# Import CloudFlare bypass configuration (with fallback)
try:
    from config import CF_BYPASS_SERVICE_PORT, CF_BYPASS_ENABLED
except ImportError:
    CF_BYPASS_SERVICE_PORT = 8000
    CF_BYPASS_ENABLED = True

# Import proxy pool configuration (with fallback)
try:
    from config import PROXY_MODE, PROXY_POOL, PROXY_POOL_COOLDOWN_SECONDS, PROXY_POOL_MAX_FAILURES
except ImportError:
    PROXY_MODE = 'single'
    PROXY_POOL = []
    PROXY_POOL_COOLDOWN_SECONDS = 691200  # 8 days (691200 seconds)
    PROXY_POOL_MAX_FAILURES = 3

# Import GPT API configuration for login (optional)
try:
    from config import GPT_API_KEY, GPT_API_URL
    LOGIN_FEATURE_AVAILABLE = bool(GPT_API_KEY and GPT_API_URL)
except ImportError:
    GPT_API_KEY = None
    GPT_API_URL = None
    LOGIN_FEATURE_AVAILABLE = False

# Import JavDB login credentials (optional)
try:
    from config import JAVDB_USERNAME, JAVDB_PASSWORD
except ImportError:
    JAVDB_USERNAME = None
    JAVDB_PASSWORD = None

# Configure logging
from utils.logging_config import setup_logging, get_logger
setup_logging(SPIDER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Import masking utilities
from utils.masking import mask_ip_address, mask_username, mask_full, mask_proxy_url

# Import proxy pool
from utils.proxy_pool import ProxyPool, create_proxy_pool_from_config

# Import unified request handler
from utils.request_handler import RequestHandler, RequestConfig, create_request_handler_from_config

# Global proxy pool instance (will be initialized in main)
global_proxy_pool: Optional[ProxyPool] = None

# Global request handler instance (will be initialized in main)
global_request_handler: Optional[RequestHandler] = None

# Global set to track parsed links
parsed_links = set()

# Global list to track saved proxy ban HTML files for email notification
proxy_ban_html_files = []

# Global flag to track if login has been attempted (only allow once per run)
login_attempted = False

# Global variable to store refreshed session cookie
refreshed_session_cookie = None

# Generate output CSV filename
OUTPUT_CSV = f'Javdb_TodayTitle_{datetime.now().strftime("%Y%m%d")}.csv'


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='JavDB Spider - Extract torrent links from javdb.com')

    parser.add_argument('--dry-run', action='store_true',
                        help='Print items that would be written without changing CSV file')

    parser.add_argument('--output-file', type=str,
                        help='Specify output CSV file name (without changing directory)')

    parser.add_argument('--start-page', type=int, default=START_PAGE,
                        help=f'Starting page number (default: {START_PAGE})')

    parser.add_argument('--end-page', type=int, default=END_PAGE,
                        help=f'Ending page number (default: {END_PAGE})')

    parser.add_argument('--all', action='store_true',
                        help='Parse all pages until an empty page is found (ignores --end-page)')

    parser.add_argument('--ignore-history', action='store_true',
                        help='Ignore history file for reading (scrape all pages) but still save to history')

    parser.add_argument('--use-history', action='store_true',
                        help='Enable history filter for ad-hoc mode (by default, ad-hoc mode ignores history for reading)')

    parser.add_argument('--url', type=str,
                        help='Custom URL to scrape (add ?page=x for pages)')

    parser.add_argument('--phase', choices=['1', '2', 'all'], default='all',
                        help='Which phase to run: 1 (subtitle+today), 2 (today only), all (default)')

    parser.add_argument('--ignore-release-date', action='store_true',
                        help='Ignore today/yesterday tags and download all entries matching phase criteria (subtitle for phase1, quality for phase2)')

    parser.add_argument('--use-proxy', action='store_true',
                        help='Enable proxy for all HTTP requests (proxy settings from config.py)')

    parser.add_argument('--use-cf-bypass', action='store_true',
                        help='Use CloudFlare5sBypass service to get cf_clearance cookie (service must be running)')

    parser.add_argument('--from-pipeline', action='store_true',
                        help='Running from pipeline.py - use GIT_USERNAME for commits')

    return parser.parse_args()


def ensure_reports_dir():
    """Ensure the reports root directory exists (for history files)"""
    if not os.path.exists(REPORTS_DIR):
        os.makedirs(REPORTS_DIR)
        logger.info(f"Created directory: {REPORTS_DIR}")


def ensure_report_dated_dir(base_dir):
    """
    Ensure the dated subdirectory (YYYY/MM) exists for report files.
    
    Args:
        base_dir: Base directory (DAILY_REPORT_DIR or AD_HOC_DIR)
    
    Returns:
        Path to the dated subdirectory
    """
    dated_dir = ensure_dated_dir(base_dir)
    logger.info(f"Using dated directory: {dated_dir}")
    return dated_dir


def save_proxy_ban_html(html_content, proxy_name, page_num):
    """
    Save the HTML content that caused a proxy to be banned.
    This helps with debugging proxy ban issues.
    
    Args:
        html_content: The HTML content from the failed request
        proxy_name: Name of the proxy being banned
        page_num: Page number where the failure occurred
    
    Returns:
        str: Path to the saved file, or None if failed
    """
    # Note: proxy_ban_html_files is a module-level list, no need for 'global'
    # since we're only appending to it, not reassigning it
    
    if not html_content:
        logger.warning(f"No HTML content to save for banned proxy {proxy_name}")
        return None
    
    try:
        # Create logs directory if not exists
        logs_dir = 'logs'
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # Sanitize proxy name for filename (remove special characters)
        safe_proxy_name = re.sub(r'[^\w\-]', '_', proxy_name)
        filename = f"proxy_ban_{safe_proxy_name}_page{page_num}_{timestamp}.txt"
        filepath = os.path.join(logs_dir, filename)
        
        # Write HTML content to file
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# Proxy Ban HTML Capture\n")
            f.write(f"# Proxy: {proxy_name}\n")
            f.write(f"# Page: {page_num}\n")
            f.write(f"# Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# HTML Length: {len(html_content)} bytes\n")
            f.write(f"{'=' * 60}\n\n")
            f.write(html_content)
        
        logger.info(f"Saved proxy ban HTML to: {filepath}")
        proxy_ban_html_files.append(filepath)
        
        # Also output the path for downstream scripts to capture
        print(f"PROXY_BAN_HTML={filepath}")
        
        return filepath
        
    except Exception as e:
        logger.error(f"Failed to save proxy ban HTML: {e}")
        return None


# Legacy wrapper functions - now delegated to RequestHandler
# These functions are kept for backward compatibility but internally use the global_request_handler

def should_use_proxy_for_module(module_name: str, use_proxy_flag: bool) -> bool:
    """
    Check if a specific module should use proxy based on configuration.
    Delegated to global_request_handler.
    """
    if global_request_handler:
        return global_request_handler.should_use_proxy_for_module(module_name, use_proxy_flag)
    # Fallback if handler not initialized
    if not use_proxy_flag:
        return False
    if not PROXY_MODULES:
        return False
    if 'all' in PROXY_MODULES:
        return True
    return module_name in PROXY_MODULES


def extract_ip_from_proxy_url(proxy_url: str) -> Optional[str]:
    """
    Extract IP address or hostname from a proxy URL.
    Delegated to RequestHandler static method.
    """
    return RequestHandler.extract_ip_from_proxy_url(proxy_url)


def get_cf_bypass_service_url(proxy_ip: Optional[str] = None) -> str:
    """
    Get the CF bypass service URL based on proxy configuration.
    Delegated to global_request_handler.
    """
    if global_request_handler:
        return global_request_handler.get_cf_bypass_service_url(proxy_ip)
    # Fallback if handler not initialized
    if proxy_ip:
        return f"http://{proxy_ip}:{CF_BYPASS_SERVICE_PORT}"
    else:
        return f"http://127.0.0.1:{CF_BYPASS_SERVICE_PORT}"


def is_cf_bypass_failure(html_content: str) -> bool:
    """
    Check if the CF bypass response indicates a failure.
    Delegated to RequestHandler static method.
    """
    return RequestHandler.is_cf_bypass_failure(html_content)


def get_page(url, session=None, use_cookie=False, use_proxy=False, module_name='unknown', max_retries=3, use_cf_bypass=False):
    """
    Fetch a webpage with proper headers, age verification bypass, and proxy pool support.
    
    This function delegates to the global_request_handler for actual request handling.
    
    Mode combinations:
    - --use-proxy only: Use proxy to access website directly (no bypass)
    - --use-cf-bypass only: Use local CF bypass service (http://127.0.0.1:8000/html?url=...)
    - --use-proxy --use-cf-bypass: Use proxy's CF bypass service (http://{proxy_ip}:8000/html?url=...)
    
    CF Bypass failure detection: HTML size < 1000 bytes AND contains 'fail' keyword
    
    Retry sequence on CF bypass failure:
      a. Retry current method (bypass)
      b. Without bypass, use current proxy
      c. Switch to another proxy, without bypass
      d. Use bypass with new proxy
    
    Service repository: https://github.com/sarperavci/CloudflareBypassForScraping
    
    Args:
        url: URL to fetch
        session: requests.Session object for connection reuse
        use_cookie: Whether to add session cookie
        use_proxy: Whether --use-proxy flag is enabled
        module_name: Module name for proxy control ('spider', 'qbittorrent', 'pikpak', etc.)
        max_retries: Maximum number of retries with different proxies (only for proxy pool mode)
        use_cf_bypass: Whether to use CF bypass service
        
    Returns:
        HTML content as string, or None if failed
    """
    if global_request_handler is None:
        logger.error("Request handler not initialized. Call initialize_request_handler() first.")
        return None
    
    return global_request_handler.get_page(
        url=url,
        session=session,
        use_cookie=use_cookie,
        use_proxy=use_proxy,
        module_name=module_name,
        max_retries=max_retries,
        use_cf_bypass=use_cf_bypass
    )


def initialize_request_handler():
    """
    Initialize the global request handler with configuration from config.py.
    This should be called after proxy_pool is initialized in main().
    """
    global global_request_handler
    
    config = RequestConfig(
        base_url=BASE_URL,
        cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
        cf_bypass_enabled=CF_BYPASS_ENABLED,
        cf_bypass_max_failures=3,
        cf_turnstile_cooldown=CF_TURNSTILE_COOLDOWN,
        fallback_cooldown=FALLBACK_COOLDOWN,
        javdb_session_cookie=JAVDB_SESSION_COOKIE,
        proxy_http=PROXY_HTTP,
        proxy_https=PROXY_HTTPS,
        proxy_modules=PROXY_MODULES,
        proxy_mode=PROXY_MODE
    )
    
    global_request_handler = RequestHandler(proxy_pool=global_proxy_pool, config=config)
    logger.info("Request handler initialized successfully")


def attempt_login_refresh():
    """
    Attempt to refresh session cookie by logging in via login.py.
    This function can only be called ONCE per spider run.
    
    After successful login, login.py updates config.py with the new cookie,
    then we reload config.py to get the updated cookie value.
    
    Note: If spider is using proxy, login will also use the same proxy.
    
    Returns:
        tuple: (success: bool, new_cookie: str or None)
    """
    global login_attempted, refreshed_session_cookie
    
    # Check if login has already been attempted
    if login_attempted:
        logger.debug("Login already attempted in this run, skipping")
        return False, None
    
    # Mark login as attempted (even if it fails, we don't retry)
    login_attempted = True
    
    # Check if login feature is available
    if not LOGIN_FEATURE_AVAILABLE:
        logger.warning("Login feature not available (GPT_API_KEY/GPT_API_URL not configured)")
        return False, None
    
    # Check if credentials are available
    if not JAVDB_USERNAME or not JAVDB_PASSWORD:
        logger.warning("Login credentials not configured (JAVDB_USERNAME/JAVDB_PASSWORD)")
        return False, None
    
    logger.info("=" * 60)
    logger.info("ATTEMPTING SESSION COOKIE REFRESH VIA LOGIN")
    logger.info("=" * 60)
    
    # Get current proxy configuration if proxy pool is available
    login_proxies = None
    if global_proxy_pool is not None:
        current_proxy = global_proxy_pool.get_current_proxy()
        if current_proxy:
            login_proxies = {
                'http': current_proxy.get('http'),
                'https': current_proxy.get('https')
            }
            # Remove None values
            login_proxies = {k: v for k, v in login_proxies.items() if v}
            if login_proxies:
                logger.info(f"Login will use proxy: {global_proxy_pool.get_current_proxy_name()}")
            else:
                login_proxies = None
    
    try:
        # Import login functions from login.py
        from scripts.login import login_with_retry, update_config_file
        
        # Perform login with retry logic (max 10 attempts), passing proxy config
        success, session_cookie, message = login_with_retry(JAVDB_USERNAME, JAVDB_PASSWORD, max_retries=10, proxies=login_proxies)
        
        if success and session_cookie:
            logger.info(f"✓ Login successful, new session cookie obtained")
            logger.info(f"  Cookie: {session_cookie[:10]}***{session_cookie[-10:]}")
            
            # Update config.py with new cookie
            if update_config_file(session_cookie):
                logger.info("✓ Updated config.py with new session cookie")
                
                # Reload config.py to get the updated cookie value
                import importlib
                import config
                importlib.reload(config)
                
                # Get the refreshed cookie from reloaded config
                new_cookie = getattr(config, 'JAVDB_SESSION_COOKIE', session_cookie)
                refreshed_session_cookie = new_cookie
                logger.info(f"✓ Reloaded config.py, cookie: {new_cookie[:10]}***{new_cookie[-10:]}")
                
                # Update global request handler with new cookie from config
                if global_request_handler:
                    global_request_handler.config.javdb_session_cookie = new_cookie
                    logger.info("✓ Updated request handler with new session cookie")
                
                logger.info("=" * 60)
                return True, new_cookie
            else:
                # Failed to update config.py, use the cookie directly
                logger.warning("Failed to update config.py, using cookie directly for this run")
                refreshed_session_cookie = session_cookie
                
                # Update global request handler with the returned cookie
                if global_request_handler:
                    global_request_handler.config.javdb_session_cookie = session_cookie
                    logger.info("✓ Updated request handler with new session cookie")
                
                logger.info("=" * 60)
                return True, session_cookie
        else:
            logger.error(f"✗ Login failed: {message}")
            logger.info("=" * 60)
            return False, None
            
    except ImportError as e:
        logger.error(f"Failed to import login module: {e}")
        return False, None
    except Exception as e:
        logger.error(f"Unexpected error during login: {e}")
        return False, None


def can_attempt_login(is_adhoc_mode: bool, is_index_page: bool = False) -> bool:
    """
    Check if login attempt is allowed based on mode and context.
    
    Args:
        is_adhoc_mode: True if running in adhoc mode
        is_index_page: True if this is for an index page fetch
    
    Returns:
        bool: True if login attempt is allowed
    """
    # Already attempted - never allow again
    if login_attempted:
        return False
    
    # Login feature not available
    if not LOGIN_FEATURE_AVAILABLE:
        return False
    
    # Daily mode: only allow for movie (detail) pages, not index pages
    if not is_adhoc_mode:
        if is_index_page:
            return False
        return True
    
    # Adhoc mode: allow for both index and movie pages
    return True


# ============================================================
# Adhoc URL Magnet Filter Functions
# ============================================================

def has_magnet_filter(url):
    """
    Check if URL already has a magnet/download filter.
    
    Different URL types use different filter parameters:
    - actors: t=d or t=c (download/subtitle filter)
    - makers/video_codes: f=download
    
    Args:
        url: The URL to check
    
    Returns:
        bool: True if URL already has magnet filter, False otherwise
    """
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return False
        
        # Parse query parameters
        from urllib.parse import parse_qs
        params = parse_qs(parsed.query)
        
        # Determine URL type
        path = parsed.path.strip('/')
        
        if path.startswith('actors/'):
            # For actors: check 't' parameter for 'd' or 'c'
            if 't' not in params:
                return False
            t_values = params['t']
            for t_val in t_values:
                # t can be comma-separated like "312,d"
                parts = t_val.split(',')
                if 'd' in parts or 'c' in parts:
                    return True
            return False
        
        elif path.startswith('makers/') or path.startswith('video_codes/'):
            # For makers/video_codes: check 'f' parameter for 'download'
            if 'f' not in params:
                return False
            f_values = params['f']
            for f_val in f_values:
                if f_val == 'download':
                    return True
            return False
        
        else:
            # Unknown URL type, no filter check needed
            return False
        
    except Exception as e:
        logger.debug(f"Error checking magnet filter in URL {url}: {e}")
        return False


def add_magnet_filter_to_url(url):
    """
    Add magnet filter to URL for adhoc mode based on URL type.
    
    Different URL types use different filter parameters:
    - actors: t=d (or append ,d to existing t value)
    - makers/video_codes: f=download
    
    Rules for actors (t parameter):
    1. If URL has no 't' param: add ?t=d or &t=d
    2. If URL has t=d or t=c: return URL unchanged (already has filter)
    3. If URL has t=<other> (e.g. t=312): change to t=<other>,d (e.g. t=312,d)
    
    Rules for makers/video_codes (f parameter):
    1. If URL has no 'f' param: add ?f=download or &f=download
    2. If URL has f=download: return URL unchanged
    
    Args:
        url: The original URL
    
    Returns:
        str: URL with magnet filter added, or original if already has filter
    """
    try:
        # If already has magnet filter, return unchanged
        if has_magnet_filter(url):
            logger.debug(f"URL already has magnet filter: {url}")
            return url
        
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        
        # Determine URL type and appropriate filter
        if path.startswith('actors/'):
            # For actors: use t=d filter
            return _add_actors_filter(url, parsed)
        elif path.startswith('makers/') or path.startswith('video_codes/'):
            # For makers/video_codes: use f=download filter
            return _add_download_filter(url, parsed)
        else:
            # Unknown URL type, return unchanged
            logger.debug(f"Unknown URL type, not adding filter: {url}")
            return url
            
    except Exception as e:
        logger.warning(f"Error adding magnet filter to URL {url}: {e}")
        return url


def _add_actors_filter(url, parsed):
    """
    Add t=d filter for actors URLs.
    
    Args:
        url: The original URL
        parsed: ParseResult from urlparse
    
    Returns:
        str: URL with t=d filter added
    """
    from urllib.parse import parse_qs, urlencode, urlunparse
    
    # If no query string, simply add ?t=d
    if not parsed.query:
        # Handle edge case: URL ends with '?' but has no query params
        base_url = url.rstrip('?')
        return f"{base_url}?t=d"
    
    params = parse_qs(parsed.query, keep_blank_values=True)
    
    if 't' not in params:
        # No 't' parameter, add t=d to existing query
        # Handle edge case: URL might end with '&' or have trailing '?'
        base_url = url.rstrip('&')
        return f"{base_url}&t=d"
    else:
        # Has 't' parameter with other value (e.g. t=312), append ,d
        t_values = params['t']
        new_t_values = []
        for t_val in t_values:
            parts = t_val.split(',')
            if 'd' not in parts and 'c' not in parts:
                new_t_values.append(f"{t_val},d")
            else:
                new_t_values.append(t_val)
        
        params['t'] = new_t_values
        
        # Rebuild URL
        flat_params = []
        for key, values in params.items():
            for val in values:
                flat_params.append((key, val))
        
        new_query = urlencode(flat_params, safe=',')
        
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))


def _add_download_filter(url, parsed):
    """
    Add f=download filter for makers/video_codes URLs.
    
    Args:
        url: The original URL
        parsed: ParseResult from urlparse
    
    Returns:
        str: URL with f=download filter added
    """
    from urllib.parse import parse_qs
    
    # If no query string, simply add ?f=download
    if not parsed.query:
        # Handle edge case: URL ends with '?' but has no query params
        base_url = url.rstrip('?')
        return f"{base_url}?f=download"
    
    params = parse_qs(parsed.query, keep_blank_values=True)
    
    if 'f' not in params:
        # No 'f' parameter, add f=download to existing query
        # Handle edge case: URL might end with '&'
        base_url = url.rstrip('&')
        return f"{base_url}&f=download"
    else:
        # 'f' parameter exists but is not 'download', we should still add it
        # Actually, if we reach here, has_magnet_filter returned False,
        # meaning f is not 'download', so we need to handle this case
        # For simplicity, just append &f=download (the server will use the last value)
        # Or we can replace it - let's replace it for cleaner URLs
        from urllib.parse import urlencode, urlunparse
        
        params['f'] = ['download']
        
        flat_params = []
        for key, values in params.items():
            for val in values:
                flat_params.append((key, val))
        
        new_query = urlencode(flat_params)
        
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))


def get_page_url(page_num, phase=1, custom_url=None):
    """Generate URL for a specific page number and phase"""
    if custom_url:
        # If custom URL is provided, just add page parameter
        if page_num == 1:
            return custom_url
        else:
            separator = '&' if '?' in custom_url else '?'
            return f"{custom_url}{separator}page={page_num}"

    if BASE_URL.endswith('.com'):
        return f'{BASE_URL}/?page={page_num}'
    else:
        return f'{BASE_URL}&page={page_num}'


def fetch_index_page_with_fallback(page_url, session, use_cookie, use_proxy, use_cf_bypass, page_num, is_adhoc_mode=False):
    """
    Fetch index page with smart multi-level fallback mechanism.
    
    Fallback Hierarchy:
    1. Initial Attempt: Use provided settings (e.g. No Proxy, No CF).
    2. Local CF Fallback: If Direct failed, try Local CF Bypass.
    3. Proxy Pool Iteration: If Local failed (IP banned?), iterate through proxies.
       For each proxy:
       a. Try Direct Proxy (No CF)
       b. Try Proxy + CF Bypass
       c. If both fail, mark proxy as BANNED and switch to next (unless in ad hoc mode).
    
    Args:
        page_url: URL to fetch
        session: requests.Session object
        use_cookie: Whether to use session cookie
        use_proxy: Whether proxy is currently enabled
        use_cf_bypass: Whether CF bypass is currently enabled
        page_num: Current page number (for logging)
        is_adhoc_mode: If True, don't mark proxies as banned on failure (for custom URLs)
    
    Returns:
        tuple: (html_content, has_movie_list, proxy_was_banned, effective_use_proxy, effective_use_cf_bypass, is_valid_empty_page)
            - html_content: The HTML content (None if failed)
            - has_movie_list: True if movie list found
            - proxy_was_banned: True if a proxy was banned during fetch
            - effective_use_proxy: The proxy setting that eventually worked
            - effective_use_cf_bypass: The CF bypass setting that eventually worked
            - is_valid_empty_page: True if page is valid but has no content (e.g. "No content yet")
    """
    proxy_was_banned = False
    last_failed_html = None  # Store HTML from failed attempts
    
    # --- Helper function to attempt fetch and validate ---
    # Returns: (html, has_movie_list, is_valid_empty_page)
    def try_fetch(u_proxy, u_cf, context_msg):
        nonlocal last_failed_html
        logger.debug(f"[Page {page_num}] {context_msg}...")
        try:
            html = get_page(page_url, session, use_cookie=use_cookie,
                            use_proxy=u_proxy, module_name='spider',
                            use_cf_bypass=u_cf)
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                # Use flexible matching - look for any div with 'movie-list' in class
                # Different pages may have different class combinations:
                # - Normal pages: 'movie-list h cols-4 vcols-8'  
                # - Rankings pages: 'movie-list h cols-4'
                movie_list = soup.find('div', class_=lambda x: x and 'movie-list' in x)
                if movie_list:
                    # Check if movie-list has actual movie items
                    # An empty movie-list div (e.g., page=8 of rankings with only 250 items total)
                    # should be treated as a valid empty page, not a successful fetch
                    movie_items = movie_list.find_all('div', class_='item')
                    if len(movie_items) > 0:
                        logger.debug(f"[Page {page_num}] Success: {context_msg} - Found {len(movie_items)} movie items")
                        return html, True, False
                    else:
                        # movie-list exists but is empty - this is a valid empty page
                        logger.info(f"[Page {page_num}] movie-list exists but is empty (0 items) - treating as valid empty page")
                        return html, False, True
                else:
                    # No movie list - check if this is a valid empty page or a failed fetch
                    page_text = soup.get_text()
                    title = soup.find('title')
                    title_text = title.text.strip() if title else ""
                    age_modal = soup.find('div', class_='modal is-active over18-modal')
                    
                    # Check if it's a login page
                    is_login_page = '登入' in title_text or 'login' in title_text.lower()
                    
                    # Check if it's a valid empty results page
                    # Various indicators that the page exists but has no movies:
                    # 1. Check for empty-message div (e.g. <div class="empty-message">暫無內容</div>)
                    empty_message_div = soup.find('div', class_='empty-message')
                    # 2. Check for text patterns in various languages
                    has_no_content_msg = (
                        'No content yet' in page_text or 
                        'No result' in page_text or
                        '暫無內容' in page_text or
                        '暂无内容' in page_text or
                        empty_message_div is not None
                    )
                    
                    # If empty-message div is found, this is a valid empty page regardless of age_modal
                    # (age modal is just an overlay, doesn't affect page content validity)
                    if not is_login_page and empty_message_div is not None:
                        empty_msg_text = empty_message_div.get_text().strip()
                        logger.info(f"[Page {page_num}] Page exists but has no content (empty-message: '{empty_msg_text}')")
                        # This is a valid empty page - no need to retry
                        return html, False, True
                    elif not is_login_page and not age_modal and has_no_content_msg:
                        # Fallback for text-based detection (No content yet, etc.)
                        logger.info(f"[Page {page_num}] Page exists but has no content (text pattern detected)")
                        return html, False, True
                    elif not is_login_page and not age_modal and len(html) > 20000:
                        # Large HTML but no movie list - might be a valid page, treat as empty
                        logger.debug(f"[Page {page_num}] Large HTML without movie list, treating as empty page")
                        return html, False, True
                    else:
                        # Fetched HTML but validation failed (login page or age modal)
                        last_failed_html = html
                        logger.debug(f"[Page {page_num}] Validation failed (no movie list, login={is_login_page}, age_modal={age_modal is not None}): {context_msg}")
        except Exception as e:
            logger.debug(f"[Page {page_num}] Failed {context_msg}: {e}")
        return None, False, False

    # --- Phase 0: Initial Attempt (User Config) ---
    current_proxy_name = global_proxy_pool.get_current_proxy_name() if (use_proxy and global_proxy_pool) else "None"
    html, success, is_valid_empty = try_fetch(use_proxy, use_cf_bypass, 
                              f"Initial attempt (Proxy={use_proxy}, CF={use_cf_bypass}, Node={current_proxy_name})")
    if success:
        return html, True, False, use_proxy, use_cf_bypass, False
    if is_valid_empty:
        # Page fetched successfully but has no content - this is a valid stopping point
        return html, False, False, use_proxy, use_cf_bypass, True

    logger.warning(f"[Page {page_num}] Initial attempt failed. Starting smart fallback mechanism...")

    # --- Phase 1: Local CF Fallback (Only if we started with No Proxy & No CF) ---
    # Disabled by default: Don't automatically try CF Bypass if user didn't request it (avoids localhost connection errors)
    # The user must explicitly provide --use-cf-bypass to enable this feature
    if not use_proxy and not use_cf_bypass:
        logger.debug(f"[Page {page_num}] Skipping automatic Local CF Bypass fallback (flag not set). Switching to Proxy Pool...")
        # html, success, is_valid_empty = try_fetch(False, True, "Fallback Phase 1: Local CF Bypass (No Proxy)")
        # if success:
        #     logger.info(f"[Page {page_num}] Local CF Bypass succeeded. Switching mode to: use_cf_bypass=True")
        #     return html, True, False, False, True, False
        # if is_valid_empty:
        #     return html, False, False, False, True, True
        # logger.warning(f"[Page {page_num}] Local CF Bypass failed. Assuming local IP banned. Switching to Proxy Pool...")

    # --- Phase 1.5: Login Refresh Fallback (adhoc mode only, before switching proxies) ---
    # Try to refresh session cookie via login if in adhoc mode
    if is_adhoc_mode and can_attempt_login(is_adhoc_mode, is_index_page=True):
        logger.info(f"[Page {page_num}] Attempting login refresh before switching proxies...")
        login_success, new_cookie = attempt_login_refresh()
        if login_success:
            # Retry with new cookie
            html, success, is_valid_empty = try_fetch(use_proxy, use_cf_bypass, 
                f"Fallback: Retry with refreshed cookie")
            if success:
                logger.info(f"[Page {page_num}] Login refresh succeeded! Index page fetched successfully.")
                return html, True, False, use_proxy, use_cf_bypass, False
            if is_valid_empty:
                return html, False, False, use_proxy, use_cf_bypass, True
            logger.warning(f"[Page {page_num}] Login refresh completed but index page still failed")
        else:
            logger.warning(f"[Page {page_num}] Login refresh failed, continuing with proxy pool fallback...")

    # --- Phase 2: Proxy Pool Iteration ---
    if global_proxy_pool is None:
        logger.error(f"[Page {page_num}] Fallback failed: No proxy pool configured")
        # Return last failed HTML if we have it, otherwise None
        return last_failed_html, False, False, use_proxy, use_cf_bypass, False

    # If we weren't using proxy, start using it now
    if not use_proxy:
        # If we are just switching to proxy mode, ensure we start with a fresh/valid proxy if possible
        # (The current one might be random if we haven't used it yet)
        pass 

    # We will try up to N switches (coverage of the pool)
    # If using Single mode, we only have 1 try.
    max_switches = len(global_proxy_pool.proxies) if PROXY_MODE == 'pool' else 1
    # Limit max switches to avoid infinite loops if pool is huge, e.g. 10
    max_switches = min(max_switches, 10) 
    
    attempts = 0
    while attempts < max_switches:
        current_proxy_name = global_proxy_pool.get_current_proxy_name()
        
        # Sub-step 2.1: Try Direct Proxy (No CF)
        # User requested sequence: "依次是不使用cloudflare bypass和使用bypass"
        html, success, is_valid_empty = try_fetch(True, False, f"Fallback Phase 2: Proxy Direct (Node={current_proxy_name})")
        if success:
            logger.info(f"[Page {page_num}] Proxy Direct succeeded. Switching mode to: use_proxy=True, use_cf_bypass=False")
            return html, True, proxy_was_banned, True, False, False
        if is_valid_empty:
            logger.info(f"[Page {page_num}] Proxy Direct: valid empty page detected")
            return html, False, proxy_was_banned, True, False, True
            
        # Sub-step 2.2: Try Proxy + CF Bypass
        html, success, is_valid_empty = try_fetch(True, True, f"Fallback Phase 2: Proxy + CF Bypass (Node={current_proxy_name})")
        if success:
            logger.info(f"[Page {page_num}] Proxy + CF Bypass succeeded. Switching mode to: use_proxy=True, use_cf_bypass=True")
            return html, True, proxy_was_banned, True, True, False
        if is_valid_empty:
            logger.info(f"[Page {page_num}] Proxy + CF Bypass: valid empty page detected")
            return html, False, proxy_was_banned, True, True, True

        # If both failed for this proxy, handle based on mode
        attempts += 1
        if attempts < max_switches and PROXY_MODE == 'pool':
            if is_adhoc_mode:
                # Ad hoc mode: Don't mark as banned, just switch to next proxy
                # Failure might be due to page-specific issues (login required, page not found, etc.)
                logger.warning(f"[Page {page_num}] Proxy '{current_proxy_name}' failed both Direct and CF modes (Ad Hoc mode - not marking as banned)")
                logger.info(f"[Page {page_num}] Switching to next proxy...")
                global_proxy_pool.mark_failure_and_switch()  # Single failure mark, no ban
            else:
                # Normal mode: Mark as banned after multiple failures
                logger.warning(f"[Page {page_num}] Proxy '{current_proxy_name}' failed both Direct and CF modes. Marking BANNED and switching...")
                
                # Save the last failed HTML for debugging proxy ban issues
                if last_failed_html:
                    save_proxy_ban_html(last_failed_html, current_proxy_name, page_num)
                
                # Mark failure multiple times to trigger cooldown
                for _ in range(PROXY_POOL_MAX_FAILURES):
                    global_proxy_pool.mark_failure_and_switch()
                proxy_was_banned = True
        else:
            if PROXY_MODE == 'single':
                logger.error(f"[Page {page_num}] Single proxy mode failed. Cannot switch.")
            else:
                logger.error(f"[Page {page_num}] All proxy attempts exhausted.")
            break

    # Return the last HTML content we fetched (even if validation failed), or None if all fetches failed completely
    return last_failed_html, False, proxy_was_banned, use_proxy, use_cf_bypass, False


def fetch_detail_page_with_fallback(detail_url, session, use_cookie, use_proxy, use_cf_bypass, entry_index, is_adhoc_mode=False):
    """
    Fetch detail page with smart multi-level fallback mechanism.
    Similar to fetch_index_page_with_fallback, but validates using parse_detail success.
    
    Note: Unlike index page fallback, this function does NOT mark proxies as banned on failure,
    because detail page failures are often due to page-specific issues (login required, 
    page not found, etc.) rather than proxy problems.
    
    Fallback Hierarchy:
    1. Initial Attempt: Use provided settings (e.g. No Proxy, No CF).
    2. Proxy Pool Iteration: If initial failed, iterate through proxies.
       For each proxy:
       a. Try Direct Proxy (No CF)
       b. Try Proxy + CF Bypass
       c. If both fail, switch to next proxy (no ban marking).
    
    Args:
        detail_url: URL to fetch
        session: requests.Session object
        use_cookie: Whether to use session cookie
        use_proxy: Whether proxy is currently enabled
        use_cf_bypass: Whether CF bypass is currently enabled
        entry_index: Current entry index (for logging)
        is_adhoc_mode: Unused, kept for API compatibility
    
    Returns:
        tuple: (magnets, actor_info, parse_success, effective_use_proxy, effective_use_cf_bypass)
            - magnets: List of magnet link dictionaries
            - actor_info: Actor name string
            - parse_success: True if parsing was successful
            - effective_use_proxy: The proxy setting that eventually worked
            - effective_use_cf_bypass: The CF bypass setting that eventually worked
    """
    last_result = ([], '', False)  # Store result from failed attempts (magnets, actor_info, parse_success)
    
    # --- Helper function to attempt fetch, parse and validate ---
    def try_fetch_and_parse(u_proxy, u_cf, context_msg, skip_sleep=False):
        nonlocal last_result
        logger.debug(f"[{entry_index}] {context_msg}...")
        try:
            html = get_page(detail_url, session, use_cookie=use_cookie,
                            use_proxy=u_proxy, module_name='spider',
                            use_cf_bypass=u_cf)
            if html:
                # Parse detail page with skip_sleep for retry attempts
                # Note: video_code is already extracted from index page, not from detail page
                magnets, actor_info, parse_success = parse_detail(html, entry_index, skip_sleep=skip_sleep)
                
                if parse_success:
                    logger.debug(f"[{entry_index}] Success: {context_msg}")
                    return magnets, actor_info, True
                else:
                    # Fetched HTML but parsing failed (missing expected elements)
                    # Store it as potential return value if all fallbacks fail
                    last_result = (magnets, actor_info, False)
                    logger.debug(f"[{entry_index}] Parse validation failed (missing magnets): {context_msg}")
            else:
                logger.debug(f"[{entry_index}] Failed to fetch HTML: {context_msg}")
        except Exception as e:
            logger.debug(f"[{entry_index}] Failed {context_msg}: {e}")
        return [], '', False

    # --- Phase 0: Initial Attempt (User Config) ---
    current_proxy_name = global_proxy_pool.get_current_proxy_name() if (use_proxy and global_proxy_pool) else "None"
    magnets, actor_info, success = try_fetch_and_parse(
        use_proxy, use_cf_bypass, 
        f"Detail Initial attempt (Proxy={use_proxy}, CF={use_cf_bypass}, Node={current_proxy_name})",
        skip_sleep=False  # First attempt should respect sleep
    )
    if success:
        return magnets, actor_info, True, use_proxy, use_cf_bypass

    logger.warning(f"[{entry_index}] Detail page initial attempt failed. Starting smart fallback mechanism...")

    # --- Phase 1: Local CF Fallback (Only if we started with No Proxy & No CF) ---
    # Disabled by default: Don't automatically try CF Bypass if user didn't request it
    if not use_proxy and not use_cf_bypass:
        logger.debug(f"[{entry_index}] Skipping automatic Local CF Bypass fallback (flag not set). Switching to Proxy Pool...")

    # --- Phase 1.5: Login Refresh Fallback (before switching proxies) ---
    # Try to refresh session cookie via login if allowed
    if can_attempt_login(is_adhoc_mode, is_index_page=False):
        logger.info(f"[{entry_index}] Attempting login refresh before switching proxies...")
        login_success, new_cookie = attempt_login_refresh()
        if login_success:
            # Retry with new cookie
            magnets, actor_info, success = try_fetch_and_parse(
                use_proxy, use_cf_bypass,
                f"Detail Fallback: Retry with refreshed cookie",
                skip_sleep=True
            )
            if success:
                logger.info(f"[{entry_index}] Login refresh succeeded! Detail page fetched successfully.")
                return magnets, actor_info, True, use_proxy, use_cf_bypass
            else:
                logger.warning(f"[{entry_index}] Login refresh completed but detail page still failed")
        else:
            logger.warning(f"[{entry_index}] Login refresh failed, continuing with proxy pool fallback...")
    
    # --- Phase 2: Proxy Pool Iteration ---
    if global_proxy_pool is None:
        logger.error(f"[{entry_index}] Fallback failed: No proxy pool configured")
        # Return last result if we have it
        return last_result[0], last_result[1], last_result[2], use_proxy, use_cf_bypass

    # We will try up to N switches (coverage of the pool)
    # If using Single mode, we only have 1 try.
    max_switches = len(global_proxy_pool.proxies) if PROXY_MODE == 'pool' else 1
    # Limit max switches to avoid infinite loops if pool is huge, e.g. 10
    max_switches = min(max_switches, 10) 
    
    attempts = 0
    while attempts < max_switches:
        current_proxy_name = global_proxy_pool.get_current_proxy_name()
        
        # Sub-step 2.1: Try Direct Proxy (No CF)
        magnets, actor_info, success = try_fetch_and_parse(
            True, False, 
            f"Detail Fallback Phase 2: Proxy Direct (Node={current_proxy_name})",
            skip_sleep=True  # Skip sleep for retry attempts
        )
        if success:
            logger.info(f"[{entry_index}] Detail Proxy Direct succeeded. Switching mode to: use_proxy=True, use_cf_bypass=False")
            return magnets, actor_info, True, True, False
            
        # Sub-step 2.2: Try Proxy + CF Bypass
        magnets, actor_info, success = try_fetch_and_parse(
            True, True, 
            f"Detail Fallback Phase 2: Proxy + CF Bypass (Node={current_proxy_name})",
            skip_sleep=True  # Skip sleep for retry attempts
        )
        if success:
            logger.info(f"[{entry_index}] Detail Proxy + CF Bypass succeeded. Switching mode to: use_proxy=True, use_cf_bypass=True")
            return magnets, actor_info, True, True, True

        # If both failed for this proxy, just switch to next (no ban marking for detail pages)
        # Detail page failures are often due to page-specific issues, not proxy problems
        attempts += 1
        if attempts < max_switches and PROXY_MODE == 'pool':
            logger.debug(f"[{entry_index}] Proxy '{current_proxy_name}' failed both Direct and CF modes. Switching to next proxy (not marking as banned)...")
            global_proxy_pool.mark_failure_and_switch()  # Single failure mark, no ban
        else:
            if PROXY_MODE == 'single':
                logger.debug(f"[{entry_index}] Single proxy mode failed. Cannot switch.")
            else:
                logger.debug(f"[{entry_index}] All proxy attempts exhausted.")
            break

    # Return the last result we got (even if parsing failed), or empty if all fetches failed completely
    logger.warning(f"[{entry_index}] Detail page fallback exhausted. Returning best available result.")
    return last_result[0], last_result[1], last_result[2], use_proxy, use_cf_bypass


def merge_row_data(existing_row, new_row):
    """
    Merge new row data into existing row.
    
    Merge rules:
    - If existing has data AND new has data -> use new (overwrite)
    - If existing has data AND new is empty -> keep existing
    - If existing is empty AND new has data -> use new
    
    Args:
        existing_row: The existing row from CSV
        new_row: The new row to merge
    
    Returns:
        dict: Merged row data
    """
    merged = existing_row.copy()
    
    for key, new_value in new_row.items():
        existing_value = merged.get(key, '')
        
        # Convert to string for comparison (handle None values)
        new_str = str(new_value) if new_value is not None else ''
        existing_str = str(existing_value) if existing_value is not None else ''
        
        if new_str:
            # New has data - use it (overwrite or fill empty)
            merged[key] = new_value
        # else: keep existing value (new is empty)
    
    return merged


def write_csv(rows, csv_path, fieldnames, dry_run=False, append_mode=False):
    """
    Write results to CSV file or print if dry-run.
    
    When append_mode is True and file exists:
    - Reads existing data
    - Merges new rows with existing rows based on video_code
    - If video_code exists: merge data (new data takes priority, but keeps existing if new is empty)
    - If video_code is new: append as new row
    - Writes back the merged data
    
    Args:
        rows: List of row dictionaries to write
        csv_path: Path to the CSV file
        fieldnames: List of column names
        dry_run: If True, only log what would be written
        append_mode: If True, merge with existing file data
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would write {len(rows)} entries to {csv_path}")
        logger.info("[DRY RUN] Sample entries:")
        for i, row in enumerate(rows[:3]):  # Show first 3 entries
            logger.info(f"[DRY RUN] Entry {i + 1}: {row['video_code']} (Page {row['page']})")
        if len(rows) > 3:
            logger.info(f"[DRY RUN] ... and {len(rows) - 3} more entries")
        return

    # If append_mode and file exists, read existing data and merge
    if append_mode and os.path.exists(csv_path):
        existing_rows = {}  # Keyed by video_code for merge operations
        rows_without_key = []  # Preserve rows without video_code
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    video_code = row.get('video_code', '')
                    if video_code:
                        existing_rows[video_code] = row
                    else:
                        # Preserve rows without video_code (cannot merge, just keep them)
                        rows_without_key.append(row)
            if rows_without_key:
                logger.warning(f"[CSV] Found {len(rows_without_key)} existing rows without video_code - preserving them")
        except Exception as e:
            logger.warning(f"Error reading existing CSV file: {e}. Will create new file.")
            existing_rows = {}
            rows_without_key = []
        
        # Merge new rows with existing rows
        merged_count = 0
        added_count = 0
        for new_row in rows:
            video_code = new_row.get('video_code', '')
            if not video_code:
                # New row without video_code - cannot merge, append directly
                rows_without_key.append(new_row)
                added_count += 1
                logger.warning(f"[CSV] Added new entry without video_code (cannot merge)")
            elif video_code in existing_rows:
                # Merge with existing row
                existing_rows[video_code] = merge_row_data(existing_rows[video_code], new_row)
                merged_count += 1
                logger.debug(f"[CSV] Merged existing entry: {video_code}")
            else:
                # Add new row with video_code
                existing_rows[video_code] = new_row
                added_count += 1
                logger.debug(f"[CSV] Added new entry: {video_code}")
        
        # Write all data back to file (keyed rows first, then rows without key)
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in existing_rows.values():
                writer.writerow(row)
            for row in rows_without_key:
                writer.writerow(row)
        
        total_entries = len(existing_rows) + len(rows_without_key)
        if merged_count > 0 or added_count > 0:
            logger.info(f"[CSV] Updated {csv_path}: {merged_count} merged, {added_count} added, {total_entries} total entries")
    else:
        # No existing file or not in append mode - write new file
        logger.debug(f"[CSV] Writing new file: {csv_path}")
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        logger.info(f"[CSV] Created {csv_path} with {len(rows)} entries")


def should_include_torrent_in_csv(href, history_data, magnet_links):
    """Check if torrent categories should be included in CSV based on history"""
    if not history_data or href not in history_data:
        # New movie, include all found torrents
        return True

    history_torrent_types = history_data[href].get('torrent_types', [])
    current_torrent_types = determine_torrent_types(magnet_links)

    # Check if any current torrent types are not in history
    for torrent_type in current_torrent_types:
        if torrent_type not in history_torrent_types:
            return True

    # All current torrent types already exist in history
    return False


def create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links, history_data):
    """Create CSV row with torrent categories, marking downloaded ones with [DOWNLOADED PREVIOUSLY]"""
    if not history_data or href not in history_data:
        # New movie, apply preference rules to current magnet links
        row = {
            'href': href,
            'video_code': entry['video_code'],
            'page': page_num,
            'actor': actor_info,
            'rate': entry['rate'],
            'comment_number': entry['comment_number'],
            'hacked_subtitle': magnet_links['hacked_subtitle'],
            'hacked_no_subtitle': '',
            'subtitle': magnet_links['subtitle'],
            'no_subtitle': '',
            'size_hacked_subtitle': magnet_links['size_hacked_subtitle'],
            'size_hacked_no_subtitle': '',
            'size_subtitle': magnet_links['size_subtitle'],
            'size_no_subtitle': ''
        }

        # Apply preference rules for new movies
        # Rule 1: If subtitle is available, ignore no_subtitle
        if magnet_links['subtitle']:
            row['no_subtitle'] = ''
            row['size_no_subtitle'] = ''
        else:
            row['no_subtitle'] = magnet_links['no_subtitle']
            row['size_no_subtitle'] = magnet_links['size_no_subtitle']

        # Rule 2: If hacked_subtitle is available, ignore hacked_no_subtitle
        if magnet_links['hacked_subtitle']:
            row['hacked_no_subtitle'] = ''
            row['size_hacked_no_subtitle'] = ''
        else:
            row['hacked_no_subtitle'] = magnet_links['hacked_no_subtitle']
            row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']

        return row

    # Movie exists in history - check what torrent types are missing
    history_torrent_types = history_data[href].get('torrent_types', [])
    current_torrent_types = determine_torrent_types(magnet_links)
    
    # Get missing torrent types that should be added to CSV
    missing_types = get_missing_torrent_types(history_torrent_types, current_torrent_types)
    
    row = {
        'href': href,
        'video_code': entry['video_code'],
        'page': page_num,
        'actor': actor_info,
        'rate': entry['rate'],
        'comment_number': entry['comment_number'],
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
        'size_hacked_subtitle': '',
        'size_hacked_no_subtitle': '',
        'size_subtitle': '',
        'size_no_subtitle': ''
    }

    # Add torrents that are missing from history (normal magnet links)
    if 'hacked_subtitle' in missing_types and magnet_links['hacked_subtitle']:
        row['hacked_subtitle'] = magnet_links['hacked_subtitle']
        row['size_hacked_subtitle'] = magnet_links['size_hacked_subtitle']
        logger.debug(f"Adding missing hacked_subtitle torrent for {entry['video_code']}")
    
    if 'hacked_no_subtitle' in missing_types and magnet_links['hacked_no_subtitle']:
        row['hacked_no_subtitle'] = magnet_links['hacked_no_subtitle']
        row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
        logger.debug(f"Adding missing hacked_no_subtitle torrent for {entry['video_code']}")
    
    if 'subtitle' in missing_types and magnet_links['subtitle']:
        row['subtitle'] = magnet_links['subtitle']
        row['size_subtitle'] = magnet_links['size_subtitle']
        logger.debug(f"Adding missing subtitle torrent for {entry['video_code']}")
    
    if 'no_subtitle' in missing_types and magnet_links['no_subtitle']:
        row['no_subtitle'] = magnet_links['no_subtitle']
        row['size_no_subtitle'] = magnet_links['size_no_subtitle']
        logger.debug(f"Adding missing no_subtitle torrent for {entry['video_code']}")

    # Add [DOWNLOADED PREVIOUSLY] markers for torrent types that already exist in history
    if 'hacked_subtitle' in history_torrent_types and magnet_links['hacked_subtitle']:
        row['hacked_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_hacked_subtitle'] = magnet_links['size_hacked_subtitle']
        logger.debug(f"Marking hacked_subtitle as downloaded for {entry['video_code']}")
    
    if 'hacked_no_subtitle' in history_torrent_types and magnet_links['hacked_no_subtitle']:
        row['hacked_no_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
        logger.debug(f"Marking hacked_no_subtitle as downloaded for {entry['video_code']}")
    
    if 'subtitle' in history_torrent_types and magnet_links['subtitle']:
        row['subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_subtitle'] = magnet_links['size_subtitle']
        logger.debug(f"Marking subtitle as downloaded for {entry['video_code']}")
    
    if 'no_subtitle' in history_torrent_types and magnet_links['no_subtitle']:
        row['no_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_no_subtitle'] = magnet_links['size_no_subtitle']
        logger.debug(f"Marking no_subtitle as downloaded for {entry['video_code']}")

    return row


# ============================================================
# URL Type Detection and Page Name Extraction Functions
# ============================================================

def detect_url_type(url):
    """
    Detect the type of JavDB URL.
    
    Args:
        url: The JavDB URL (e.g., 'https://javdb.com/actors/bkxd')
    
    Returns:
        str: The URL type ('actors', 'makers', 'publishers', 'series', 'directors', 'video_codes', or 'unknown')
    """
    if not url or 'javdb.com' not in url:
        return 'unknown'
    
    try:
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        
        if path.startswith('actors/'):
            return 'actors'
        elif path.startswith('makers/'):
            return 'makers'
        elif path.startswith('publishers/'):
            return 'publishers'
        elif path.startswith('series/'):
            return 'series'
        elif path.startswith('directors/'):
            return 'directors'
        elif path.startswith('video_codes/'):
            return 'video_codes'
        else:
            return 'unknown'
    except Exception as e:
        logger.warning(f"Error detecting URL type for {url}: {e}")
        return 'unknown'


def extract_url_identifier(url):
    """
    Extract the identifier from a JavDB URL (e.g., 'bkxd' from '/actors/bkxd').
    
    Args:
        url: The JavDB URL
    
    Returns:
        str: The identifier part of the URL
    """
    try:
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        parts = path.split('/')
        if len(parts) >= 2:
            return parts[1]
    except Exception as e:
        logger.warning(f"Error extracting URL identifier from {url}: {e}")
    return None


def parse_actor_name_from_html(html_content):
    """
    Extract actor name from JavDB actor page HTML.
    
    Looks for:
    <span class="actor-section-name">森日向子</span>
    
    Args:
        html_content: HTML content of the actor page
    
    Returns:
        str: Actor name if found, None otherwise
    """
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        actor_span = soup.find('span', class_='actor-section-name')
        if actor_span:
            actor_name = actor_span.get_text(strip=True)
            if actor_name:
                return actor_name
    except Exception as e:
        logger.warning(f"Error parsing actor name from HTML: {e}")
    return None


def parse_section_name_from_html(html_content):
    """
    Extract section name from JavDB page HTML.
    
    This is a generic function that extracts names from pages using the 
    <span class="section-name"> element, which is used by:
    - makers (片商): e.g., "蚊香社, PRESTIGE,プレステージ"
    - publishers (发行商): e.g., "ABSOLUTELY FANTASIA"
    - series (系列): e.g., "親友の人妻と背徳不倫。禁断中出し小旅行。"
    - video_codes (番号): e.g., "ABF"
    - directors (导演): e.g., "Director Name"
    
    Args:
        html_content: HTML content of the page
    
    Returns:
        str: Section name if found, None otherwise
    """
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        section_name = soup.find('span', class_='section-name')
        if section_name:
            name = section_name.get_text(strip=True)
            if name:
                return name
    except Exception as e:
        logger.warning(f"Error parsing section name from HTML: {e}")
    return None


def parse_maker_name_from_html(html_content):
    """
    Extract maker (studio) name from JavDB maker page HTML.
    
    Looks for:
    <span class="section-subtitle">片商</span>
    <span class="section-name">MOODYZ</span>
    
    Args:
        html_content: HTML content of the maker page
    
    Returns:
        str: Maker name if found, None otherwise
    """
    return parse_section_name_from_html(html_content)


def parse_publisher_name_from_html(html_content):
    """
    Extract publisher name from JavDB publisher page HTML.
    
    Looks for:
    <span class="section-name">ABSOLUTELY FANTASIA</span>
    
    Args:
        html_content: HTML content of the publisher page
    
    Returns:
        str: Publisher name if found, None otherwise
    """
    return parse_section_name_from_html(html_content)


def parse_series_name_from_html(html_content):
    """
    Extract series name from JavDB series page HTML.
    
    Looks for:
    <span class="section-subtitle">系列</span>
    <span class="section-name">Series Name</span>
    
    Args:
        html_content: HTML content of the series page
    
    Returns:
        str: Series name if found, None otherwise
    """
    return parse_section_name_from_html(html_content)


def parse_video_code_name_from_html(html_content):
    """
    Extract video code name from JavDB video_codes page HTML.
    
    Looks for:
    <span class="section-subtitle">番號</span>
    <span class="section-name">ABF</span>
    
    Args:
        html_content: HTML content of the video_codes page
    
    Returns:
        str: Video code name if found, None otherwise
    """
    return parse_section_name_from_html(html_content)


def parse_director_name_from_html(html_content):
    """
    Extract director name from JavDB director page HTML.
    
    Looks for:
    <span class="section-name">Director Name</span>
    
    Args:
        html_content: HTML content of the director page
    
    Returns:
        str: Director name if found, None otherwise
    """
    return parse_section_name_from_html(html_content)


def sanitize_filename_part(text, max_length=30):
    """
    Sanitize text for use in filename.
    Removes or replaces characters that are not safe for filenames.
    
    Args:
        text: The text to sanitize
        max_length: Maximum length of the result
    
    Returns:
        str: Sanitized text safe for filenames
    """
    if not text:
        return ''
    
    # Replace or remove unsafe filename characters
    unsafe_chars = r'<>:"/\|?*'
    sanitized = text
    for char in unsafe_chars:
        sanitized = sanitized.replace(char, '')
    
    # Replace whitespace with underscore
    sanitized = re.sub(r'\s+', '_', sanitized)
    
    # Remove any remaining non-alphanumeric characters except underscore, hyphen, and CJK characters
    # Keep: alphanumeric, CJK characters, underscore, hyphen
    sanitized = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff-]', '', sanitized)
    
    # Truncate to max length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    
    return sanitized


def fetch_page_simple(url, timeout=30, use_session_cookie=False):
    """
    Fetch a webpage with minimal configuration.
    Used for extracting page names during CSV filename generation.
    Does not use proxy or CF bypass.
    
    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        use_session_cookie: Whether to include the JAVDB_SESSION_COOKIE in request
    
    Returns:
        str: HTML content if successful, None otherwise
    """
    # Build cookie string
    cookie_str = 'over18=1'
    if use_session_cookie and JAVDB_SESSION_COOKIE:
        cookie_str = f'over18=1; _jdb_session={JAVDB_SESSION_COOKIE}'
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
        'Cookie': cookie_str,
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            return response.text
        else:
            logger.debug(f"Failed to fetch {url}: HTTP {response.status_code}")
    except Exception as e:
        logger.debug(f"Error fetching {url} for page name: {e}")
    
    return None


def get_page_display_name(url, use_proxy=False, use_cf_bypass=False):
    """
    Get a human-readable display name for an adhoc URL by fetching the page.
    
    Note: This function is deprecated for CSV filename generation.
    Use generate_output_csv_name_from_html() instead, which parses the display name
    from already-fetched index page HTML to avoid extra network requests.
    
    For actors: Fetches HTML and extracts actor name
    For makers: Fetches HTML and extracts maker/studio name  
    For video_codes: Extracts the video code directly from URL
    
    Args:
        url: The JavDB URL
        use_proxy: Whether to use proxy (uses global_request_handler if available)
        use_cf_bypass: Whether to use CF bypass
    
    Returns:
        tuple: (display_name, url_type) where display_name is sanitized for filename
    """
    url_type = detect_url_type(url)
    url_id = extract_url_identifier(url)
    
    # For video_codes, we can get the name directly from URL without fetching HTML
    if url_type == 'video_codes':
        if url_id:
            return (sanitize_filename_part(url_id), 'video_codes')
        return (None, 'video_codes')
    
    # For actors and makers, we need to fetch the HTML
    html_content = None
    
    # Try to use global_request_handler if available
    if global_request_handler is not None:
        try:
            html_content = global_request_handler.get_page(
                url=url,
                use_proxy=use_proxy,
                use_cf_bypass=use_cf_bypass,
                use_cookie=True,
                module_name='csv_name_resolver',
                max_retries=2
            )
        except Exception as e:
            logger.debug(f"Failed to fetch page with request handler: {e}")
    
    # Fallback to simple fetch if request handler not available or failed
    if not html_content:
        html_content = fetch_page_simple(url, use_session_cookie=True)
    
    if not html_content:
        logger.debug(f"Could not fetch page for display name: {url}")
        return (None, url_type)
    
    # Parse based on URL type
    if url_type == 'actors':
        display_name = parse_actor_name_from_html(html_content)
        if display_name:
            return (sanitize_filename_part(display_name), 'actors')
    elif url_type == 'makers':
        display_name = parse_maker_name_from_html(html_content)
        if display_name:
            return (sanitize_filename_part(display_name), 'makers')
    
    return (None, url_type)


def extract_url_part_after_javdb(url):
    """
    Extract the part of URL after javdb.com and convert it to a filename-safe format.
    
    Converts URL path and query parameters to a safe filename string by replacing
    special characters (/, ?, &, =) with underscores and collapsing multiple
    consecutive underscores.
    
    Args:
        url: The custom URL (e.g., 'https://javdb.com/rankings/movies?p=monthly&t=censored')
    
    Returns:
        str: The extracted part converted to filename-safe format 
             (e.g., 'rankings_movies_p-monthly_t-censored')
    
    Examples:
        - 'https://javdb.com/actors/EvkJ' -> 'actors_EvkJ'
        - 'https://javdb.com/rankings/movies?p=monthly&t=censored' -> 'rankings_movies_p-monthly_t-censored'
    """
    try:
        if 'javdb.com' in url:
            domain_pos = url.find('javdb.com')
            if domain_pos != -1:
                after_domain = url[domain_pos + len('javdb.com'):]
                if after_domain.startswith('/'):
                    after_domain = after_domain[1:]
                if after_domain.endswith('/'):
                    after_domain = after_domain[:-1]
                # Replace URL special characters for filename safety
                # - / (path separator) -> _
                # - ? (query start) -> _
                # - & (param separator) -> _
                # - = (key-value separator) -> - (hyphen for better readability)
                filename_part = after_domain
                for char in ['/', '?', '&']:
                    filename_part = filename_part.replace(char, '_')
                filename_part = filename_part.replace('=', '-')
                # Collapse multiple consecutive underscores into one
                filename_part = re.sub(r'_+', '_', filename_part)
                # Remove leading/trailing underscores
                filename_part = filename_part.strip('_')
                return filename_part if filename_part else 'custom_url'
    except Exception as e:
        logger.warning(f"Error extracting URL part from {url}: {e}")
    return 'custom_url'


def generate_output_csv_name_from_html(custom_url, index_html):
    """
    Generate the output CSV filename by parsing display name from already-fetched HTML.
    
    This function is called after successfully fetching the first index page,
    which avoids an extra network request and ensures success.
    
    For adhoc mode with custom URLs:
    - actors: Uses actor name from HTML (e.g., Javdb_AdHoc_actors_森日向子_20251224.csv)
    - makers: Uses maker name from HTML (e.g., Javdb_AdHoc_makers_MOODYZ_20251224.csv)
    - publishers: Uses publisher name from HTML (e.g., Javdb_AdHoc_publishers_PRESTIGE_20251224.csv)
    - series: Uses series name from HTML (e.g., Javdb_AdHoc_series_SeriesName_20251224.csv)
    - directors: Uses director name from HTML (e.g., Javdb_AdHoc_directors_DirectorName_20251224.csv)
    - video_codes: Uses code from HTML or URL (e.g., Javdb_AdHoc_video_codes_ABF_20251224.csv)
    - unknown: Falls back to URL path extraction
    
    Args:
        custom_url: The custom URL being processed
        index_html: The HTML content of the first index page (already fetched)
    
    Returns:
        str: The generated CSV filename
    """
    today_date = datetime.now().strftime("%Y%m%d")
    url_type = detect_url_type(custom_url)
    display_name = None
    raw_name = None
    
    # Parse display name based on URL type
    if url_type == 'actors':
        raw_name = parse_actor_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted actor name from index page: {raw_name}")
    elif url_type == 'makers':
        raw_name = parse_maker_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted maker name from index page: {raw_name}")
    elif url_type == 'publishers':
        raw_name = parse_publisher_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted publisher name from index page: {raw_name}")
    elif url_type == 'series':
        raw_name = parse_series_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted series name from index page: {raw_name}")
    elif url_type == 'directors':
        raw_name = parse_director_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted director name from index page: {raw_name}")
    elif url_type == 'video_codes':
        # Try to get from HTML first, fallback to URL extraction
        raw_name = parse_video_code_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted video code from index page: {raw_name}")
        else:
            # Fallback to URL extraction for video_codes
            url_id = extract_url_identifier(custom_url)
            if url_id:
                display_name = sanitize_filename_part(url_id)
                logger.info(f"[AdHoc] Extracted video code from URL: {url_id}")
    
    if display_name:
        csv_filename = f'Javdb_AdHoc_{url_type}_{display_name}_{today_date}.csv'
        logger.info(f"[AdHoc] URL type: {url_type}, Display name: {display_name}")
        logger.info(f"[AdHoc] Generated CSV filename: {csv_filename}")
        return csv_filename
    else:
        # Fallback to URL extraction
        url_part = extract_url_part_after_javdb(custom_url)
        csv_filename = f'Javdb_AdHoc_{url_part}_{today_date}.csv'
        logger.warning(f"[AdHoc] Could not extract display name for URL type: {url_type}")
        logger.info(f"[AdHoc] Fallback CSV filename: {csv_filename}")
        return csv_filename


def generate_output_csv_name(custom_url=None, use_proxy=False, use_cf_bypass=False):
    """
    Generate the output CSV filename based on whether a custom URL is provided.
    
    Note: For adhoc mode, this generates a temporary filename using URL extraction.
    The actual display name will be resolved later using generate_output_csv_name_from_html()
    after the first index page is successfully fetched.
    
    Args:
        custom_url: Custom URL if provided, None otherwise
        use_proxy: Whether to use proxy for fetching page (unused, kept for compatibility)
        use_cf_bypass: Whether to use CF bypass for fetching page (unused, kept for compatibility)
    
    Returns:
        str: The generated CSV filename
    """
    if custom_url:
        today_date = datetime.now().strftime("%Y%m%d")
        url_type = detect_url_type(custom_url)
        
        # For video_codes, we can get the name directly from URL without fetching HTML
        if url_type == 'video_codes':
            url_id = extract_url_identifier(custom_url)
            if url_id:
                display_name = sanitize_filename_part(url_id)
                csv_filename = f'Javdb_AdHoc_{url_type}_{display_name}_{today_date}.csv'
                logger.info(f"[AdHoc] URL type: {url_type}, Display name: {display_name}")
                logger.info(f"[AdHoc] Generated CSV filename: {csv_filename}")
                return csv_filename
        
        # For other types (actors, makers), use temporary URL-based filename
        # The actual name will be resolved after fetching the first index page
        url_part = extract_url_part_after_javdb(custom_url)
        csv_filename = f'Javdb_AdHoc_{url_part}_{today_date}.csv'
        logger.info(f"[AdHoc] Temporary CSV filename (will resolve display name after fetching index page): {csv_filename}")
        return csv_filename
    else:
        return f'Javdb_TodayTitle_{datetime.now().strftime("%Y%m%d")}.csv'


def main():
    global global_proxy_pool
    
    # Parse command line arguments
    args = parse_arguments()

    # Update global variables based on arguments
    start_page = args.start_page
    end_page = args.end_page
    phase_mode = args.phase
    custom_url = args.url
    dry_run = args.dry_run
    ignore_history = args.ignore_history
    use_history = args.use_history
    parse_all = args.all
    ignore_release_date = args.ignore_release_date
    use_proxy = args.use_proxy
    use_cf_bypass = args.use_cf_bypass
    
    # Initialize proxy pool (always initialize if configured, even if not enabled by default)
    # This allows automatic fallback to proxy if direct connection fails
    # Ban log file is stored in reports directory
    ban_log_file = os.path.join(REPORTS_DIR, 'proxy_bans.csv')
    
    if PROXY_POOL and len(PROXY_POOL) > 0:
        if PROXY_MODE == 'pool':
            # Full proxy pool mode with automatic failover
            logger.info(f"Initializing proxy pool with {len(PROXY_POOL)} proxies...")
            global_proxy_pool = create_proxy_pool_from_config(
                PROXY_POOL,
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES,
                ban_log_file=ban_log_file
            )
            logger.info(f"Proxy pool initialized successfully")
            logger.info(f"Cooldown: {PROXY_POOL_COOLDOWN_SECONDS}s, Max failures before cooldown: {PROXY_POOL_MAX_FAILURES}")
        elif PROXY_MODE == 'single':
            # Single proxy mode - only use first proxy from pool
            logger.info(f"Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],  # Only use first proxy
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES,
                ban_log_file=ban_log_file
            )
            logger.info(f"Single proxy initialized: {PROXY_POOL[0].get('name', 'Main-Proxy')}")
    # Fallback to legacy PROXY_HTTP/PROXY_HTTPS if no PROXY_POOL configured
    elif PROXY_HTTP or PROXY_HTTPS:
        logger.info("Using legacy PROXY_HTTP/PROXY_HTTPS configuration")
        # Create a temporary proxy pool entry for consistency
        legacy_proxy = {
            'name': 'Legacy-Proxy',
            'http': PROXY_HTTP,
            'https': PROXY_HTTPS
        }
        global_proxy_pool = create_proxy_pool_from_config(
            [legacy_proxy],
            cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
            max_failures=PROXY_POOL_MAX_FAILURES,
            ban_log_file=ban_log_file
        )
    else:
        if use_proxy:
            logger.warning("Proxy enabled but no proxy configuration found (neither PROXY_POOL nor PROXY_HTTP/PROXY_HTTPS)")
        global_proxy_pool = None

    # Initialize global request handler (must be after proxy pool initialization)
    initialize_request_handler()

    # Determine output directory and filename
    # Reports use dated subdirectories (YYYY/MM) while history files stay at root
    if args.url:
        # Ad hoc mode: create dated subdirectory for reports
        output_dated_dir = ensure_report_dated_dir(AD_HOC_DIR)
        # Generate CSV filename with resolved page names (actor/maker/video_codes)
        if args.output_file:
            output_csv = args.output_file
        else:
            output_csv = generate_output_csv_name(custom_url, use_proxy=use_proxy, use_cf_bypass=use_cf_bypass)
        csv_path = os.path.join(output_dated_dir, output_csv)
        # Ad hoc mode: default to NOT checking history (process all entries)
        # Use --use-history to enable history filter in ad hoc mode
        use_history_for_loading = use_history
        use_history_for_saving = True    # Always record to history for ad hoc mode
    else:
        # Daily mode: create dated subdirectory for reports
        output_dated_dir = ensure_report_dated_dir(DAILY_REPORT_DIR)
        output_csv = args.output_file if args.output_file else OUTPUT_CSV
        csv_path = os.path.join(output_dated_dir, output_csv)
        use_history_for_loading = True   # Check history for daily mode
        use_history_for_saving = True    # Record to history for daily mode

    logger.info("Starting JavDB spider...")
    logger.info(f"Arguments: start_page={start_page}, end_page={end_page}, phase={phase_mode}")
    if custom_url:
        logger.info(f"Custom URL: {custom_url}")
        if use_history:
            logger.info("AD HOC MODE: History filter ENABLED (--use-history) - will skip entries already in history")
        else:
            logger.info("AD HOC MODE: Will process all entries (history filter disabled by default)")
    if dry_run:
        logger.info("DRY RUN MODE: No CSV file will be written")
    if ignore_history and not custom_url:
        # Only log this for daily mode, since ad hoc mode already defaults to ignoring history
        logger.info("IGNORE HISTORY: Will scrape all pages without checking history (but still save to history)")
    if parse_all:
        logger.info("PARSE ALL MODE: Will continue until empty page is found")
    if ignore_release_date:
        logger.info("IGNORE RELEASE DATE: Will process all entries regardless of today/yesterday tags")
    # Log mode information based on flags
    if use_cf_bypass and not CF_BYPASS_ENABLED:
        logger.warning("CF BYPASS MODE: Requested but DISABLED via CF_BYPASS_ENABLED=False in config.py")
    elif use_proxy and use_cf_bypass:
        # Mode: --use-proxy --use-cf-bypass
        logger.info("MODE: Proxy + Proxy's CF Bypass Service")
        logger.info(f"CF Bypass service port: {CF_BYPASS_SERVICE_PORT}")
        if global_proxy_pool is not None:
            current_proxy = global_proxy_pool.get_current_proxy()
            if current_proxy:
                proxy_url = current_proxy.get('https') or current_proxy.get('http')
                if proxy_url:
                    proxy_ip = extract_ip_from_proxy_url(proxy_url)
                    service_url = get_cf_bypass_service_url(proxy_ip)
                    masked_service_url = f"http://{mask_ip_address(proxy_ip)}:{CF_BYPASS_SERVICE_PORT}"
                    logger.info(f"CF Bypass URL: {masked_service_url}/html?url=<target>")
                    logger.info("Requests go directly to proxy server's bypass service (no proxy forwarding)")
    elif use_cf_bypass:
        # Mode: --use-cf-bypass only
        logger.info("MODE: Local CF Bypass Service only (no proxy)")
        logger.info(f"CF Bypass service port: {CF_BYPASS_SERVICE_PORT}")
        service_url = get_cf_bypass_service_url()
        logger.info(f"CF Bypass URL: http://127.0.0.1:{CF_BYPASS_SERVICE_PORT}/html?url=<target>")
    elif use_proxy:
        # Mode: --use-proxy only
        logger.info("MODE: Proxy only (no CF bypass)")
    
    if use_proxy:
        if global_proxy_pool is not None:
            stats = global_proxy_pool.get_statistics()
            
            if PROXY_MODE == 'pool':
                # Full proxy pool mode
                logger.info(f"PROXY POOL MODE: {stats['total_proxies']} proxies configured with automatic failover")
            elif PROXY_MODE == 'single':
                # Single proxy mode (using first proxy from pool)
                logger.info(f"SINGLE PROXY MODE: Using main proxy only (no automatic failover)")
                if stats['total_proxies'] > 0:
                    main_proxy_name = stats['proxies'][0]['name']
                    logger.info(f"Main proxy: {main_proxy_name}")
            
            # Show which modules will use proxy
            if not PROXY_MODULES:
                logger.warning("PROXY ENABLED: But PROXY_MODULES is empty - no modules will use proxy")
            elif 'all' in PROXY_MODULES:
                logger.info(f"PROXY ENABLED: Using proxy for ALL modules")
            else:
                logger.info(f"PROXY ENABLED: Using proxy for modules {PROXY_MODULES}")
        else:
            logger.warning("PROXY ENABLED: But no proxy configured in config.py")

    # Ensure reports root directory exists (for history files)
    ensure_reports_dir()

    # Initialize history file path and data (history files are stored in REPORTS_DIR root)
    history_file = os.path.join(REPORTS_DIR, PARSED_MOVIES_CSV)
    parsed_movies_history_phase1 = {}
    parsed_movies_history_phase2 = {}

    # Only load history if use_history_for_loading is True
    if use_history_for_loading:
        # Validate history file integrity
        if os.path.exists(history_file):
            logger.info("Validating history file integrity...")
            if not validate_history_file(history_file):
                logger.warning("History file validation failed - duplicates may be present")

        # If history file does not exist, create it with header
        if not os.path.exists(history_file):
            with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('href,phase,video_code,parsed_date,torrent_type\n')
            logger.info(f"Created new history file: {history_file}")
        
        if ignore_history:
            parsed_movies_history_phase1 = {}
            parsed_movies_history_phase2 = {}
        else:
            parsed_movies_history_phase1 = load_parsed_movies_history(history_file, phase=1)
            # For phase 2, load ALL history (phase 1 and 2)
            parsed_movies_history_phase2 = load_parsed_movies_history(history_file, phase=None)
    else:
        # For ad hoc mode, ensure history file exists for saving
        if use_history_for_saving and not os.path.exists(history_file):
            with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('href,phase,video_code,parsed_date,torrent_type\n')
            logger.info(f"Created new history file for ad hoc mode: {history_file}")

    # Create requests session for connection reuse
    session = requests.Session()
    logger.info("Initialized requests session")

    all_index_results_phase1 = []
    rows = []
    phase1_rows = []  # Track phase 1 entries separately
    phase2_rows = []  # Track phase 2 entries separately
    subtitle_count = 0
    hacked_count = 0
    no_subtitle_count = 0
    
    # Define fieldnames for CSV
    fieldnames = ['href', 'video_code', 'page', 'actor', 'rate', 'comment_number', 'hacked_subtitle',
                  'hacked_no_subtitle', 'subtitle', 'no_subtitle', 'size_hacked_subtitle', 'size_hacked_no_subtitle',
                  'size_subtitle', 'size_no_subtitle']
    
    # Tolerance mechanism configuration
    max_consecutive_empty = 3    # Maximum tolerance for consecutive empty pages
    
    # Track if any proxy was banned during the entire run (initialize before phases)
    any_proxy_banned = False
    any_proxy_banned_phase2 = False
    
    # Track skipped entries (for accurate statistics)
    # Separate counters for each phase for accurate email reporting
    skipped_session_count = 0
    skipped_history_count = 0  # Track entries skipped due to history
    failed_count = 0  # Track entries that failed to fetch/parse
    no_new_torrents_count = 0  # Track entries with no new torrents to download
    phase1_skipped_session = 0
    phase1_skipped_history_actual = 0  # Actual count during processing
    phase1_failed = 0  # Track entries that failed to fetch/parse in phase 1
    phase1_no_new_torrents = 0  # Track entries with no new torrents in phase 1
    phase2_skipped_session = 0
    phase2_skipped_history_actual = 0  # Actual count during processing
    phase2_failed = 0  # Track entries that failed to fetch/parse in phase 2
    phase2_no_new_torrents = 0  # Track entries with no new torrents in phase 2

    # ========================================
    # Fetch all index pages and parse immediately
    # ========================================
    # This avoids fetching the same pages twice for phase 1 and phase 2
    # Parse results are cached, not the raw HTML
    cached_pages = {}  # {page_num: index_html} - for phase 2 parsing
    all_index_results_phase2 = []  # Pre-collect phase 2 results
    last_valid_page = 0
    
    logger.info("=" * 75)
    logger.info("Fetching and parsing index pages")
    logger.info("=" * 75)

    page_num = start_page
    consecutive_empty_pages = 0
    csv_name_resolved = False  # Track if CSV name has been resolved from first successful page
    consecutive_fallback_successes = 0  # Track consecutive fallback successes before persisting settings
    # Thresholds based on fallback type:
    # - CF bypass only (proxy unchanged): proxies * 1
    # - Other changes (proxy changed): proxies * 3
    proxy_count = len(global_proxy_pool.proxies) if global_proxy_pool else 1
    fallback_persist_threshold_cf_only = proxy_count * 1  # Lower threshold for CF bypass only
    fallback_persist_threshold_full = proxy_count * 3     # Higher threshold for proxy changes
    current_fallback_threshold = fallback_persist_threshold_full  # Will be set based on fallback type
    pending_fallback_settings = None  # Store the fallback settings to be persisted (use_proxy, use_cf_bypass)
    logger.debug(f"Fallback persist thresholds: CF-only={fallback_persist_threshold_cf_only}, Full={fallback_persist_threshold_full} (based on {proxy_count} proxies in pool)")
    
    # Note: Magnet filtering is now done in parser.py based on HTML tags
    # This avoids the need for ?t=d URL filter which requires authentication
    # The URL-based magnet filter (t=d) is deprecated and disabled
        
    while True:
        # Generate page URL (no URL-based magnet filter - filtering done in parser)
        page_url = get_page_url(page_num, phase=1, custom_url=custom_url)
        logger.debug(f"[Page {page_num}] Fetching: {page_url}")

        # Fetch index page with fallback mechanism
        index_html, has_movie_list, proxy_was_banned, effective_use_proxy, effective_use_cf_bypass, is_valid_empty_page = fetch_index_page_with_fallback(
            page_url, session, 
            use_cookie=custom_url is not None, 
            use_proxy=use_proxy, 
            use_cf_bypass=use_cf_bypass,
            page_num=page_num,
            is_adhoc_mode=custom_url is not None  # Don't ban proxies in ad hoc mode
        )
        
        # Track fallback successes and persist settings after reaching threshold
        if has_movie_list and (effective_use_proxy != use_proxy or effective_use_cf_bypass != use_cf_bypass):
            # Determine fallback type and appropriate threshold
            is_cf_only_change = (effective_use_proxy == use_proxy) and (effective_use_cf_bypass != use_cf_bypass)
            
            # If this is a new fallback type, reset counter and set appropriate threshold
            if pending_fallback_settings is None or pending_fallback_settings != (effective_use_proxy, effective_use_cf_bypass):
                consecutive_fallback_successes = 0
                current_fallback_threshold = fallback_persist_threshold_cf_only if is_cf_only_change else fallback_persist_threshold_full
            
            # Fallback was triggered and succeeded
            consecutive_fallback_successes += 1
            pending_fallback_settings = (effective_use_proxy, effective_use_cf_bypass)
            fallback_type = "CF-only" if is_cf_only_change else "Full"
            logger.info(f"[Page {page_num}] Fallback succeeded ({fallback_type}: Proxy={effective_use_proxy}, CF={effective_use_cf_bypass}). "
                       f"Consecutive successes: {consecutive_fallback_successes}/{current_fallback_threshold}")
            
            # Only persist settings after reaching the threshold
            if consecutive_fallback_successes >= current_fallback_threshold:
                logger.info(f"[Page {page_num}] Reached {current_fallback_threshold} consecutive {fallback_type} fallback successes. "
                           f"Persisting settings: use_proxy={effective_use_proxy}, use_cf_bypass={effective_use_cf_bypass}")
                use_proxy = effective_use_proxy
                use_cf_bypass = effective_use_cf_bypass
                consecutive_fallback_successes = 0  # Reset counter after persisting
                pending_fallback_settings = None
            
            logger.info(f"[Page {page_num}] Applying {FALLBACK_COOLDOWN}s cooldown before next page...")
            time.sleep(FALLBACK_COOLDOWN)
        elif has_movie_list and pending_fallback_settings is not None:
            # Initial attempt succeeded but we have pending fallback settings - reset counter
            # This means the previous fallback mode is no longer consistently needed
            logger.debug(f"[Page {page_num}] Initial attempt succeeded, resetting fallback counter (was {consecutive_fallback_successes})")
            consecutive_fallback_successes = 0
            pending_fallback_settings = None
        
        if proxy_was_banned:
            any_proxy_banned = True
        
        # Note: URL-based magnet filter fallback removed - filtering now done in parser.py
        # based on HTML tags, no authentication required
        
        # Handle valid empty page (e.g. "No content yet") - this is the last page
        if is_valid_empty_page:
            logger.info(f"[Page {page_num}] End of content reached (no more pages available)")
            break
        
        if not index_html:
            logger.info(f"[Page {page_num}] no movie list found (page fetch failed or does not exist)")
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= max_consecutive_empty:
                logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping fetch")
                break
            page_num += 1
            continue
        
        if not has_movie_list:
            logger.warning(f"[Page {page_num}] No movie list found after all fallback attempts")
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= max_consecutive_empty:
                logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping fetch")
                break
            page_num += 1
            continue

        # Parse immediately after fetching - log results right away
        p1_count = 0
        p2_count = 0
        
        if phase_mode in ['1', 'all']:
            page_results = parse_index(index_html, page_num, phase=1,
                                       disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                                       is_adhoc_mode=(custom_url is not None))
            p1_count = len(page_results)
            if p1_count > 0:
                all_index_results_phase1.extend(page_results)
        
        # Also parse for phase 2 if needed (reuse the same HTML)
        if phase_mode in ['2', 'all']:
            page_results_p2 = parse_index(index_html, page_num, phase=2,
                                          disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                                          is_adhoc_mode=(custom_url is not None))
            p2_count = len(page_results_p2)
            if p2_count > 0:
                all_index_results_phase2.extend(page_results_p2)
        
        # Log combined results with aligned formatting
        if phase_mode == 'all':
            logger.info(f"[Page {page_num:2d}] Found {p1_count:3d} entries for phase 1, {p2_count:3d} for phase 2")
        elif phase_mode == '1':
            logger.info(f"[Page {page_num:2d}] Found {p1_count:3d} entries for phase 1")
        elif phase_mode == '2':
            logger.info(f"[Page {page_num:2d}] Found {p2_count:3d} entries for phase 2")
        
        last_valid_page = page_num
        consecutive_empty_pages = 0  # Reset counter when we find valid page
        
        # For ad hoc mode: resolve display name from first successful page's HTML
        # This updates csv_path with the actual name instead of URL-based fallback
        # Use csv_name_resolved flag instead of page_num == start_page to handle cases
        # where the starting page fails but a subsequent page succeeds
        if custom_url is not None and not csv_name_resolved and not args.output_file:
            url_type = detect_url_type(custom_url)
            # Resolve for all types that can extract names from HTML
            # (actors, makers, publishers, series, directors, video_codes)
            if url_type in ('actors', 'makers', 'publishers', 'series', 'directors', 'video_codes'):
                resolved_csv_name = generate_output_csv_name_from_html(custom_url, index_html)
                if resolved_csv_name != output_csv:
                    output_csv = resolved_csv_name
                    csv_path = os.path.join(output_dated_dir, output_csv)
                    logger.info(f"[AdHoc] Updated CSV path: {csv_path}")
            csv_name_resolved = True  # Mark as resolved to avoid re-resolving on subsequent pages

        # If not parse_all and reached end_page, stop
        if not parse_all and page_num >= end_page:
            break

        page_num += 1

        # Small delay between pages
        time.sleep(PAGE_SLEEP)
    
    logger.info(f"Fetched and parsed {last_valid_page - start_page + 1 if last_valid_page >= start_page else 0} pages")

    # ========================================
    # Process Phase 1 entries
    # ========================================
    if phase_mode in ['1', 'all']:
        logger.info("=" * 75)
        if custom_url is not None:
            logger.info(f"PHASE 1: Processing {len(all_index_results_phase1)} collected entries with subtitle (AD HOC MODE)")
        else:
            logger.info(f"PHASE 1: Processing {len(all_index_results_phase1)} collected entries with subtitle")
        logger.info("=" * 75)

        # Process phase 1 entries
        total_entries_phase1 = len(all_index_results_phase1)
        
        # Reset fallback tracking for detail pages (reuse thresholds from index phase)
        detail_consecutive_fallback_successes = 0
        detail_pending_fallback_settings = None
        detail_current_fallback_threshold = fallback_persist_threshold_full

        # Track pending sleep - only sleep before entries that will make network requests
        pending_movie_sleep = False

        for i, entry in enumerate(all_index_results_phase1, 1):
            href = entry['href']
            page_num = entry['page']
            fallback_triggered = False  # Track if fallback was triggered for this entry

            # Skip if already parsed in this session
            if href in parsed_links:
                logger.info(f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipping already parsed in this session")
                skipped_session_count += 1
                phase1_skipped_session += 1
                # No sleep needed - pending_movie_sleep stays as-is for next entry that needs processing
                continue

            # Add to parsed links set for this session
            parsed_links.add(href)

            # Early skip check: if movie already has both subtitle and hacked_subtitle in history,
            # skip fetching detail page to avoid unnecessary network requests
            if has_complete_subtitles(href, parsed_movies_history_phase1):
                logger.info(f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipping {entry['video_code']} - already has subtitle and hacked_subtitle in history")
                skipped_history_count += 1
                phase1_skipped_history_actual += 1
                # No sleep needed - pending_movie_sleep stays as-is for next entry that needs processing
                continue

            # This entry needs processing - apply pending sleep before making network request
            if pending_movie_sleep:
                time.sleep(MOVIE_SLEEP)
                pending_movie_sleep = False

            detail_url = urljoin(BASE_URL, href)

            # Fetch detail page with fallback mechanism
            # Note: video_code is already extracted from index page in entry['video_code']
            magnets, actor_info, parse_success, effective_use_proxy, effective_use_cf_bypass = fetch_detail_page_with_fallback(
                detail_url, session,
                use_cookie=custom_url is not None,
                use_proxy=use_proxy,
                use_cf_bypass=use_cf_bypass,
                entry_index=f"{i}/{total_entries_phase1}",
                is_adhoc_mode=custom_url is not None
            )
            
            # Track fallback successes and persist settings after reaching threshold (same as index phase)
            fallback_triggered = parse_success and (effective_use_proxy != use_proxy or effective_use_cf_bypass != use_cf_bypass)
            if fallback_triggered:
                # Determine fallback type and appropriate threshold
                is_cf_only_change = (effective_use_proxy == use_proxy) and (effective_use_cf_bypass != use_cf_bypass)
                
                # If this is a new fallback type, reset counter and set appropriate threshold
                if detail_pending_fallback_settings is None or detail_pending_fallback_settings != (effective_use_proxy, effective_use_cf_bypass):
                    detail_consecutive_fallback_successes = 0
                    detail_current_fallback_threshold = fallback_persist_threshold_cf_only if is_cf_only_change else fallback_persist_threshold_full
                
                detail_consecutive_fallback_successes += 1
                detail_pending_fallback_settings = (effective_use_proxy, effective_use_cf_bypass)
                fallback_type = "CF-only" if is_cf_only_change else "Full"
                logger.info(f"[{i}/{total_entries_phase1}] Fallback succeeded ({fallback_type}: Proxy={effective_use_proxy}, CF={effective_use_cf_bypass}). "
                           f"Consecutive successes: {detail_consecutive_fallback_successes}/{detail_current_fallback_threshold}")
                
                # Only persist settings after reaching the threshold
                if detail_consecutive_fallback_successes >= detail_current_fallback_threshold:
                    logger.info(f"[{i}/{total_entries_phase1}] Reached {detail_current_fallback_threshold} consecutive {fallback_type} fallback successes. "
                               f"Persisting settings: use_proxy={effective_use_proxy}, use_cf_bypass={effective_use_cf_bypass}")
                    use_proxy = effective_use_proxy
                    use_cf_bypass = effective_use_cf_bypass
                    detail_consecutive_fallback_successes = 0  # Reset counter after persisting
                    detail_pending_fallback_settings = None
            elif parse_success and detail_pending_fallback_settings is not None:
                # Initial attempt succeeded but we have pending fallback settings - reset counter
                logger.debug(f"[{i}/{total_entries_phase1}] Initial attempt succeeded, resetting fallback counter (was {detail_consecutive_fallback_successes})")
                detail_consecutive_fallback_successes = 0
                detail_pending_fallback_settings = None
            
            if not parse_success and not magnets:
                logger.error(f"[{i}/{total_entries_phase1}] [Page {page_num}] Failed to fetch/parse detail page after all fallback attempts")
                failed_count += 1
                phase1_failed += 1
                # Mark pending sleep - will only execute if next entry needs network request
                pending_movie_sleep = True
                continue
            
            magnet_links = extract_magnets(magnets, i)

            # Log the processing with video_code from index page
            logger.info(f"[{i}/{total_entries_phase1}] [Page {page_num}] Processing {entry['video_code'] or href}")

            # Check if we should process this movie based on history and phase rules
            should_process, history_torrent_types = should_process_movie(href, parsed_movies_history_phase1, 1,
                                                                         magnet_links)

            if not should_process:
                # Only skip if both hacked_subtitle and subtitle are present in history
                if history_torrent_types and 'hacked_subtitle' in history_torrent_types and 'subtitle' in history_torrent_types:
                    logger.debug(
                        f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipping based on history rules (history types: {history_torrent_types})")
                else:
                    logger.debug(
                        f"[{i}/{total_entries_phase1}] [Page {page_num}] Should process, missing preferred types: {get_missing_torrent_types(history_torrent_types, [])}")
                skipped_history_count += 1
                phase1_skipped_history_actual += 1
                # Mark pending sleep - network request was made, so respect rate limiting
                pending_movie_sleep = True
                continue

            # Count found torrents
            if magnet_links['subtitle']:
                subtitle_count += 1
            if magnet_links['hacked_subtitle'] or magnet_links['hacked_no_subtitle']:
                hacked_count += 1
            if magnet_links['no_subtitle']:
                no_subtitle_count += 1

            # Determine current torrent types and merge with history
            current_torrent_types = determine_torrent_types(magnet_links)
            all_torrent_types = list(
                set(history_torrent_types + current_torrent_types)) if history_torrent_types else current_torrent_types

            # Create row with video_code as title
            row = create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links,
                                                     parsed_movies_history_phase1)
            # Override the title with video_code
            video_code = entry['video_code']
            row['video_code'] = video_code

            # Only add row if it contains new torrent categories (excluding already downloaded ones)
            has_new_torrents = any([
                row['hacked_subtitle'] and row['hacked_subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['hacked_no_subtitle'] and row['hacked_no_subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['subtitle'] and row['subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['no_subtitle'] and row['no_subtitle'] != '[DOWNLOADED PREVIOUSLY]'
            ])

            if has_new_torrents:
                # Write to CSV immediately (before updating history)
                write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
                rows.append(row)
                phase1_rows.append(row)  # Track phase 1 entries
                logger.debug(f"[{i}/{total_entries_phase1}] [Page {page_num}] Added to CSV with new torrent categories")
                
                # Save to parsed movies history AFTER writing to CSV (only if new torrents found)
                # Note: ignore_history only affects reading, not saving
                if use_history_for_saving and not dry_run:
                    # Only save new magnet links to history (exclude already downloaded ones)
                    new_magnet_links = {}
                    if row['hacked_subtitle'] and row['hacked_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['hacked_subtitle'] = magnet_links.get('hacked_subtitle', '')
                    if row['hacked_no_subtitle'] and row['hacked_no_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['hacked_no_subtitle'] = magnet_links.get('hacked_no_subtitle', '')
                    if row['subtitle'] and row['subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['subtitle'] = magnet_links.get('subtitle', '')
                    if row['no_subtitle'] and row['no_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['no_subtitle'] = magnet_links.get('no_subtitle', '')
                    
                    if new_magnet_links:  # Only save if there are actually new magnet links
                        save_parsed_movie_to_history(history_file, href, 1, video_code, new_magnet_links)
            else:
                logger.debug(
                    f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipped CSV entry - all torrent categories already in history")
                # Don't update history if no new torrents were found
                no_new_torrents_count += 1
                phase1_no_new_torrents += 1

            # Apply appropriate delay
            if fallback_triggered:
                # Extra cooldown after fallback to let Cloudflare/server recover
                logger.debug(f"[{i}/{total_entries_phase1}] Applying fallback cooldown: {FALLBACK_COOLDOWN}s")
                time.sleep(FALLBACK_COOLDOWN)
                pending_movie_sleep = False  # Already waited enough
            else:
                # Mark pending sleep - will only execute if next entry needs network request
                pending_movie_sleep = True

        # Phase 1 statistics - use actual tracked counts
        # Verify: total_entries_phase1 == phase1_skipped_session + phase1_skipped_history_actual + len(phase1_rows) + phase1_no_new_torrents + phase1_failed
        logger.info(f"Phase 1 completed: {total_entries_phase1} movies discovered, {len(phase1_rows)} processed, {phase1_skipped_session} skipped (session), {phase1_skipped_history_actual} skipped (history), {phase1_no_new_torrents} no new torrents, {phase1_failed} failed")

    # ========================================
    # Process Phase 2 entries (already parsed during fetch)
    # ========================================
    # Phase 2: Collect entries with only "今日新種"/"昨日新種" tag (filtered by quality)
    if phase_mode in ['2', 'all']:
        # Add cooldown delay between phases to avoid triggering Cloudflare protection
        if phase_mode == 'all':
            logger.info(f"Waiting {PHASE_TRANSITION_COOLDOWN} seconds before Phase 2")
            time.sleep(PHASE_TRANSITION_COOLDOWN)
        
        logger.info("=" * 75)
        if custom_url is not None:
            # Ad hoc mode: all filters disabled
            logger.info(f"PHASE 2: Processing {len(all_index_results_phase2)} collected entries (AD HOC MODE - all filters disabled)")
        else:
            logger.info(f"PHASE 2: Processing {len(all_index_results_phase2)} collected entries (rate > {PHASE2_MIN_RATE}, comments > {PHASE2_MIN_COMMENTS})")
        logger.info("=" * 75)

        # all_index_results_phase2 was already populated during the fetch phase

        # Process phase 2 entries
        total_entries_phase2 = len(all_index_results_phase2)
        
        # Reset fallback tracking for phase 2 detail pages (reuse thresholds from index phase)
        detail_consecutive_fallback_successes = 0
        detail_pending_fallback_settings = None
        detail_current_fallback_threshold = fallback_persist_threshold_full

        # Track pending sleep - only sleep before entries that will make network requests
        pending_movie_sleep = False

        for i, entry in enumerate(all_index_results_phase2, 1):
            href = entry['href']
            page_num = entry['page']
            fallback_triggered = False  # Track if fallback was triggered for this entry

            # Skip if already parsed in this session
            if href in parsed_links:
                logger.info(f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipping already parsed in this session")
                skipped_session_count += 1
                phase2_skipped_session += 1
                # No sleep needed - pending_movie_sleep stays as-is for next entry that needs processing
                continue

            # Add to parsed links set for this session
            parsed_links.add(href)

            # Early skip check: if movie already has both subtitle and hacked_subtitle in history,
            # skip fetching detail page to avoid unnecessary network requests
            if has_complete_subtitles(href, parsed_movies_history_phase2):
                logger.info(f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipping {entry['video_code']} - already has subtitle and hacked_subtitle in history")
                skipped_history_count += 1
                phase2_skipped_history_actual += 1
                # No sleep needed - pending_movie_sleep stays as-is for next entry that needs processing
                continue

            # This entry needs processing - apply pending sleep before making network request
            if pending_movie_sleep:
                time.sleep(MOVIE_SLEEP)
                pending_movie_sleep = False

            detail_url = urljoin(BASE_URL, href)

            # Fetch detail page with fallback mechanism
            # Note: video_code is already extracted from index page in entry['video_code']
            magnets, actor_info, parse_success, effective_use_proxy, effective_use_cf_bypass = fetch_detail_page_with_fallback(
                detail_url, session,
                use_cookie=custom_url is not None,
                use_proxy=use_proxy,
                use_cf_bypass=use_cf_bypass,
                entry_index=f"P2-{i}/{total_entries_phase2}",
                is_adhoc_mode=custom_url is not None
            )
            
            # Track fallback successes and persist settings after reaching threshold (same as index phase)
            fallback_triggered = parse_success and (effective_use_proxy != use_proxy or effective_use_cf_bypass != use_cf_bypass)
            if fallback_triggered:
                # Determine fallback type and appropriate threshold
                is_cf_only_change = (effective_use_proxy == use_proxy) and (effective_use_cf_bypass != use_cf_bypass)
                
                # If this is a new fallback type, reset counter and set appropriate threshold
                if detail_pending_fallback_settings is None or detail_pending_fallback_settings != (effective_use_proxy, effective_use_cf_bypass):
                    detail_consecutive_fallback_successes = 0
                    detail_current_fallback_threshold = fallback_persist_threshold_cf_only if is_cf_only_change else fallback_persist_threshold_full
                
                detail_consecutive_fallback_successes += 1
                detail_pending_fallback_settings = (effective_use_proxy, effective_use_cf_bypass)
                fallback_type = "CF-only" if is_cf_only_change else "Full"
                logger.info(f"[P2-{i}/{total_entries_phase2}] Fallback succeeded ({fallback_type}: Proxy={effective_use_proxy}, CF={effective_use_cf_bypass}). "
                           f"Consecutive successes: {detail_consecutive_fallback_successes}/{detail_current_fallback_threshold}")
                
                # Only persist settings after reaching the threshold
                if detail_consecutive_fallback_successes >= detail_current_fallback_threshold:
                    logger.info(f"[P2-{i}/{total_entries_phase2}] Reached {detail_current_fallback_threshold} consecutive {fallback_type} fallback successes. "
                               f"Persisting settings: use_proxy={effective_use_proxy}, use_cf_bypass={effective_use_cf_bypass}")
                    use_proxy = effective_use_proxy
                    use_cf_bypass = effective_use_cf_bypass
                    detail_consecutive_fallback_successes = 0  # Reset counter after persisting
                    detail_pending_fallback_settings = None
            elif parse_success and detail_pending_fallback_settings is not None:
                # Initial attempt succeeded but we have pending fallback settings - reset counter
                logger.debug(f"[P2-{i}/{total_entries_phase2}] Initial attempt succeeded, resetting fallback counter (was {detail_consecutive_fallback_successes})")
                detail_consecutive_fallback_successes = 0
                detail_pending_fallback_settings = None
            
            if not parse_success and not magnets:
                logger.error(f"[{i}/{total_entries_phase2}] [Page {page_num}] Failed to fetch/parse detail page after all fallback attempts")
                failed_count += 1
                phase2_failed += 1
                # Mark pending sleep - will only execute if next entry needs network request
                pending_movie_sleep = True
                continue
            
            magnet_links = extract_magnets(magnets, f"P2-{i}")

            # Log the processing with video_code from index page
            logger.info(f"[{i}/{total_entries_phase2}] [Page {page_num}] Processing {entry['video_code']}")

            # Check if we should process this movie based on history and phase rules
            should_process, history_torrent_types = should_process_movie(href, parsed_movies_history_phase2, 2,
                                                                         magnet_links)

            if not should_process:
                # Only skip if both hacked_subtitle and subtitle are present in history
                if history_torrent_types and 'hacked_subtitle' in history_torrent_types and 'subtitle' in history_torrent_types:
                    logger.debug(
                        f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipping based on history rules (history types: {history_torrent_types})")
                else:
                    logger.debug(
                        f"[{i}/{total_entries_phase2}] [Page {page_num}] Should process, missing preferred types: {get_missing_torrent_types(history_torrent_types, [])}")
                skipped_history_count += 1
                phase2_skipped_history_actual += 1
                # Mark pending sleep - network request was made, so respect rate limiting
                pending_movie_sleep = True
                continue

            # Count found torrents
            if magnet_links['subtitle']:
                subtitle_count += 1
            if magnet_links['hacked_subtitle'] or magnet_links['hacked_no_subtitle']:
                hacked_count += 1
            if magnet_links['no_subtitle']:
                no_subtitle_count += 1

            # Determine current torrent types and merge with history
            current_torrent_types = determine_torrent_types(magnet_links)
            all_torrent_types = list(
                set(history_torrent_types + current_torrent_types)) if history_torrent_types else current_torrent_types

            # Create row for Phase 2
            row = create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links,
                                                     parsed_movies_history_phase2)
            # Override the title with video_code
            video_code = entry['video_code']
            row['video_code'] = video_code

            # Only add row if it contains new torrent categories (excluding already downloaded ones)
            has_new_torrents = any([
                row['hacked_subtitle'] and row['hacked_subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['hacked_no_subtitle'] and row['hacked_no_subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['subtitle'] and row['subtitle'] != '[DOWNLOADED PREVIOUSLY]',
                row['no_subtitle'] and row['no_subtitle'] != '[DOWNLOADED PREVIOUSLY]'
            ])

            if has_new_torrents:
                # Write to CSV immediately (before updating history)
                write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
                rows.append(row)
                phase2_rows.append(row)  # Track phase 2 entries
                logger.debug(f"[{i}/{total_entries_phase2}] [Page {page_num}] Added to CSV with new torrent categories")
                
                # Save to parsed movies history AFTER writing to CSV (only if new torrents found)
                # Note: ignore_history only affects reading, not saving
                if use_history_for_saving and not dry_run:
                    # Only save new magnet links to history (exclude already downloaded ones)
                    new_magnet_links = {}
                    if row['hacked_subtitle'] and row['hacked_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['hacked_subtitle'] = magnet_links.get('hacked_subtitle', '')
                    if row['hacked_no_subtitle'] and row['hacked_no_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['hacked_no_subtitle'] = magnet_links.get('hacked_no_subtitle', '')
                    if row['subtitle'] and row['subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['subtitle'] = magnet_links.get('subtitle', '')
                    if row['no_subtitle'] and row['no_subtitle'] != '[DOWNLOADED PREVIOUSLY]':
                        new_magnet_links['no_subtitle'] = magnet_links.get('no_subtitle', '')
                    
                    if new_magnet_links:  # Only save if there are actually new magnet links
                        save_parsed_movie_to_history(history_file, href, 2, video_code, new_magnet_links)
            else:
                logger.debug(
                    f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipped CSV entry - all torrent categories already in history")
                # Don't update history if no new torrents were found
                no_new_torrents_count += 1
                phase2_no_new_torrents += 1

            # Apply appropriate delay
            if fallback_triggered:
                # Extra cooldown after fallback to let Cloudflare/server recover
                logger.debug(f"[P2-{i}/{total_entries_phase2}] Applying fallback cooldown: {FALLBACK_COOLDOWN}s")
                time.sleep(FALLBACK_COOLDOWN)
                pending_movie_sleep = False  # Already waited enough
            else:
                # Mark pending sleep - will only execute if next entry needs network request
                pending_movie_sleep = True

        # Phase 2 statistics - use actual tracked counts
        # Verify: total_entries_phase2 == phase2_skipped_session + phase2_skipped_history_actual + len(phase2_rows) + phase2_no_new_torrents + phase2_failed
        logger.info(f"Phase 2 completed: {total_entries_phase2} movies discovered, {len(phase2_rows)} processed, {phase2_skipped_session} skipped (session), {phase2_skipped_history_actual} skipped (history), {phase2_no_new_torrents} no new torrents, {phase2_failed} failed")

    # CSV has been written incrementally during processing
    if not dry_run:
        logger.info(f"CSV file written incrementally to: {csv_path}")

    # Generate summary
    logger.info("=" * 75)
    logger.info("SUMMARY REPORT")
    logger.info("=" * 75)
    if parse_all:
        logger.info(f"Pages processed: {start_page} to last page with results")
    else:
        logger.info(f"Pages processed: {start_page} to {end_page}")
    
    logger.info(f"Tolerance mechanism: Stops after {max_consecutive_empty} consecutive pages with no HTML content")

    # Phase 1 Summary
    if phase_mode in ['1', 'all']:
        logger.info("=" * 30)
        logger.info("PHASE 1 SUMMARY")
        logger.info("=" * 30)
        logger.info(f"Phase 1 entries found: {len(phase1_rows)}")
        if len(phase1_rows) > 0:
            phase1_subtitle_count = sum(1 for row in phase1_rows if row['subtitle'])
            phase1_hacked_subtitle_count = sum(1 for row in phase1_rows if row['hacked_subtitle'])
            phase1_hacked_no_subtitle_count = sum(1 for row in phase1_rows if row['hacked_no_subtitle'])
            phase1_no_subtitle_count = sum(1 for row in phase1_rows if row['no_subtitle'])

            logger.info(
                f"  - Subtitle torrents: {phase1_subtitle_count} ({(phase1_subtitle_count / len(phase1_rows) * 100):.1f}%)")
            logger.info(
                f"  - Hacked subtitle torrents: {phase1_hacked_subtitle_count} ({(phase1_hacked_subtitle_count / len(phase1_rows) * 100):.1f}%)")
            logger.info(
                f"  - Hacked no-subtitle torrents: {phase1_hacked_no_subtitle_count} ({(phase1_hacked_no_subtitle_count / len(phase1_rows) * 100):.1f}%)")
            logger.info(
                f"  - No-subtitle torrents: {phase1_no_subtitle_count} ({(phase1_no_subtitle_count / len(phase1_rows) * 100):.1f}%)")
        else:
            logger.info("  - No entries found in Phase 1")

    # Phase 2 Summary
    if phase_mode in ['2', 'all']:
        logger.info("=" * 30)
        logger.info("PHASE 2 SUMMARY")
        logger.info("=" * 30)
        logger.info(f"Phase 2 entries found: {len(phase2_rows)}")
        if len(phase2_rows) > 0:
            phase2_subtitle_count = sum(1 for row in phase2_rows if row['subtitle'])
            phase2_hacked_subtitle_count = sum(1 for row in phase2_rows if row['hacked_subtitle'])
            phase2_hacked_no_subtitle_count = sum(1 for row in phase2_rows if row['hacked_no_subtitle'])
            phase2_no_subtitle_count = sum(1 for row in phase2_rows if row['no_subtitle'])

            logger.info(
                f"  - Subtitle torrents: {phase2_subtitle_count} ({(phase2_subtitle_count / len(phase2_rows) * 100):.1f}%)")
            logger.info(
                f"  - Hacked subtitle torrents: {phase2_hacked_subtitle_count} ({(phase2_hacked_subtitle_count / len(phase2_rows) * 100):.1f}%)")
            logger.info(
                f"  - Hacked no-subtitle torrents: {phase2_hacked_no_subtitle_count} ({(phase2_hacked_no_subtitle_count / len(phase2_rows) * 100):.1f}%)")
            logger.info(
                f"  - No-subtitle torrents: {phase2_no_subtitle_count} ({(phase2_no_subtitle_count / len(phase2_rows) * 100):.1f}%)")
        else:
            logger.info("  - No entries found in Phase 2")

    # Overall Summary
    # Note: "movies" = unique movie pages, each movie can have multiple torrent links
    # total_discovered = processed + skipped_session + skipped_history + no_new_torrents + failed
    total_discovered = len(rows) + skipped_session_count + skipped_history_count + no_new_torrents_count + failed_count
    logger.info("=" * 30)
    logger.info("OVERALL SUMMARY")
    logger.info("=" * 30)
    logger.info(f"Total movies discovered: {total_discovered}")
    logger.info(f"Successfully processed: {len(rows)}")
    logger.info(f"Skipped already parsed in this session: {skipped_session_count}")
    if use_history_for_loading and not ignore_history:
        logger.info(f"Skipped already parsed in previous runs: {skipped_history_count}")
    elif ignore_history:
        logger.info("History reading was disabled (--ignore-history), but results will still be saved to history")
    logger.info(f"No new torrents to download: {no_new_torrents_count}")
    logger.info(f"Failed to fetch/parse: {failed_count}")
    logger.info(f"Current parsed links in memory: {len(parsed_links)}")

    # Overall torrent statistics
    if len(rows) > 0:
        total_subtitle_count = sum(1 for row in rows if row['subtitle'])
        total_hacked_subtitle_count = sum(1 for row in rows if row['hacked_subtitle'])
        total_hacked_no_subtitle_count = sum(1 for row in rows if row['hacked_no_subtitle'])
        total_no_subtitle_count = sum(1 for row in rows if row['no_subtitle'])

        logger.info(
            f"Overall subtitle torrents: {total_subtitle_count} ({(total_subtitle_count / len(rows) * 100):.1f}%)")
        logger.info(
            f"Overall hacked subtitle torrents: {total_hacked_subtitle_count} ({(total_hacked_subtitle_count / len(rows) * 100):.1f}%)")
        logger.info(
            f"Overall hacked no-subtitle torrents: {total_hacked_no_subtitle_count} ({(total_hacked_no_subtitle_count / len(rows) * 100):.1f}%)")
        logger.info(
            f"Overall no-subtitle torrents: {total_no_subtitle_count} ({(total_no_subtitle_count / len(rows) * 100):.1f}%)")

    if not dry_run:
        logger.info(f"Results saved to: {csv_path}")
        if use_history_for_saving:
            logger.info(f"History saved to: {os.path.join(REPORTS_DIR, PARSED_MOVIES_CSV)}")
        # Output the CSV full path in a parseable format for downstream scripts
        # This allows GitHub Actions or pipeline.py to capture and pass to qb_uploader
        print(f"SPIDER_OUTPUT_CSV={csv_path}")
    logger.info("=" * 75)
    
    # Log proxy statistics and ban status if using proxy
    if use_proxy and PROXY_MODE in ('pool', 'single') and global_proxy_pool is not None:
        logger.info("")
        global_proxy_pool.log_statistics(level=logging.INFO)
        
        # Log ban summary (without IP for logs)
        logger.info("")
        logger.info("=" * 75)
        logger.info("PROXY BAN STATUS")
        logger.info("=" * 75)
        ban_summary = global_proxy_pool.get_ban_summary(include_ip=False)
        logger.info(ban_summary)
        logger.info("=" * 75)
    
    # Log proxy ban HTML files if any were saved
    if proxy_ban_html_files:
        logger.info("")
        logger.info("=" * 75)
        logger.info("PROXY BAN HTML FILES")
        logger.info("=" * 75)
        logger.info(f"Saved {len(proxy_ban_html_files)} proxy ban HTML file(s) for debugging:")
        for html_file in proxy_ban_html_files:
            logger.info(f"  - {html_file}")
        logger.info("=" * 75)
        # Output file list for downstream scripts (e.g., email notification)
        print(f"PROXY_BAN_HTML_FILES={','.join(proxy_ban_html_files)}")
    
    # Check for critical failures and exit with appropriate code
    # Track if any proxy was banned during the entire run
    proxies_were_banned = False
    if phase_mode in ['1', 'all']:
        proxies_were_banned = proxies_were_banned or any_proxy_banned
    if phase_mode in ['2', 'all']:
        proxies_were_banned = proxies_were_banned or any_proxy_banned_phase2
    
    if proxies_were_banned:
        logger.error("=" * 75)
        logger.error("CRITICAL: PROXY BAN DETECTED DURING THIS RUN")
        logger.error("=" * 75)
        logger.error("One or more proxies were marked as BANNED due to failure to retrieve movie list.")
        logger.error("This indicates the proxy IP may be blocked by JavDB.")
        logger.error("Please check proxy ban status and consider using different proxies.")
        sys.exit(2)  # Exit code 2 indicates proxy ban
    
    # Check if we got any results at all (might indicate all proxies are banned)
    if len(rows) == 0 and use_proxy and use_cf_bypass:
        # No results with proxy + CF bypass might indicate issues
        logger.warning("=" * 75)
        logger.warning("WARNING: No entries found while using proxy and CF bypass")
        logger.warning("=" * 75)
        logger.warning("This might indicate proxy issues or CF bypass service problems.")
        # Don't exit with error - it's possible there are legitimately no new entries

    # Git commit spider results (only if credentials are available)
    from_pipeline = args.from_pipeline if hasattr(args, 'from_pipeline') else False
    
    if not dry_run and has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing spider results...")
        # Flush log handlers to ensure all logs are written before commit
        flush_log_handlers()
        
        files_to_commit = [
            REPORTS_DIR,  # Includes DailyReport, AdHoc subdirectories and history files
            'logs/',
        ]
        commit_message = f"Auto-commit: Spider results {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        git_commit_and_push(
            files_to_add=files_to_commit,
            commit_message=commit_message,
            from_pipeline=from_pipeline,
            git_username=GIT_USERNAME,
            git_password=GIT_PASSWORD,
            git_repo_url=GIT_REPO_URL,
            git_branch=GIT_BRANCH
        )
    elif not dry_run:
        logger.info("Skipping git commit - no credentials provided (commit will be handled by workflow)")


if __name__ == '__main__':
    main() 
