import requests
import time
import re
import random
import logging
import os
import argparse
import sys
import threading
import queue as queue_module
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
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

from api.parsers import parse_index_page as api_parse_index_page
from api.parsers import parse_detail_page as api_parse_detail_page
from api.parsers import parse_category_page as api_parse_category_page
from api.parsers import parse_top_page as api_parse_top_page
from utils.git_helper import git_commit_and_push, flush_log_handlers, has_git_credentials
from utils.path_helper import get_dated_report_path, ensure_dated_dir, get_dated_subdir

# Import unified configuration
try:
    from config import (
        BASE_URL, START_PAGE, END_PAGE,
        REPORTS_DIR, DAILY_REPORT_DIR, AD_HOC_DIR, PARSED_MOVIES_CSV,
        SPIDER_LOG_FILE, LOG_LEVEL, PAGE_SLEEP, MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX,
        JAVDB_SESSION_COOKIE, PHASE2_MIN_RATE, PHASE2_MIN_COMMENTS,
        PROXY_HTTP, PROXY_HTTPS, PROXY_MODULES,
        CF_TURNSTILE_COOLDOWN, FALLBACK_COOLDOWN,
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
    PAGE_SLEEP = 2
    MOVIE_SLEEP_MIN = 5
    MOVIE_SLEEP_MAX = 15
    JAVDB_SESSION_COOKIE = None
    PHASE2_MIN_RATE = 4.0
    PHASE2_MIN_COMMENTS = 100
    PROXY_HTTP = None
    PROXY_HTTPS = None
    PROXY_MODULES = ['all']
    CF_TURNSTILE_COOLDOWN = 10
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

# Import report configuration (optional)
try:
    from config import INCLUDE_DOWNLOADED_IN_REPORT
except ImportError:
    INCLUDE_DOWNLOADED_IN_REPORT = False

# Configure logging
from utils.logging_config import setup_logging, get_logger
setup_logging(SPIDER_LOG_FILE, LOG_LEVEL)
logger = get_logger(__name__)

# Check Rust components availability and log status
try:
    from api.parsers import RUST_PARSERS_AVAILABLE
    if RUST_PARSERS_AVAILABLE:
        logger.debug("✅ Spider using Rust parsers - high-performance HTML parsing enabled")
    else:
        logger.debug("⚠️  Spider using Python parsers - Rust parsers not available")
except Exception:
    logger.debug("⚠️  Could not determine parser implementation status")

try:
    from utils.history_manager import RUST_HISTORY_AVAILABLE
    if RUST_HISTORY_AVAILABLE:
        logger.debug("✅ Spider using Rust history manager - high-performance CSV I/O enabled")
    else:
        logger.debug("⚠️  Spider using Python history manager - Rust not available")
except Exception:
    logger.info("⚠️  Could not determine history manager implementation status")

# Import masking utilities
from utils.masking import mask_ip_address, mask_username, mask_full, mask_proxy_url
from utils.url_helper import (
    detect_url_type, extract_url_identifier, has_magnet_filter,
    add_magnet_filter_to_url, sanitize_filename_part,
    extract_url_part_after_javdb,
    get_page_url as _url_helper_get_page_url,
)
from utils.csv_writer import merge_row_data, write_csv
from utils.filename_helper import (
    generate_output_csv_name, generate_output_csv_name_from_html,
)

# Import proxy pool
from utils.proxy_pool import ProxyPool, create_proxy_pool_from_config

# Import unified request handler
from utils.request_handler import RequestHandler, RequestConfig, create_request_handler_from_config

class MovieSleepManager:
    """Randomised movie sleep with adaptive throttling.

    Picks a random sleep time in ``[sleep_min, sleep_max]``.  When the chosen
    value falls in the bottom 10 % of the range the *next* call is forced to
    pick from the top 30 % to avoid consecutive short intervals.
    """

    # (threshold, min_multiplier, max_multiplier)  — base [5, 15]
    # N < 50  → (1.0, 1.0): [5, 15]    avg ~10s
    # 50–74   → (1.1, 1.3): [5.5, 19.5] avg ~13s
    # 75–99   → (1.2, 1.6): [6, 24]     avg ~16s
    # 100–124 → (1.3, 2.0): [6.5, 30]   avg ~19s
    # 125–149 → (1.4, 2.5): [7, 37.5]   avg ~23s
    # ≥ 150   → (1.5, 3.0): [7.5, 45]   avg ~27s
    VOLUME_TIERS = [
        (50,  1.0, 1.0),
        (75,  1.1, 1.3),
        (100, 1.2, 1.6),
        (125, 1.3, 2.0),
        (150, 1.4, 2.5),
    ]
    VOLUME_MAX_MULTIPLIER = (1.5, 3.0)

    def __init__(self, sleep_min: float, sleep_max: float):
        self.base_min = float(sleep_min)
        self.base_max = float(sleep_max)
        self.sleep_min = self.base_min
        self.sleep_max = self.base_max
        self._force_high = False

    def apply_volume_multiplier(self, n: int) -> None:
        """Scale sleep range based on estimated processing volume *n*.

        Only activates when n > 50.  Multipliers grow with volume to reduce
        request pressure during large runs.
        """
        min_mult, max_mult = 1.0, 1.0
        for threshold, m_lo, m_hi in self.VOLUME_TIERS:
            if n < threshold:
                break
            min_mult, max_mult = m_lo, m_hi
        else:
            if n >= self.VOLUME_TIERS[-1][0]:
                min_mult, max_mult = self.VOLUME_MAX_MULTIPLIER

        self.sleep_min = round(self.base_min * min_mult, 1)
        self.sleep_max = round(self.base_max * max_mult, 1)
        if min_mult > 1.0 or max_mult > 1.0:
            logger.info(
                "Volume-based sleep adjustment: N=%d → sleep range [%.1f, %.1f] "
                "(base [%.1f, %.1f], multipliers %.1fx/%.1fx)",
                n, self.sleep_min, self.sleep_max,
                self.base_min, self.base_max, min_mult, max_mult,
            )

    def get_sleep_time(self) -> float:
        span = self.sleep_max - self.sleep_min
        if span <= 0:
            return self.sleep_min

        if self._force_high:
            low = self.sleep_min + span * 0.7
            sleep_time = random.uniform(low, self.sleep_max)
            self._force_high = False
        else:
            sleep_time = random.uniform(self.sleep_min, self.sleep_max)

        if sleep_time <= self.sleep_min + span * 0.1:
            self._force_high = True

        return round(sleep_time, 1)

    def sleep(self) -> float:
        """Sleep for a random duration and return the chosen time."""
        t = self.get_sleep_time()
        logger.debug("Movie sleep: %.1fs (force_high_next=%s)", t, self._force_high)
        time.sleep(t)
        return t


movie_sleep_mgr = MovieSleepManager(MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX)

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

# Per-proxy CF bypass tracking: proxy names that require CF bypass in this runtime
# When CF bypass succeeds through a proxy, that proxy is added here.
# All future requests via that proxy will automatically use CF bypass.
proxies_requiring_cf_bypass: set = set()


def proxy_needs_cf_bypass(proxy_name: str) -> bool:
    """Check if a proxy has been marked as requiring CF bypass."""
    return proxy_name in proxies_requiring_cf_bypass


def mark_proxy_cf_bypass(proxy_name: str):
    """Mark a proxy as requiring CF bypass for all future requests in this runtime."""
    if proxy_name not in proxies_requiring_cf_bypass:
        proxies_requiring_cf_bypass.add(proxy_name)
        logger.info(f"Proxy '{proxy_name}' marked as requiring CF bypass for this runtime")


# ---------------------------------------------------------------------------
# Parallel detail processing data structures
# ---------------------------------------------------------------------------

@dataclass
class DetailTask:
    """A detail page to be fetched by a worker thread."""
    url: str
    entry: dict
    phase: int
    entry_index: str
    retry_count: int = 0
    failed_proxies: set = field(default_factory=set)


@dataclass
class DetailResult:
    """Result produced by a worker after processing a DetailTask."""
    task: DetailTask
    magnets: list
    actor_info: str
    parse_success: bool
    used_cf_bypass: bool


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

    parser.add_argument('--from-pipeline', action='store_true',
                        help='Running from pipeline.py - use GIT_USERNAME for commits')

    parser.add_argument('--max-movies-phase1', type=int, default=None,
                        help='Limit the number of movies to process in phase 1 (for testing purposes)')

    parser.add_argument('--max-movies-phase2', type=int, default=None,
                        help='Limit the number of movies to process in phase 2 (for testing purposes)')

    parser.add_argument('--sequential', action='store_true',
                        help='Force sequential detail processing even in proxy pool mode (disables parallel workers)')

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
    
    CF bypass is not enabled by default for initial requests. It is automatically
    tried during the fallback mechanism in fetch_index_page_with_fallback() and
    fetch_detail_page_with_fallback() when direct requests fail.
    
    Service repository: https://github.com/sarperavci/CloudflareBypassForScraping
    
    Args:
        url: URL to fetch
        session: requests.Session object for connection reuse
        use_cookie: Whether to add session cookie
        use_proxy: Whether proxy is enabled
        module_name: Module name for proxy control ('spider', 'qbittorrent', 'pikpak', etc.)
        max_retries: Maximum number of retries with different proxies (only for proxy pool mode)
        use_cf_bypass: Whether to use CF bypass service (set by fallback mechanism)
        
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


def is_login_page(html: str) -> bool:
    """Detect whether the returned HTML is a JavDB login page."""
    if not html:
        return False
    from bs4 import BeautifulSoup as _BS
    soup = _BS(html, 'html.parser')
    title_tag = soup.find('title')
    if title_tag:
        title_text = title_tag.get_text().strip().lower()
        if '登入' in title_text or 'login' in title_text:
            return True
    return False


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

def get_page_url(page_num, custom_url=None):
    """Generate URL for a specific page number."""
    return _url_helper_get_page_url(page_num, BASE_URL, custom_url)


def fetch_index_page_with_fallback(page_url, session, use_cookie, use_proxy, use_cf_bypass, page_num, is_adhoc_mode=False):
    """
    Fetch index page with smart multi-level fallback mechanism.
    
    Fallback Hierarchy (per-proxy, with max_retries=1 to avoid internal proxy cycling):
    1. Proxy A: direct request (or CF bypass if proxy is marked)
    2. Proxy A: CF bypass (if not already tried in step 1)
    3. Login refresh (once per runtime, adhoc mode only for index) → retry Proxy A steps 1-2
    4. Proxy B: direct request (or CF bypass if proxy is marked)
    5. Proxy B: CF bypass (if not already tried)
    6. Continue through all proxies in the pool
    
    When CF bypass succeeds with a proxy, that proxy is marked to always use
    CF bypass for subsequent requests in this runtime.
    
    Args:
        page_url: URL to fetch
        session: requests.Session object
        use_cookie: Whether to use session cookie
        use_proxy: Whether proxy is currently enabled
        use_cf_bypass: Whether CF bypass is currently enabled
        page_num: Current page number (for logging)
        is_adhoc_mode: If True, don't mark proxies as banned on failure
    
    Returns:
        tuple: (html_content, has_movie_list, proxy_was_banned, effective_use_proxy, effective_use_cf_bypass, is_valid_empty_page)
    """
    proxy_was_banned = False
    last_failed_html = None
    
    def _validate_index_html(html, context_msg):
        """Validate index page HTML. Returns (html, has_movie_list, is_valid_empty)."""
        nonlocal last_failed_html
        soup = BeautifulSoup(html, 'html.parser')
        movie_list = soup.find('div', class_=lambda x: x and 'movie-list' in x)
        if movie_list:
            movie_items = movie_list.find_all('div', class_='item')
            if len(movie_items) > 0:
                logger.debug(f"[Page {page_num}] Success: {context_msg} - Found {len(movie_items)} movie items")
                return html, True, False
            else:
                logger.info(f"[Page {page_num}] movie-list exists but is empty (0 items) - treating as valid empty page")
                return html, False, True
        else:
            page_text = soup.get_text()
            title = soup.find('title')
            title_text = title.text.strip() if title else ""
            age_modal = soup.find('div', class_='modal is-active over18-modal')
            empty_message_div = soup.find('div', class_='empty-message')
            has_no_content_msg = (
                'No content yet' in page_text or
                'No result' in page_text or
                '暫無內容' in page_text or
                '暂无内容' in page_text or
                empty_message_div is not None
            )

            if empty_message_div is not None:
                empty_msg_text = empty_message_div.get_text().strip()
                logger.info(f"[Page {page_num}] Page exists but has no content (empty-message: '{empty_msg_text}')")
                return html, False, True
            elif not age_modal and has_no_content_msg:
                logger.info(f"[Page {page_num}] Page exists but has no content (text pattern detected)")
                return html, False, True
            elif not age_modal and len(html) > 20000:
                logger.debug(f"[Page {page_num}] Large HTML without movie list, treating as empty page")
                return html, False, True
            else:
                last_failed_html = html
                logger.debug(f"[Page {page_num}] Validation failed (no movie list, age_modal={age_modal is not None}): {context_msg}")
        return None, False, False

    def try_fetch(u_proxy, u_cf, context_msg):
        nonlocal last_failed_html
        logger.debug(f"[Page {page_num}] {context_msg}...")
        try:
            html = get_page(page_url, session, use_cookie=use_cookie,
                            use_proxy=u_proxy, module_name='spider',
                            max_retries=1, use_cf_bypass=u_cf)
            if html:
                if is_login_page(html):
                    logger.warning(f"[Page {page_num}] Login page detected: {context_msg}")
                    if can_attempt_login(is_adhoc_mode, is_index_page=True):
                        logger.info(f"[Page {page_num}] Attempting login refresh due to login page...")
                        login_ok, _ = attempt_login_refresh()
                        if login_ok:
                            html = get_page(page_url, session, use_cookie=use_cookie,
                                            use_proxy=u_proxy, module_name='spider',
                                            max_retries=1, use_cf_bypass=u_cf)
                            if html and not is_login_page(html):
                                return _validate_index_html(html, context_msg)
                            else:
                                logger.warning(f"[Page {page_num}] Still login page after refresh")
                    last_failed_html = html
                    return None, False, False

                return _validate_index_html(html, context_msg)
        except Exception as e:
            logger.debug(f"[Page {page_num}] Failed {context_msg}: {e}")
        return None, False, False

    def try_proxy_direct_then_cf(proxy_name):
        """Try a single proxy: direct first (unless marked for CF), then CF bypass.
        Returns (html, has_movie_list, is_valid_empty, used_cf_bypass)."""
        needs_cf = proxy_needs_cf_bypass(proxy_name)
        
        if needs_cf:
            html, success, is_valid_empty = try_fetch(
                True, True, f"Index: Proxy={proxy_name} + CF Bypass (marked)")
            if success or is_valid_empty:
                return html, success, is_valid_empty, True
            return None, False, False, True
        
        # Step a: Direct request with this proxy
        html, success, is_valid_empty = try_fetch(
            True, False, f"Index: Proxy={proxy_name} Direct")
        if success or is_valid_empty:
            return html, success, is_valid_empty, False
        
        # Step b: CF bypass with this proxy
        html, success, is_valid_empty = try_fetch(
            True, True, f"Index: Proxy={proxy_name} + CF Bypass")
        if success or is_valid_empty:
            if success:
                mark_proxy_cf_bypass(proxy_name)
            return html, success, is_valid_empty, True
        
        return None, False, False, False

    # --- Phase 0: Initial Attempt with current proxy ---
    if use_proxy and global_proxy_pool:
        current_proxy_name = global_proxy_pool.get_current_proxy_name()
        initial_cf = use_cf_bypass or proxy_needs_cf_bypass(current_proxy_name)
        
        html, success, is_valid_empty = try_fetch(
            True, initial_cf,
            f"Initial attempt (Proxy={current_proxy_name}, CF={initial_cf})")
        if success:
            return html, True, False, True, initial_cf, False
        if is_valid_empty:
            return html, False, False, True, initial_cf, True
        
        # Phase 0.5: If initial was direct, try CF bypass with same proxy
        if not initial_cf:
            html, success, is_valid_empty = try_fetch(
                True, True,
                f"Index: Proxy={current_proxy_name} + CF Bypass")
            if success:
                mark_proxy_cf_bypass(current_proxy_name)
                return html, True, False, True, True, False
            if is_valid_empty:
                return html, False, False, True, True, True
        
        logger.warning(f"[Page {page_num}] Initial attempt failed. Starting fallback...")
    elif not use_proxy:
        html, success, is_valid_empty = try_fetch(
            False, use_cf_bypass,
            f"Initial attempt (No Proxy, CF={use_cf_bypass})")
        if success:
            return html, True, False, False, use_cf_bypass, False
        if is_valid_empty:
            return html, False, False, False, use_cf_bypass, True
        logger.warning(f"[Page {page_num}] Initial attempt failed (no proxy). Starting fallback...")
    else:
        logger.warning(f"[Page {page_num}] No proxy pool configured for initial attempt.")

    # --- Phase 1: Login Refresh (once per runtime, adhoc only for index pages) ---
    if is_adhoc_mode and can_attempt_login(is_adhoc_mode, is_index_page=True):
        logger.info(f"[Page {page_num}] Attempting login refresh...")
        login_success, new_cookie = attempt_login_refresh()
        if login_success and use_proxy and global_proxy_pool:
            current_proxy_name = global_proxy_pool.get_current_proxy_name()
            html, success, is_valid_empty, used_cf = try_proxy_direct_then_cf(current_proxy_name)
            if success:
                logger.info(f"[Page {page_num}] Login refresh + retry succeeded (Proxy={current_proxy_name}, CF={used_cf})")
                return html, True, False, True, used_cf, False
            if is_valid_empty:
                return html, False, False, True, used_cf, True
            logger.warning(f"[Page {page_num}] Login refresh completed but index page still failed")
        elif login_success and not use_proxy:
            html, success, is_valid_empty = try_fetch(
                False, use_cf_bypass,
                f"Fallback: Retry with refreshed cookie (No Proxy)")
            if success:
                return html, True, False, False, use_cf_bypass, False
            if is_valid_empty:
                return html, False, False, False, use_cf_bypass, True
        elif not login_success:
            logger.warning(f"[Page {page_num}] Login refresh failed, continuing with proxy pool fallback...")

    # --- Phase 2: Iterate through remaining proxies ---
    if global_proxy_pool is None:
        logger.error(f"[Page {page_num}] Fallback failed: No proxy pool configured")
        return last_failed_html, False, False, use_proxy, use_cf_bypass, False

    max_switches = global_proxy_pool.get_proxy_count() if PROXY_MODE == 'pool' else 1
    max_switches = min(max_switches, 10)
    
    # If current proxy was already tried in Phase 0, mark it failed before the loop
    if use_proxy:
        if is_adhoc_mode:
            global_proxy_pool.mark_failure_and_switch()
        else:
            if last_failed_html:
                current_proxy_name = global_proxy_pool.get_current_proxy_name()
                save_proxy_ban_html(last_failed_html, current_proxy_name, page_num)
            for _ in range(PROXY_POOL_MAX_FAILURES):
                global_proxy_pool.mark_failure_and_switch()
            proxy_was_banned = True

    for attempt in range(max_switches):
        current_proxy_name = global_proxy_pool.get_current_proxy_name()
        html, success, is_valid_empty, used_cf = try_proxy_direct_then_cf(current_proxy_name)
        if success:
            logger.info(f"[Page {page_num}] Index Proxy fallback succeeded (Proxy={current_proxy_name}, CF={used_cf})")
            return html, True, proxy_was_banned, True, used_cf, False
        if is_valid_empty:
            return html, False, proxy_was_banned, True, used_cf, True

        if is_adhoc_mode:
            global_proxy_pool.mark_failure_and_switch()
        else:
            if html:
                save_proxy_ban_html(html, current_proxy_name, page_num)
            for _ in range(PROXY_POOL_MAX_FAILURES):
                global_proxy_pool.mark_failure_and_switch()
            proxy_was_banned = True

    logger.error(f"[Page {page_num}] All proxy attempts exhausted.")
    return last_failed_html, False, proxy_was_banned, use_proxy, use_cf_bypass, False


def fetch_detail_page_with_fallback(detail_url, session, use_cookie, use_proxy, use_cf_bypass, entry_index, is_adhoc_mode=False):
    """
    Fetch detail page with smart multi-level fallback mechanism.
    Similar to fetch_index_page_with_fallback, but validates using parse_detail success.
    
    Note: Unlike index page fallback, this function does NOT mark proxies as banned on failure,
    because detail page failures are often due to page-specific issues (login required, 
    page not found, etc.) rather than proxy problems.
    
    Fallback Hierarchy (per-proxy, with max_retries=1 to avoid internal proxy cycling):
    1. Proxy A: direct request (or CF bypass if proxy is marked)
    2. Proxy A: CF bypass (if not already tried in step 1)
    3. Login refresh (once per runtime) → retry Proxy A steps 1-2
    4. Proxy B: direct request (or CF bypass if proxy is marked)
    5. Proxy B: CF bypass (if not already tried)
    6. Continue through all proxies in the pool
    
    When CF bypass succeeds with a proxy, that proxy is marked to always use
    CF bypass for subsequent requests in this runtime.
    
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
    """
    last_result = ([], '', False)
    
    def try_fetch_and_parse(u_proxy, u_cf, context_msg, skip_sleep=False):
        nonlocal last_result
        logger.debug(f"[{entry_index}] {context_msg}...")
        try:
            html = get_page(detail_url, session, use_cookie=use_cookie,
                            use_proxy=u_proxy, module_name='spider',
                            max_retries=1, use_cf_bypass=u_cf)
            if html:
                if is_login_page(html):
                    logger.warning(f"[{entry_index}] Login page detected: {context_msg}")
                    if can_attempt_login(is_adhoc_mode, is_index_page=False):
                        logger.info(f"[{entry_index}] Attempting login refresh due to login page...")
                        login_ok, _ = attempt_login_refresh()
                        if login_ok:
                            html = get_page(detail_url, session, use_cookie=use_cookie,
                                            use_proxy=u_proxy, module_name='spider',
                                            max_retries=1, use_cf_bypass=u_cf)
                            if html and not is_login_page(html):
                                magnets, actor_info, parse_success = parse_detail(html, entry_index, skip_sleep=skip_sleep)
                                if parse_success:
                                    logger.info(f"[{entry_index}] Login refresh succeeded: {context_msg}")
                                    return magnets, actor_info, True
                                else:
                                    last_result = (magnets, actor_info, False)
                            else:
                                logger.warning(f"[{entry_index}] Still login page after refresh")
                    return [], '', False

                magnets, actor_info, parse_success = parse_detail(html, entry_index, skip_sleep=skip_sleep)
                if parse_success:
                    logger.debug(f"[{entry_index}] Success: {context_msg}")
                    return magnets, actor_info, True
                else:
                    last_result = (magnets, actor_info, False)
                    logger.debug(f"[{entry_index}] Parse validation failed (missing magnets): {context_msg}")
            else:
                logger.debug(f"[{entry_index}] Failed to fetch HTML: {context_msg}")
        except Exception as e:
            logger.debug(f"[{entry_index}] Failed {context_msg}: {e}")
        return [], '', False

    def try_proxy_direct_then_cf(proxy_name, skip_sleep=True):
        """Try a single proxy: direct first (unless marked for CF), then CF bypass.
        Returns (magnets, actor_info, success, used_cf_bypass)."""
        needs_cf = proxy_needs_cf_bypass(proxy_name)
        
        if needs_cf:
            # This proxy is marked as needing CF bypass, skip direct attempt
            magnets, actor_info, success = try_fetch_and_parse(
                True, True,
                f"Detail: Proxy={proxy_name} + CF Bypass (marked)",
                skip_sleep=skip_sleep
            )
            if success:
                return magnets, actor_info, True, True
            return [], '', False, True
        
        # Step a: Direct request with this proxy
        magnets, actor_info, success = try_fetch_and_parse(
            True, False,
            f"Detail: Proxy={proxy_name} Direct",
            skip_sleep=skip_sleep
        )
        if success:
            return magnets, actor_info, True, False
        
        # Step b: CF bypass with this proxy
        magnets, actor_info, success = try_fetch_and_parse(
            True, True,
            f"Detail: Proxy={proxy_name} + CF Bypass",
            skip_sleep=skip_sleep
        )
        if success:
            mark_proxy_cf_bypass(proxy_name)
            return magnets, actor_info, True, True
        
        return [], '', False, False

    # --- Phase 0: Initial Attempt with current proxy ---
    if use_proxy and global_proxy_pool:
        current_proxy_name = global_proxy_pool.get_current_proxy_name()
        initial_cf = use_cf_bypass or proxy_needs_cf_bypass(current_proxy_name)
        
        magnets, actor_info, success = try_fetch_and_parse(
            True, initial_cf,
            f"Detail Initial (Proxy={current_proxy_name}, CF={initial_cf})",
            skip_sleep=False
        )
        if success:
            return magnets, actor_info, True, True, initial_cf
        
        # Phase 0.5: If initial was direct, try CF bypass with same proxy
        if not initial_cf:
            magnets, actor_info, success = try_fetch_and_parse(
                True, True,
                f"Detail: Proxy={current_proxy_name} + CF Bypass",
                skip_sleep=True
            )
            if success:
                mark_proxy_cf_bypass(current_proxy_name)
                logger.info(f"[{entry_index}] Detail CF Bypass succeeded with initial proxy={current_proxy_name}")
                return magnets, actor_info, True, True, True
        
        logger.warning(f"[{entry_index}] Detail page initial attempt failed. Starting fallback...")
    elif not use_proxy:
        # No proxy mode: single direct attempt
        magnets, actor_info, success = try_fetch_and_parse(
            False, use_cf_bypass,
            f"Detail Initial (No Proxy, CF={use_cf_bypass})",
            skip_sleep=False
        )
        if success:
            return magnets, actor_info, True, False, use_cf_bypass
        logger.warning(f"[{entry_index}] Detail page initial attempt failed (no proxy). Starting fallback...")
    else:
        logger.warning(f"[{entry_index}] No proxy pool configured for initial attempt.")

    # --- Phase 1: Login Refresh (once per runtime) → retry current proxy ---
    if can_attempt_login(is_adhoc_mode, is_index_page=False):
        logger.info(f"[{entry_index}] Attempting login refresh...")
        login_success, new_cookie = attempt_login_refresh()
        if login_success and use_proxy and global_proxy_pool:
            current_proxy_name = global_proxy_pool.get_current_proxy_name()
            magnets, actor_info, success, used_cf = try_proxy_direct_then_cf(current_proxy_name, skip_sleep=True)
            if success:
                logger.info(f"[{entry_index}] Login refresh + retry succeeded (Proxy={current_proxy_name}, CF={used_cf})")
                return magnets, actor_info, True, True, used_cf
            logger.warning(f"[{entry_index}] Login refresh completed but detail page still failed")
        elif login_success and not use_proxy:
            magnets, actor_info, success = try_fetch_and_parse(
                False, use_cf_bypass,
                f"Detail: Retry with refreshed cookie (No Proxy)",
                skip_sleep=True
            )
            if success:
                return magnets, actor_info, True, False, use_cf_bypass
        elif not login_success:
            logger.warning(f"[{entry_index}] Login refresh failed, continuing with proxy pool fallback...")

    # --- Phase 2: Iterate through remaining proxies ---
    if global_proxy_pool is None:
        logger.error(f"[{entry_index}] Fallback failed: No proxy pool configured")
        return last_result[0], last_result[1], last_result[2], use_proxy, use_cf_bypass

    max_switches = global_proxy_pool.get_proxy_count() if PROXY_MODE == 'pool' else 1
    max_switches = min(max_switches, 10)
    
    for attempt in range(max_switches):
        switched = global_proxy_pool.mark_failure_and_switch()
        if not switched:
            logger.warning(f"[{entry_index}] No more proxies available in pool")
            break
        
        current_proxy_name = global_proxy_pool.get_current_proxy_name()
        magnets, actor_info, success, used_cf = try_proxy_direct_then_cf(current_proxy_name, skip_sleep=True)
        if success:
            logger.info(f"[{entry_index}] Detail Proxy fallback succeeded (Proxy={current_proxy_name}, CF={used_cf})")
            return magnets, actor_info, True, True, used_cf

    logger.warning(f"[{entry_index}] Detail page fallback exhausted. Returning best available result.")
    return last_result[0], last_result[1], last_result[2], use_proxy, use_cf_bypass


# ---------------------------------------------------------------------------
# Parallel detail processing — Producer-Consumer with proxy affinity
# ---------------------------------------------------------------------------

# Lock protecting login_attempted / refreshed_session_cookie across workers
_login_lock = threading.Lock()


class ProxyWorker(threading.Thread):
    """Worker thread bound to a single proxy (ARM server + local CF bypass)."""

    def __init__(
        self,
        worker_id: int,
        proxy_config: dict,
        detail_queue: 'queue_module.Queue[Optional[DetailTask]]',
        result_queue: 'queue_module.Queue[DetailResult]',
        total_workers: int,
        use_cookie: bool,
        is_adhoc_mode: bool,
        movie_sleep_min: float,
        movie_sleep_max: float,
        fallback_cooldown: float,
        ban_log_file: str,
        all_workers: list,
    ):
        super().__init__(daemon=True, name=f"ProxyWorker-{proxy_config.get('name', worker_id)}")
        self.worker_id = worker_id
        self.proxy_config = proxy_config
        self.proxy_name: str = proxy_config.get('name', f'Proxy-{worker_id}')
        self.detail_queue = detail_queue
        self.result_queue = result_queue
        self.total_workers = total_workers
        self.use_cookie = use_cookie
        self.is_adhoc_mode = is_adhoc_mode
        self._sleep_mgr = MovieSleepManager(movie_sleep_min, movie_sleep_max)
        self.fallback_cooldown = fallback_cooldown
        self.all_workers = all_workers

        self.needs_cf_bypass = False
        self._first_request = True

        self._proxy_pool = create_proxy_pool_from_config(
            [proxy_config],
            cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
            max_failures=PROXY_POOL_MAX_FAILURES,
            ban_log_file=ban_log_file,
        )
        self._handler = RequestHandler(
            proxy_pool=self._proxy_pool,
            config=RequestConfig(
                base_url=BASE_URL,
                cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
                cf_bypass_enabled=CF_BYPASS_ENABLED,
                cf_bypass_max_failures=3,
                cf_turnstile_cooldown=CF_TURNSTILE_COOLDOWN,
                fallback_cooldown=FALLBACK_COOLDOWN,
                javdb_session_cookie=JAVDB_SESSION_COOKIE,
                proxy_http=proxy_config.get('http'),
                proxy_https=proxy_config.get('https'),
                proxy_modules=['all'],
                proxy_mode='single',
            ),
        )

    # -- internal helpers --------------------------------------------------

    def _fetch_html(self, url: str, use_cf: bool) -> Optional[str]:
        return self._handler.get_page(
            url,
            use_cookie=self.use_cookie,
            use_proxy=True,
            module_name='spider',
            max_retries=1,
            use_cf_bypass=use_cf,
        )

    def _try_fetch_and_parse(self, task: DetailTask, use_cf: bool, context: str):
        """Attempt fetch + parse_detail; returns (magnets, actor, success)."""
        logger.debug(f"[{self.proxy_name}] [{task.entry_index}] {context}")
        try:
            html = self._fetch_html(task.url, use_cf)
            if html:
                if is_login_page(html):
                    logger.warning(
                        f"[{self.proxy_name}] [{task.entry_index}] Login page detected: {context}")
                    if can_attempt_login(self.is_adhoc_mode, is_index_page=False):
                        if self._try_login_refresh():
                            html = self._fetch_html(task.url, use_cf)
                            if html and not is_login_page(html):
                                magnets, actor_info, ok = parse_detail(
                                    html, task.entry_index, skip_sleep=True)
                                if ok:
                                    logger.info(
                                        f"[{self.proxy_name}] [{task.entry_index}] "
                                        f"Login refresh succeeded: {context}")
                                    return magnets, actor_info, True
                            else:
                                logger.warning(
                                    f"[{self.proxy_name}] [{task.entry_index}] "
                                    f"Still login page after refresh")
                    return [], '', False

                magnets, actor_info, ok = parse_detail(html, task.entry_index, skip_sleep=True)
                if ok:
                    return magnets, actor_info, True
                logger.debug(f"[{self.proxy_name}] [{task.entry_index}] parse failed: {context}")
            else:
                logger.debug(f"[{self.proxy_name}] [{task.entry_index}] no HTML: {context}")
        except Exception as e:
            logger.debug(f"[{self.proxy_name}] [{task.entry_index}] error in {context}: {e}")
        return [], '', False

    def _try_direct_then_cf(self, task: DetailTask):
        """Try direct, then CF bypass. Returns (magnets, actor, success, used_cf)."""
        if self.needs_cf_bypass:
            m, a, ok = self._try_fetch_and_parse(task, True, f"CF Bypass (marked)")
            return m, a, ok, True

        m, a, ok = self._try_fetch_and_parse(task, False, "Direct")
        if ok:
            return m, a, True, False

        m, a, ok = self._try_fetch_and_parse(task, True, "CF Bypass")
        if ok:
            self.needs_cf_bypass = True
            logger.info(f"[{self.proxy_name}] CF Bypass succeeded — marking proxy for this runtime")
            return m, a, True, True
        return [], '', False, False

    def _try_login_refresh(self):
        """Thread-safe global login; returns True on success."""
        with _login_lock:
            if login_attempted:
                return refreshed_session_cookie is not None
            success, new_cookie = attempt_login_refresh()
            if success and new_cookie:
                for w in self.all_workers:
                    w._handler.config.javdb_session_cookie = new_cookie
                return True
            return False

    # -- main loop ---------------------------------------------------------

    def run(self):
        while True:
            task = self.detail_queue.get()
            if task is None:
                break

            if self.proxy_name in task.failed_proxies:
                if len(task.failed_proxies) >= self.total_workers:
                    if can_attempt_login(self.is_adhoc_mode, is_index_page=False):
                        if self._try_login_refresh():
                            task.failed_proxies.clear()
                            task.retry_count += 1
                            self.detail_queue.put(task)
                            continue
                    self.result_queue.put(DetailResult(
                        task=task, magnets=[], actor_info='',
                        parse_success=False, used_cf_bypass=False,
                    ))
                    continue
                self.detail_queue.put(task)
                time.sleep(0.1)
                continue

            if not self._first_request:
                self._sleep_mgr.sleep()
            self._first_request = False

            magnets, actor_info, success, used_cf = self._try_direct_then_cf(task)
            if success:
                cf_tag = " +CF" if used_cf else ""
                logger.info(
                    f"[{task.entry_index}] "
                    f"Parsed {task.entry.get('video_code', '')}{cf_tag} "
                    f"[{self.proxy_name}]"
                )
                self.result_queue.put(DetailResult(
                    task=task, magnets=magnets, actor_info=actor_info,
                    parse_success=True, used_cf_bypass=used_cf,
                ))
            else:
                task.failed_proxies.add(self.proxy_name)
                task.retry_count += 1
                self.detail_queue.put(task)
                logger.info(
                    f"[{self.proxy_name}] [{task.entry_index}] "
                    f"Failed {task.entry.get('video_code', '')}, re-queued "
                    f"(tried {len(task.failed_proxies)}/{self.total_workers} proxies)"
                )


def process_detail_entries_parallel(
    entries: List[dict],
    phase: int,
    history_data: dict,
    history_file: str,
    csv_path: str,
    fieldnames: list,
    dry_run: bool,
    use_history_for_saving: bool,
    use_cookie: bool,
    is_adhoc_mode: bool,
    ban_log_file: str,
) -> dict:
    """Process detail entries in parallel using one worker per proxy.

    Returns a dict with statistics keys:
        rows, skipped_history, failed, no_new_torrents
    """
    total_entries = len(entries)
    phase_prefix = '' if phase == 1 else 'P2-'

    detail_queue: queue_module.Queue[Optional[DetailTask]] = queue_module.Queue()
    result_queue: queue_module.Queue[DetailResult] = queue_module.Queue()

    all_workers: List[ProxyWorker] = []
    for idx, proxy_cfg in enumerate(PROXY_POOL):
        w = ProxyWorker(
            worker_id=idx,
            proxy_config=proxy_cfg,
            detail_queue=detail_queue,
            result_queue=result_queue,
            total_workers=len(PROXY_POOL),
            use_cookie=use_cookie,
            is_adhoc_mode=is_adhoc_mode,
            movie_sleep_min=MOVIE_SLEEP_MIN,
            movie_sleep_max=MOVIE_SLEEP_MAX,
            fallback_cooldown=FALLBACK_COOLDOWN,
            ban_log_file=ban_log_file,
            all_workers=all_workers,
        )
        all_workers.append(w)

    tasks_submitted = 0
    local_parsed_links: set = set()

    for i, entry in enumerate(entries, 1):
        href = entry['href']
        if href in parsed_links or href in local_parsed_links:
            continue
        local_parsed_links.add(href)

        if has_complete_subtitles(href, history_data):
            logger.info(
                f"[{phase_prefix}{i}/{total_entries}] [Page {entry['page']}] "
                f"Skipping {entry['video_code']} — already has subtitle and hacked_subtitle in history"
            )
            continue

        detail_url = urljoin(BASE_URL, href)
        entry_index = f"{phase_prefix}{i}/{total_entries}"
        logger.debug(f"[{entry_index}] [Page {entry['page']}] Queued {entry['video_code'] or href}")
        detail_queue.put(DetailTask(
            url=detail_url,
            entry=entry,
            phase=phase,
            entry_index=entry_index,
        ))
        tasks_submitted += 1

    parsed_links.update(local_parsed_links)

    skipped_history = len(local_parsed_links) - tasks_submitted

    if tasks_submitted == 0:
        logger.info(f"Phase {phase}: No detail tasks to process (all filtered)")
        return {'rows': [], 'skipped_history': skipped_history, 'failed': 0, 'no_new_torrents': 0}

    logger.info(
        f"Phase {phase}: Starting {len(all_workers)} workers for {tasks_submitted} detail tasks "
        f"({skipped_history} skipped by history)"
    )
    for w in all_workers:
        w.start()

    rows: list = []
    phase_rows: list = []
    failed = 0
    no_new_torrents = 0
    results_received = 0

    while results_received < tasks_submitted:
        result: DetailResult = result_queue.get()
        results_received += 1
        task = result.task
        entry = task.entry
        href = entry['href']
        page_num = entry['page']
        idx_str = task.entry_index

        if not result.parse_success:
            logger.error(f"[{idx_str}] [Page {page_num}] Failed after all workers exhausted")
            failed += 1
            continue

        magnet_links = extract_magnets(result.magnets, idx_str)

        should_process, history_torrent_types = should_process_movie(
            href, history_data, phase, magnet_links,
        )
        if not should_process:
            skipped_history += 1
            continue

        row = create_csv_row_with_history_filter(href, entry, page_num, '', magnet_links, history_data)
        row['video_code'] = entry['video_code']

        _has_any, has_new_torrents, should_include_in_report = check_torrent_status(row)

        if should_include_in_report:
            write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
            rows.append(row)
            phase_rows.append(row)

            if use_history_for_saving and not dry_run and has_new_torrents:
                new_magnet_links = collect_new_magnet_links(row, magnet_links)
                if new_magnet_links:
                    save_parsed_movie_to_history(
                        history_file, href, phase, entry['video_code'], new_magnet_links,
                    )
        else:
            no_new_torrents += 1

    for _ in all_workers:
        detail_queue.put(None)
    for w in all_workers:
        w.join(timeout=10)

    logger.info(
        f"Phase {phase} parallel completed: {total_entries} discovered, "
        f"{len(phase_rows)} processed, {skipped_history} skipped (history), "
        f"{no_new_torrents} no new torrents, {failed} failed"
    )
    return {
        'rows': phase_rows,
        'skipped_history': skipped_history,
        'failed': failed,
        'no_new_torrents': no_new_torrents,
    }


def check_torrent_status(row: dict) -> Tuple[bool, bool, bool]:
    """Determine download status for a CSV row's torrent fields.

    Returns:
        (has_any_torrents, has_new_torrents, should_include_in_report)
    """
    _TORRENT_FIELDS = ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle')
    has_any = any(row[f] for f in _TORRENT_FIELDS)
    has_new = any(
        row[f] and row[f] != '[DOWNLOADED PREVIOUSLY]'
        for f in _TORRENT_FIELDS
    )
    should_include = has_new or (INCLUDE_DOWNLOADED_IN_REPORT and has_any)
    return has_any, has_new, should_include


def collect_new_magnet_links(row: dict, magnet_links: dict) -> dict:
    """Extract magnet links that haven't been downloaded previously."""
    new_magnets = {}
    for mtype in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
        if row[mtype] and row[mtype] != '[DOWNLOADED PREVIOUSLY]':
            new_magnets[mtype] = magnet_links.get(mtype, '')
    return new_magnets


def process_phase_entries_sequential(
    entries: List[dict],
    phase: int,
    history_data: dict,
    history_file: str,
    csv_path: str,
    fieldnames: list,
    dry_run: bool,
    use_history_for_saving: bool,
    use_cookie: bool,
    is_adhoc_mode: bool,
    session,
    use_proxy: bool,
    use_cf_bypass: bool,
) -> dict:
    """Process detail entries sequentially (single-threaded mode).

    Mirrors the interface of ``process_detail_entries_parallel`` with two
    additional return keys (``use_proxy``, ``use_cf_bypass``) because
    sequential fallback may update global proxy/CF state.

    Returns a dict with keys:
        rows, skipped_history, failed, no_new_torrents,
        use_proxy, use_cf_bypass
    """
    total_entries = len(entries)
    phase_prefix = '' if phase == 1 else f'P{phase}-'

    phase_rows: list = []
    skipped_history = 0
    failed = 0
    no_new_torrents = 0
    pending_movie_sleep = False

    for i, entry in enumerate(entries, 1):
        href = entry['href']
        page_num = entry['page']

        if href in parsed_links:
            logger.info(f"[{phase_prefix}{i}/{total_entries}] [Page {page_num}] Skipping duplicate entry in current run")
            continue

        parsed_links.add(href)

        if has_complete_subtitles(href, history_data):
            logger.info(
                f"[{phase_prefix}{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - already has subtitle and hacked_subtitle in history"
            )
            skipped_history += 1
            continue

        if pending_movie_sleep:
            movie_sleep_mgr.sleep()
            pending_movie_sleep = False

        detail_url = urljoin(BASE_URL, href)
        entry_index = f"{phase_prefix}{i}/{total_entries}"
        logger.info(f"[{entry_index}] [Page {page_num}] Processing {entry['video_code'] or href}")

        magnets, actor_info, parse_success, effective_use_proxy, effective_use_cf_bypass = fetch_detail_page_with_fallback(
            detail_url, session,
            use_cookie=use_cookie,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
            entry_index=entry_index,
            is_adhoc_mode=is_adhoc_mode,
        )

        fallback_triggered = parse_success and (effective_use_proxy != use_proxy or effective_use_cf_bypass != use_cf_bypass)
        if parse_success and effective_use_cf_bypass != use_cf_bypass:
            use_cf_bypass = effective_use_cf_bypass
        if parse_success and effective_use_proxy != use_proxy:
            use_proxy = effective_use_proxy

        if not parse_success and not magnets:
            logger.error(f"[{entry_index}] [Page {page_num}] Failed to fetch/parse detail page after all fallback attempts")
            failed += 1
            pending_movie_sleep = True
            continue

        magnet_links = extract_magnets(magnets, entry_index)

        should_process, history_torrent_types = should_process_movie(href, history_data, phase, magnet_links)

        if not should_process:
            if history_torrent_types and 'hacked_subtitle' in history_torrent_types and 'subtitle' in history_torrent_types:
                logger.debug(f"[{entry_index}] [Page {page_num}] Skipping based on history rules (history types: {history_torrent_types})")
            else:
                logger.debug(f"[{entry_index}] [Page {page_num}] Should process, missing preferred types: {get_missing_torrent_types(history_torrent_types, [])}")
            skipped_history += 1
            pending_movie_sleep = True
            continue

        row = create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links, history_data)
        row['video_code'] = entry['video_code']

        _has_any, has_new_torrents, should_include_in_report = check_torrent_status(row)

        if should_include_in_report:
            write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
            phase_rows.append(row)

            if use_history_for_saving and not dry_run and has_new_torrents:
                new_magnet_links = collect_new_magnet_links(row, magnet_links)
                if new_magnet_links:
                    save_parsed_movie_to_history(history_file, href, phase, entry['video_code'], new_magnet_links)
        else:
            no_new_torrents += 1

        if fallback_triggered:
            logger.debug(f"[{entry_index}] Applying fallback cooldown: {FALLBACK_COOLDOWN}s")
            time.sleep(FALLBACK_COOLDOWN)
            pending_movie_sleep = False
        else:
            pending_movie_sleep = True

    logger.info(
        f"Phase {phase} completed: {total_entries} movies discovered, "
        f"{len(phase_rows)} processed, {skipped_history} skipped (history), "
        f"{no_new_torrents} no new torrents, {failed} failed"
    )
    return {
        'rows': phase_rows,
        'skipped_history': skipped_history,
        'failed': failed,
        'no_new_torrents': no_new_torrents,
        'use_proxy': use_proxy,
        'use_cf_bypass': use_cf_bypass,
    }


