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
                        help='Ignore history file and scrape all pages from start to end')

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
        module_name: Module name for proxy control ('spider_index', 'spider_detail', 'spider_age_verification')
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
                            use_proxy=u_proxy, module_name='spider_index',
                            use_cf_bypass=u_cf)
            if html:
                soup = BeautifulSoup(html, 'html.parser')
                movie_list = soup.find('div', class_='movie-list h cols-4 vcols-8')
                if movie_list:
                    logger.debug(f"[Page {page_num}] Success: {context_msg}")
                    return html, True, False
                else:
                    # No movie list - check if this is a valid empty page or a failed fetch
                    page_text = soup.get_text()
                    title = soup.find('title')
                    title_text = title.text.strip() if title else ""
                    age_modal = soup.find('div', class_='modal is-active over18-modal')
                    
                    # Check if it's a login page
                    is_login_page = '登入' in title_text or 'login' in title_text.lower()
                    
                    # Check if it's a valid empty results page
                    # "No content yet" means the page exists but has no movies
                    has_no_content_msg = 'No content yet' in page_text or 'No result' in page_text
                    
                    if not is_login_page and not age_modal and has_no_content_msg:
                        logger.info(f"[Page {page_num}] Page exists but has no content ('No content yet' detected)")
                        # This is a valid empty page - no need to retry
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
                            use_proxy=u_proxy, module_name='spider_detail',
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