def log_phase_summary(phase_name: str, phase_rows: list) -> None:
    """Log torrent type statistics for a single processing phase."""
    logger.info("=" * 30)
    logger.info(f"{phase_name} SUMMARY")
    logger.info("=" * 30)
    logger.info(f"{phase_name} entries found: {len(phase_rows)}")
    if phase_rows:
        n = len(phase_rows)
        sub = sum(1 for r in phase_rows if r['subtitle'])
        hsub = sum(1 for r in phase_rows if r['hacked_subtitle'])
        hnosub = sum(1 for r in phase_rows if r['hacked_no_subtitle'])
        nosub = sum(1 for r in phase_rows if r['no_subtitle'])
        logger.info(f"  - Subtitle torrents: {sub} ({sub / n * 100:.1f}%)")
        logger.info(f"  - Hacked subtitle torrents: {hsub} ({hsub / n * 100:.1f}%)")
        logger.info(f"  - Hacked no-subtitle torrents: {hnosub} ({hnosub / n * 100:.1f}%)")
        logger.info(f"  - No-subtitle torrents: {nosub} ({nosub / n * 100:.1f}%)")
    else:
        logger.info(f"  - No entries found in {phase_name}")


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


def fetch_all_index_pages(
    session, start_page: int, end_page: int, parse_all: bool,
    phase_mode: str, custom_url: Optional[str], ignore_release_date: bool,
    use_proxy: bool, use_cf_bypass: bool, max_consecutive_empty: int,
    output_csv: str, output_dated_dir: str, csv_path: str,
    user_specified_output: bool,
    parsed_movies_history_phase1: dict, parsed_movies_history_phase2: dict,
) -> dict:
    """Fetch and parse all index pages, collecting entries for both phases.

    Returns a dict with keys:
        all_index_results_phase1, all_index_results_phase2,
        any_proxy_banned, use_proxy, use_cf_bypass, csv_path
    """
    all_index_results_phase1: list = []
    all_index_results_phase2: list = []
    any_proxy_banned = False
    last_valid_page = 0

    logger.info("=" * 75)
    logger.info("Fetching and parsing index pages")
    logger.info("=" * 75)

    page_num = start_page
    consecutive_empty_pages = 0
    csv_name_resolved = False

    while True:
        page_url = get_page_url(page_num, custom_url=custom_url)
        logger.debug(f"[Page {page_num}] Fetching: {page_url}")

        index_html, has_movie_list, proxy_was_banned, effective_use_proxy, effective_use_cf_bypass, is_valid_empty_page = fetch_index_page_with_fallback(
            page_url, session,
            use_cookie=custom_url is not None,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
            page_num=page_num,
            is_adhoc_mode=custom_url is not None,
        )

        if has_movie_list and effective_use_cf_bypass != use_cf_bypass:
            use_cf_bypass = effective_use_cf_bypass
        if has_movie_list and effective_use_proxy != use_proxy:
            use_proxy = effective_use_proxy

        if proxy_was_banned:
            any_proxy_banned = True

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

        p1_count = 0
        p2_count = 0

        if phase_mode in ['1', 'all']:
            page_results = parse_index(index_html, page_num, phase=1,
                                       disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                                       is_adhoc_mode=(custom_url is not None))
            p1_count = len(page_results)
            if p1_count > 0:
                all_index_results_phase1.extend(page_results)

        if phase_mode in ['2', 'all']:
            page_results_p2 = parse_index(index_html, page_num, phase=2,
                                          disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                                          is_adhoc_mode=(custom_url is not None))
            p2_count = len(page_results_p2)
            if p2_count > 0:
                all_index_results_phase2.extend(page_results_p2)

        if phase_mode == 'all':
            logger.info(f"[Page {page_num:2d}] Found {p1_count:3d} entries for phase 1, {p2_count:3d} for phase 2")
        elif phase_mode == '1':
            logger.info(f"[Page {page_num:2d}] Found {p1_count:3d} entries for phase 1")
        elif phase_mode == '2':
            logger.info(f"[Page {page_num:2d}] Found {p2_count:3d} entries for phase 2")

        last_valid_page = page_num
        consecutive_empty_pages = 0

        if custom_url is not None and not csv_name_resolved and not user_specified_output:
            url_type = detect_url_type(custom_url)
            if url_type in ('actors', 'makers', 'publishers', 'series', 'directors', 'video_codes'):
                resolved_csv_name = generate_output_csv_name_from_html(custom_url, index_html)
                if resolved_csv_name != output_csv:
                    output_csv = resolved_csv_name
                    csv_path = os.path.join(output_dated_dir, output_csv)
                    logger.info(f"[AdHoc] Updated CSV path: {csv_path}")
            csv_name_resolved = True

        if not parse_all and page_num >= end_page:
            break

        page_num += 1
        time.sleep(PAGE_SLEEP)

    logger.info(f"Fetched and parsed {last_valid_page - start_page + 1 if last_valid_page >= start_page else 0} pages")

    _est_skip = sum(
        1 for e in all_index_results_phase1 if has_complete_subtitles(e['href'], parsed_movies_history_phase1)
    ) + sum(
        1 for e in all_index_results_phase2 if has_complete_subtitles(e['href'], parsed_movies_history_phase2)
    )
    _est_n = len(all_index_results_phase1) + len(all_index_results_phase2) - _est_skip
    logger.info(f"Estimated processing volume: N={_est_n} (total={len(all_index_results_phase1)+len(all_index_results_phase2)}, pre-skip={_est_skip})")
    movie_sleep_mgr.apply_volume_multiplier(_est_n)

    return {
        'all_index_results_phase1': all_index_results_phase1,
        'all_index_results_phase2': all_index_results_phase2,
        'any_proxy_banned': any_proxy_banned,
        'use_proxy': use_proxy,
        'use_cf_bypass': use_cf_bypass,
        'csv_path': csv_path,
    }