def write_csv(rows, csv_path, fieldnames, dry_run=False, append_mode=False):
    """Write results to CSV file or print if dry-run"""
    if dry_run:
        logger.info(f"[DRY RUN] Would write {len(rows)} entries to {csv_path}")
        logger.info("[DRY RUN] Sample entries:")
        for i, row in enumerate(rows[:3]):  # Show first 3 entries
            logger.info(f"[DRY RUN] Entry {i + 1}: {row['video_code']} (Page {row['page']})")
        if len(rows) > 3:
            logger.info(f"[DRY RUN] ... and {len(rows) - 3} more entries")
        return

    # Determine if we need to write header (only if file doesn't exist or not in append mode)
    write_header = not os.path.exists(csv_path) or not append_mode
    
    mode = 'a' if append_mode else 'w'
    logger.debug(f"[FINISH] Writing results to {csv_path} (mode: {mode})")
    
    with open(csv_path, mode, newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


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


def extract_url_part_after_javdb(url):
    """
    Extract the part of URL after javdb.com and convert it to a filename-safe format.
    Args:
        url: The custom URL (e.g., 'https://javdb.com/actors/EvkJ')
    Returns:
        str: The extracted part converted to filename-safe format (e.g., 'actors_EvkJ')
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
                filename_part = after_domain.replace('/', '_')
                if '?' in filename_part:
                    filename_part = filename_part.split('?')[0]
                return filename_part
    except Exception as e:
        logger.warning(f"Error extracting URL part from {url}: {e}")
    return 'custom_url'


def generate_output_csv_name(custom_url=None):
    """
    Generate the output CSV filename based on whether a custom URL is provided.
    Args:
        custom_url: Custom URL if provided, None otherwise
    Returns:
        str: The generated CSV filename
    """
    if custom_url:
        url_part = extract_url_part_after_javdb(custom_url)
        today_date = datetime.now().strftime("%Y%m%d")
        return f'Javdb_AdHoc_{url_part}_{today_date}.csv'
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
    parse_all = args.all
    ignore_release_date = args.ignore_release_date
    use_proxy = args.use_proxy
    use_cf_bypass = args.use_cf_bypass
    
    # Initialize proxy pool (always initialize if configured, even if not enabled by default)
    # This allows automatic fallback to proxy if direct connection fails
    if PROXY_POOL and len(PROXY_POOL) > 0:
        if PROXY_MODE == 'pool':
            # Full proxy pool mode with automatic failover
            logger.info(f"Initializing proxy pool with {len(PROXY_POOL)} proxies...")
            global_proxy_pool = create_proxy_pool_from_config(
                PROXY_POOL,
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES
            )
            logger.info(f"Proxy pool initialized successfully")
            logger.info(f"Cooldown: {PROXY_POOL_COOLDOWN_SECONDS}s, Max failures before cooldown: {PROXY_POOL_MAX_FAILURES}")
        elif PROXY_MODE == 'single':
            # Single proxy mode - only use first proxy from pool
            logger.info(f"Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],  # Only use first proxy
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES
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
            max_failures=PROXY_POOL_MAX_FAILURES
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
        output_csv = args.output_file if args.output_file else OUTPUT_CSV
        csv_path = os.path.join(output_dated_dir, output_csv)
        use_history_for_loading = True   # Check history for ad hoc mode (changed from False)
        use_history_for_saving = True    # Record to history for ad hoc mode
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
        if ignore_history:
            logger.info("AD HOC MODE: Will process all entries (ignoring history)")
        else:
            logger.info("AD HOC MODE: Will skip entries already in history (use --ignore-history to override)")
    if dry_run:
        logger.info("DRY RUN MODE: No CSV file will be written")
    if ignore_history:
        logger.info("IGNORE HISTORY: Will scrape all pages without checking history")
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
        
    while True:
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
        
        # If fallback was triggered (settings changed), apply cooldown but DON'T persist the settings
        if has_movie_list and (effective_use_proxy != use_proxy or effective_use_cf_bypass != use_cf_bypass):
            logger.info(f"[Page {page_num}] Fallback succeeded (Proxy={effective_use_proxy}, CF={effective_use_cf_bypass}). Applying {FALLBACK_COOLDOWN}s cooldown before next page...")
            time.sleep(FALLBACK_COOLDOWN)
        
        if proxy_was_banned:
            any_proxy_banned = True
        
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
        if phase_mode in ['2', 'all'] and custom_url is None:
            page_results_p2 = parse_index(index_html, page_num, phase=2,
                                          disable_new_releases_filter=ignore_release_date,
                                          is_adhoc_mode=False)
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
        logger.info(f"PHASE 1: Processing {len(all_index_results_phase1)} collected entries with subtitle")
        logger.info("=" * 75)

        # Process phase 1 entries
        total_entries_phase1 = len(all_index_results_phase1)

        for i, entry in enumerate(all_index_results_phase1, 1):
            href = entry['href']
            page_num = entry['page']
            fallback_triggered = False  # Track if fallback was triggered for this entry

            # Skip if already parsed in this session
            if href in parsed_links:
                logger.info(f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipping already parsed in this session")
                skipped_session_count += 1
                phase1_skipped_session += 1
                continue

            # Add to parsed links set for this session
            parsed_links.add(href)

            # Early skip check: if movie already has both subtitle and hacked_subtitle in history,
            # skip fetching detail page to avoid unnecessary network requests
            if has_complete_subtitles(href, parsed_movies_history_phase1):
                logger.info(f"[{i}/{total_entries_phase1}] [Page {page_num}] Skipping {entry['video_code']} - already has subtitle and hacked_subtitle in history")
                skipped_history_count += 1
                phase1_skipped_history_actual += 1
                continue

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
            
            # If fallback was triggered (settings changed), apply cooldown but DON'T persist the settings
            # Next item should start fresh with original settings and wait for fallback if needed
            fallback_triggered = parse_success and (effective_use_proxy != use_proxy or effective_use_cf_bypass != use_cf_bypass)
            if fallback_triggered:
                logger.info(f"[{i}/{total_entries_phase1}] Fallback succeeded (Proxy={effective_use_proxy}, CF={effective_use_cf_bypass}). Will apply {FALLBACK_COOLDOWN}s cooldown after processing...")
                # Note: NOT updating use_proxy/use_cf_bypass - next item will use original settings
            
            if not parse_success and not magnets:
                logger.error(f"[{i}/{total_entries_phase1}] [Page {page_num}] Failed to fetch/parse detail page after all fallback attempts")
                failed_count += 1
                phase1_failed += 1
                time.sleep(MOVIE_SLEEP)  # Respect rate limiting even on failure
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
                time.sleep(MOVIE_SLEEP)  # Respect rate limiting even when skipping
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
                if use_history_for_saving and not dry_run and not ignore_history:
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
            else:
                # Normal delay between items
                time.sleep(MOVIE_SLEEP)

        # Phase 1 statistics - use actual tracked counts
        # Verify: total_entries_phase1 == phase1_skipped_session + phase1_skipped_history_actual + len(phase1_rows) + phase1_no_new_torrents + phase1_failed
        logger.info(f"Phase 1 completed: {total_entries_phase1} movies discovered, {len(phase1_rows)} processed, {phase1_skipped_session} skipped (session), {phase1_skipped_history_actual} skipped (history), {phase1_no_new_torrents} no new torrents, {phase1_failed} failed")

    # ========================================
    # Process Phase 2 entries (already parsed during fetch)
    # ========================================
    # Phase 2: Collect entries with only "今日新種"/"昨日新種" tag (filtered by quality)
    if phase_mode in ['2', 'all']:
        # In ad hoc mode, phase 2 is skipped (all entries processed in phase 1)
        if custom_url is not None:
            logger.info("=" * 75)
            logger.info("PHASE 2: Skipped (AD HOC MODE - all entries processed in Phase 1)")
            logger.info("=" * 75)
            all_index_results_phase2 = []
        else:
            # Add cooldown delay between phases to avoid triggering Cloudflare protection
            if phase_mode == 'all':
                logger.info(f"Waiting {PHASE_TRANSITION_COOLDOWN} seconds before Phase 2")
                time.sleep(PHASE_TRANSITION_COOLDOWN)
            
            logger.info("=" * 75)
            logger.info(f"PHASE 2: Processing {len(all_index_results_phase2)} collected entries (rate > {PHASE2_MIN_RATE}, comments > {PHASE2_MIN_COMMENTS})")
            logger.info("=" * 75)

            # all_index_results_phase2 was already populated during the fetch phase

        # Process phase 2 entries
        total_entries_phase2 = len(all_index_results_phase2)

        for i, entry in enumerate(all_index_results_phase2, 1):
            href = entry['href']
            page_num = entry['page']
            fallback_triggered = False  # Track if fallback was triggered for this entry

            # Skip if already parsed in this session
            if href in parsed_links:
                logger.info(f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipping already parsed in this session")
                skipped_session_count += 1
                phase2_skipped_session += 1
                # No sleep needed here - we haven't made any request yet
                continue

            # Add to parsed links set for this session
            parsed_links.add(href)

            # Early skip check: if movie already has both subtitle and hacked_subtitle in history,
            # skip fetching detail page to avoid unnecessary network requests
            if has_complete_subtitles(href, parsed_movies_history_phase2):
                logger.info(f"[{i}/{total_entries_phase2}] [Page {page_num}] Skipping {entry['video_code']} - already has subtitle and hacked_subtitle in history")
                skipped_history_count += 1
                phase2_skipped_history_actual += 1
                continue

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
            
            # If fallback was triggered (settings changed), apply cooldown but DON'T persist the settings
            # Next item should start fresh with original settings and wait for fallback if needed
            fallback_triggered = parse_success and (effective_use_proxy != use_proxy or effective_use_cf_bypass != use_cf_bypass)
            if fallback_triggered:
                logger.info(f"[P2-{i}/{total_entries_phase2}] Fallback succeeded (Proxy={effective_use_proxy}, CF={effective_use_cf_bypass}). Will apply {FALLBACK_COOLDOWN}s cooldown after processing...")
                # Note: NOT updating use_proxy/use_cf_bypass - next item will use original settings
            
            if not parse_success and not magnets:
                logger.error(f"[{i}/{total_entries_phase2}] [Page {page_num}] Failed to fetch/parse detail page after all fallback attempts")
                failed_count += 1
                phase2_failed += 1
                time.sleep(MOVIE_SLEEP)  # Respect rate limiting even on failure
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
                time.sleep(MOVIE_SLEEP)  # Respect rate limiting even when skipping
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
                if use_history_for_saving and not dry_run and not ignore_history:
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
            else:
                # Normal delay between items
                time.sleep(MOVIE_SLEEP)

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
        logger.info("History checking was disabled (--ignore-history)")
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