def generate_summary_report(
    *, phase_mode, parse_all, start_page, end_page, max_consecutive_empty,
    phase1_rows, phase2_rows, rows,
    use_history_for_loading, ignore_history,
    skipped_history_count, failed_count, no_new_torrents_count,
    csv_path, dry_run, use_history_for_saving,
    use_proxy, any_proxy_banned, any_proxy_banned_phase2,
) -> None:
    """Log the final summary report, proxy stats, and check exit conditions."""
    logger.info("=" * 75)
    logger.info("SUMMARY REPORT")
    logger.info("=" * 75)
    if parse_all:
        logger.info(f"Pages processed: {start_page} to last page with results")
    else:
        logger.info(f"Pages processed: {start_page} to {end_page}")

    logger.info(f"Tolerance mechanism: Stops after {max_consecutive_empty} consecutive pages with no HTML content")

    if phase_mode in ['1', 'all']:
        log_phase_summary("PHASE 1", phase1_rows)
    if phase_mode in ['2', 'all']:
        log_phase_summary("PHASE 2", phase2_rows)

    total_discovered = len(rows) + skipped_history_count + no_new_torrents_count + failed_count
    logger.info("=" * 30)
    logger.info("OVERALL SUMMARY")
    logger.info("=" * 30)
    logger.info(f"Total movies discovered: {total_discovered}")
    logger.info(f"Successfully processed: {len(rows)}")
    if use_history_for_loading and not ignore_history:
        logger.info(f"Skipped already parsed in previous runs: {skipped_history_count}")
    elif ignore_history:
        logger.info("History reading was disabled (--ignore-history), but results will still be saved to history")
    logger.info(f"No new torrents to download: {no_new_torrents_count}")
    logger.info(f"Failed to fetch/parse: {failed_count}")
    logger.info(f"Current parsed links in memory: {len(parsed_links)}")

    if rows:
        n = len(rows)
        sub = sum(1 for r in rows if r['subtitle'])
        hsub = sum(1 for r in rows if r['hacked_subtitle'])
        hnosub = sum(1 for r in rows if r['hacked_no_subtitle'])
        nosub = sum(1 for r in rows if r['no_subtitle'])
        logger.info(f"Overall subtitle torrents: {sub} ({sub / n * 100:.1f}%)")
        logger.info(f"Overall hacked subtitle torrents: {hsub} ({hsub / n * 100:.1f}%)")
        logger.info(f"Overall hacked no-subtitle torrents: {hnosub} ({hnosub / n * 100:.1f}%)")
        logger.info(f"Overall no-subtitle torrents: {nosub} ({nosub / n * 100:.1f}%)")

    if not dry_run:
        logger.info(f"Results saved to: {csv_path}")
        if use_history_for_saving:
            logger.info(f"History saved to: {os.path.join(REPORTS_DIR, PARSED_MOVIES_CSV)}")
        print(f"SPIDER_OUTPUT_CSV={csv_path}")
    logger.info("=" * 75)

    if use_proxy and PROXY_MODE in ('pool', 'single') and global_proxy_pool is not None:
        logger.info("")
        global_proxy_pool.log_statistics(level=logging.INFO)
        logger.info("")
        logger.info("=" * 75)
        logger.info("PROXY BAN STATUS")
        logger.info("=" * 75)
        ban_summary = global_proxy_pool.get_ban_summary(include_ip=False)
        logger.info(ban_summary)
        logger.info("=" * 75)

    if proxy_ban_html_files:
        logger.info("")
        logger.info("=" * 75)
        logger.info("PROXY BAN HTML FILES")
        logger.info("=" * 75)
        logger.info(f"Saved {len(proxy_ban_html_files)} proxy ban HTML file(s) for debugging:")
        for html_file in proxy_ban_html_files:
            logger.info(f"  - {html_file}")
        logger.info("=" * 75)
        print(f"PROXY_BAN_HTML_FILES={','.join(proxy_ban_html_files)}")

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
        sys.exit(2)

    if len(rows) == 0 and use_proxy:
        logger.warning("=" * 75)
        logger.warning("WARNING: No entries found while using proxy")
        logger.warning("=" * 75)
        logger.warning("This might indicate proxy issues or CF bypass service problems.")


def setup_proxy_pool(ban_log_file: str, use_proxy: bool) -> None:
    """Initialize the global proxy pool from configuration."""
    global global_proxy_pool

    if PROXY_POOL and len(PROXY_POOL) > 0:
        if PROXY_MODE == 'pool':
            logger.info(f"Initializing proxy pool with {len(PROXY_POOL)} proxies...")
            global_proxy_pool = create_proxy_pool_from_config(
                PROXY_POOL,
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES,
                ban_log_file=ban_log_file
            )
            logger.info("Proxy pool initialized successfully")
            logger.info(f"Cooldown: {PROXY_POOL_COOLDOWN_SECONDS}s, Max failures before cooldown: {PROXY_POOL_MAX_FAILURES}")
        elif PROXY_MODE == 'single':
            logger.info("Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES,
                ban_log_file=ban_log_file
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
            max_failures=PROXY_POOL_MAX_FAILURES,
            ban_log_file=ban_log_file
        )
    else:
        if use_proxy:
            logger.warning("Proxy enabled but no proxy configuration found (neither PROXY_POOL nor PROXY_HTTP/PROXY_HTTPS)")
        global_proxy_pool = None


def main():
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
    use_cf_bypass = False  # CF bypass is only activated automatically during fallback
    max_movies_phase1 = args.max_movies_phase1
    max_movies_phase2 = args.max_movies_phase2
    sequential = args.sequential

    ban_log_file = os.path.join(REPORTS_DIR, 'proxy_bans.csv')
    setup_proxy_pool(ban_log_file, use_proxy)
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
            output_csv = generate_output_csv_name(custom_url, use_proxy=use_proxy)
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
    # Log mode information
    if use_proxy:
        logger.info("MODE: Proxy (CF bypass available as automatic fallback)")
    else:
        logger.info("MODE: Direct (CF bypass available as automatic fallback)")
    if CF_BYPASS_ENABLED:
        logger.info(f"CF Bypass: Enabled as fallback (service port: {CF_BYPASS_SERVICE_PORT})")
    else:
        logger.info("CF Bypass: Globally disabled via CF_BYPASS_ENABLED=False in config.py")
    
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

    # Determine whether to use parallel detail processing
    use_parallel = (
        use_proxy
        and not sequential
        and PROXY_MODE == 'pool'
        and PROXY_POOL
        and len(PROXY_POOL) > 1
    )
    if use_parallel:
        logger.info(f"PARALLEL MODE: {len(PROXY_POOL)} workers (one per proxy) for detail page processing")
    elif use_proxy and PROXY_MODE == 'pool' and sequential:
        logger.info("SEQUENTIAL MODE: Parallel disabled by --sequential flag")

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
    skipped_history_count = 0  # Track entries skipped due to history
    failed_count = 0  # Track entries that failed to fetch/parse
    no_new_torrents_count = 0  # Track entries with no new torrents to download
    phase1_skipped_history_actual = 0  # Actual count during processing
    phase1_failed = 0  # Track entries that failed to fetch/parse in phase 1
    phase1_no_new_torrents = 0  # Track entries with no new torrents in phase 1
    phase2_skipped_history_actual = 0  # Actual count during processing
    phase2_failed = 0  # Track entries that failed to fetch/parse in phase 2
    phase2_no_new_torrents = 0  # Track entries with no new torrents in phase 2

    idx_result = fetch_all_index_pages(
        session=session, start_page=start_page, end_page=end_page,
        parse_all=parse_all, phase_mode=phase_mode, custom_url=custom_url,
        ignore_release_date=ignore_release_date, use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass, max_consecutive_empty=max_consecutive_empty,
        output_csv=output_csv, output_dated_dir=output_dated_dir,
        csv_path=csv_path, user_specified_output=bool(args.output_file),
        parsed_movies_history_phase1=parsed_movies_history_phase1,
        parsed_movies_history_phase2=parsed_movies_history_phase2,
    )
    all_index_results_phase1 = idx_result['all_index_results_phase1']
    all_index_results_phase2 = idx_result['all_index_results_phase2']
    any_proxy_banned = idx_result['any_proxy_banned']
    use_proxy = idx_result['use_proxy']
    use_cf_bypass = idx_result['use_cf_bypass']
    csv_path = idx_result['csv_path']

    # ========================================
    # Process Phase 1 entries
    # ========================================
    if phase_mode in ['1', 'all']:
        logger.info("=" * 75)
        
        # Apply max_movies_phase1 limit if specified (only positive values)
        original_count_phase1 = len(all_index_results_phase1)
        if max_movies_phase1 is not None and max_movies_phase1 > 0 and original_count_phase1 > max_movies_phase1:
            logger.info(f"PHASE 1: Discovered {original_count_phase1} entries, limiting to {max_movies_phase1} (--max-movies-phase1)")
            all_index_results_phase1 = all_index_results_phase1[:max_movies_phase1]
        
        if custom_url is not None:
            logger.info(f"PHASE 1: Processing {len(all_index_results_phase1)} entries with subtitle (AD HOC MODE)")
        else:
            logger.info(f"PHASE 1: Processing {len(all_index_results_phase1)} entries with subtitle")
        logger.info("=" * 75)

        total_entries_phase1 = len(all_index_results_phase1)

        if use_parallel:
            # --- Parallel detail processing ---
            p1_result = process_detail_entries_parallel(
                entries=all_index_results_phase1,
                phase=1,
                history_data=parsed_movies_history_phase1,
                history_file=history_file,
                csv_path=csv_path,
                fieldnames=fieldnames,
                dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                use_cookie=custom_url is not None,
                is_adhoc_mode=custom_url is not None,
                ban_log_file=ban_log_file,
            )
            phase1_rows = p1_result['rows']
            rows.extend(phase1_rows)
            phase1_skipped_history_actual = p1_result['skipped_history']
            skipped_history_count += phase1_skipped_history_actual
            phase1_failed = p1_result['failed']
            failed_count += phase1_failed
            phase1_no_new_torrents = p1_result['no_new_torrents']
            no_new_torrents_count += phase1_no_new_torrents
        else:
            # --- Sequential detail processing ---
            p1_result = process_phase_entries_sequential(
                entries=all_index_results_phase1,
                phase=1,
                history_data=parsed_movies_history_phase1,
                history_file=history_file,
                csv_path=csv_path,
                fieldnames=fieldnames,
                dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                use_cookie=custom_url is not None,
                is_adhoc_mode=custom_url is not None,
                session=session,
                use_proxy=use_proxy,
                use_cf_bypass=use_cf_bypass,
            )
            phase1_rows = p1_result['rows']
            rows.extend(phase1_rows)
            phase1_skipped_history_actual = p1_result['skipped_history']
            skipped_history_count += phase1_skipped_history_actual
            phase1_failed = p1_result['failed']
            failed_count += phase1_failed
            phase1_no_new_torrents = p1_result['no_new_torrents']
            no_new_torrents_count += phase1_no_new_torrents
            use_proxy = p1_result['use_proxy']
            use_cf_bypass = p1_result['use_cf_bypass']

    # ========================================
    # Process Phase 2 entries (already parsed during fetch)
    # ========================================
    # Phase 2: Collect entries with only "今日新種"/"昨日新種" tag (filtered by quality)
    if phase_mode in ['2', 'all']:
        if phase_mode == 'all':
            if total_entries_phase1 > 0:
                t = movie_sleep_mgr.get_sleep_time()
                logger.info(f"Phase transition cooldown: {t}s before Phase 2")
                time.sleep(t)
            else:
                logger.info("Phase 1 had no entries to process, skipping phase transition cooldown")
        
        logger.info("=" * 75)
        
        # Apply max_movies_phase2 limit if specified (only positive values)
        original_count_phase2 = len(all_index_results_phase2)
        if max_movies_phase2 is not None and max_movies_phase2 > 0 and original_count_phase2 > max_movies_phase2:
            logger.info(f"PHASE 2: Discovered {original_count_phase2} entries, limiting to {max_movies_phase2} (--max-movies-phase2)")
            all_index_results_phase2 = all_index_results_phase2[:max_movies_phase2]
        
        if custom_url is not None:
            logger.info(f"PHASE 2: Processing {len(all_index_results_phase2)} entries (AD HOC MODE - all filters disabled)")
        else:
            logger.info(f"PHASE 2: Processing {len(all_index_results_phase2)} entries (rate > {PHASE2_MIN_RATE}, comments > {PHASE2_MIN_COMMENTS})")
        logger.info("=" * 75)

        total_entries_phase2 = len(all_index_results_phase2)

        if use_parallel:
            # --- Parallel detail processing ---
            p2_result = process_detail_entries_parallel(
                entries=all_index_results_phase2,
                phase=2,
                history_data=parsed_movies_history_phase2,
                history_file=history_file,
                csv_path=csv_path,
                fieldnames=fieldnames,
                dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                use_cookie=custom_url is not None,
                is_adhoc_mode=custom_url is not None,
                ban_log_file=ban_log_file,
            )
            phase2_rows = p2_result['rows']
            rows.extend(phase2_rows)
            phase2_skipped_history_actual = p2_result['skipped_history']
            skipped_history_count += phase2_skipped_history_actual
            phase2_failed = p2_result['failed']
            failed_count += phase2_failed
            phase2_no_new_torrents = p2_result['no_new_torrents']
            no_new_torrents_count += phase2_no_new_torrents
        else:
            # --- Sequential detail processing ---
            p2_result = process_phase_entries_sequential(
                entries=all_index_results_phase2,
                phase=2,
                history_data=parsed_movies_history_phase2,
                history_file=history_file,
                csv_path=csv_path,
                fieldnames=fieldnames,
                dry_run=dry_run,
                use_history_for_saving=use_history_for_saving,
                use_cookie=custom_url is not None,
                is_adhoc_mode=custom_url is not None,
                session=session,
                use_proxy=use_proxy,
                use_cf_bypass=use_cf_bypass,
            )
            phase2_rows = p2_result['rows']
            rows.extend(phase2_rows)
            phase2_skipped_history_actual = p2_result['skipped_history']
            skipped_history_count += phase2_skipped_history_actual
            phase2_failed = p2_result['failed']
            failed_count += phase2_failed
            phase2_no_new_torrents = p2_result['no_new_torrents']
            no_new_torrents_count += phase2_no_new_torrents
            use_proxy = p2_result['use_proxy']
            use_cf_bypass = p2_result['use_cf_bypass']

    if not dry_run:
        logger.info(f"CSV file written incrementally to: {csv_path}")

    generate_summary_report(
        phase_mode=phase_mode, parse_all=parse_all,
        start_page=start_page, end_page=end_page,
        max_consecutive_empty=max_consecutive_empty,
        phase1_rows=phase1_rows, phase2_rows=phase2_rows, rows=rows,
        use_history_for_loading=use_history_for_loading,
        ignore_history=ignore_history,
        skipped_history_count=skipped_history_count,
        failed_count=failed_count,
        no_new_torrents_count=no_new_torrents_count,
        csv_path=csv_path, dry_run=dry_run,
        use_history_for_saving=use_history_for_saving,
        use_proxy=use_proxy,
        any_proxy_banned=any_proxy_banned,
        any_proxy_banned_phase2=any_proxy_banned_phase2,
    )

    # Git commit spider results (only if credentials are available)
    from_pipeline = args.from_pipeline if hasattr(args, 'from_pipeline') else False

    if not dry_run and has_git_credentials(GIT_USERNAME, GIT_PASSWORD):
        logger.info("Committing spider results...")
        flush_log_handlers()

        files_to_commit = [
            REPORTS_DIR,
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
            git_branch=GIT_BRANCH,
        )
    elif not dry_run:
        logger.info("Skipping git commit - no credentials provided (commit will be handled by workflow)")


if __name__ == '__main__':
    main() 
