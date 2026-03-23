"""Global mutable state for the spider package.

Every module that needs to read or *mutate* shared state should
``import scripts.spider.state as state`` and access ``state.<var>``.
"""

import os
import re
import logging
import time
from typing import Optional, Dict
from datetime import datetime

from utils.logging_config import get_logger
from utils.proxy_pool import ProxyPool, create_proxy_pool_from_config
from utils.request_handler import RequestHandler, RequestConfig
from utils.path_helper import ensure_dated_dir

from scripts.spider.config_loader import (
    BASE_URL,
    CF_BYPASS_SERVICE_PORT, CF_BYPASS_ENABLED,
    CF_BYPASS_PORT_MAP,
    CF_TURNSTILE_COOLDOWN, FALLBACK_COOLDOWN,
    JAVDB_SESSION_COOKIE,
    PROXY_HTTP, PROXY_HTTPS, PROXY_MODULES, PROXY_MODE,
    PROXY_POOL, PROXY_POOL_COOLDOWN_SECONDS, PROXY_POOL_MAX_FAILURES,
    REPORTS_DIR,
    LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Mutable globals
# ---------------------------------------------------------------------------

global_proxy_pool: Optional[ProxyPool] = None
global_request_handler: Optional[RequestHandler] = None

parsed_links: set = set()
proxy_ban_html_files: list = []

login_attempted: bool = False
refreshed_session_cookie: Optional[str] = None
logged_in_proxy_name: Optional[str] = None

# Per-proxy and global login budget tracking
login_attempts_per_proxy: Dict[str, int] = {}
login_failures_per_proxy: Dict[str, int] = {}
login_total_attempts: int = 0
login_total_budget: int = len(PROXY_POOL) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT if PROXY_POOL else 0

always_bypass_time: Optional[int] = None
proxies_requiring_cf_bypass: Dict[str, float] = {}

# ---------------------------------------------------------------------------
# CF bypass helpers
# ---------------------------------------------------------------------------


def proxy_needs_cf_bypass(proxy_name: str) -> bool:
    """Check if a proxy is still within the configured CF bypass window."""
    if always_bypass_time is None:
        return False

    marked_at = proxies_requiring_cf_bypass.get(proxy_name)
    if marked_at is None:
        return False

    if always_bypass_time == 0:
        return True

    window_seconds = always_bypass_time * 60
    if time.time() - marked_at <= window_seconds:
        return True

    # Expired: fall back to direct-first behavior.
    proxies_requiring_cf_bypass.pop(proxy_name, None)
    return False


def mark_proxy_cf_bypass(proxy_name: str):
    """Mark a proxy for CF bypass reuse according to --always-bypass-time."""
    if always_bypass_time is None:
        return

    proxies_requiring_cf_bypass[proxy_name] = time.time()
    if always_bypass_time == 0:
        logger.info(f"Proxy '{proxy_name}' marked as requiring CF bypass for this runtime")
    else:
        logger.info(
            f"Proxy '{proxy_name}' marked for CF bypass reuse for {always_bypass_time} minute(s)"
        )

# ---------------------------------------------------------------------------
# Request delegation
# ---------------------------------------------------------------------------


def get_page(url, session=None, use_cookie=False, use_proxy=False,
             module_name='unknown', max_retries=3, use_cf_bypass=False):
    """Fetch a webpage via the global request handler."""
    if global_request_handler is None:
        logger.error("Request handler not initialized. Call initialize_request_handler() first.")
        return None
    return global_request_handler.get_page(
        url=url, session=session, use_cookie=use_cookie,
        use_proxy=use_proxy, module_name=module_name,
        max_retries=max_retries, use_cf_bypass=use_cf_bypass,
    )


def should_use_proxy_for_module(module_name: str, use_proxy_flag: bool) -> bool:
    if global_request_handler:
        return global_request_handler.should_use_proxy_for_module(module_name, use_proxy_flag)
    if not use_proxy_flag or not PROXY_MODULES:
        return False
    return 'all' in PROXY_MODULES or module_name in PROXY_MODULES


def extract_ip_from_proxy_url(proxy_url: str) -> Optional[str]:
    return RequestHandler.extract_ip_from_proxy_url(proxy_url)


def get_cf_bypass_service_url(proxy_ip: Optional[str] = None) -> str:
    if global_request_handler:
        return global_request_handler.get_cf_bypass_service_url(proxy_ip)
    if proxy_ip:
        return f"http://{proxy_ip}:{CF_BYPASS_SERVICE_PORT}"
    return f"http://127.0.0.1:{CF_BYPASS_SERVICE_PORT}"


def is_cf_bypass_failure(html_content: str) -> bool:
    return RequestHandler.is_cf_bypass_failure(html_content)

# ---------------------------------------------------------------------------
# Initialisation helpers (called from main)
# ---------------------------------------------------------------------------


def initialize_request_handler():
    """Create the global RequestHandler from configuration."""
    global global_request_handler
    from scripts.spider.sleep_manager import penalty_tracker as _pt
    config = RequestConfig(
        base_url=BASE_URL,
        cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
        cf_bypass_port_map=CF_BYPASS_PORT_MAP,
        cf_bypass_enabled=CF_BYPASS_ENABLED,
        cf_bypass_max_failures=3,
        cf_turnstile_cooldown=CF_TURNSTILE_COOLDOWN,
        fallback_cooldown=FALLBACK_COOLDOWN,
        javdb_session_cookie=JAVDB_SESSION_COOKIE,
        proxy_http=PROXY_HTTP,
        proxy_https=PROXY_HTTPS,
        proxy_modules=PROXY_MODULES,
        proxy_mode=PROXY_MODE,
    )
    global_request_handler = RequestHandler(
        proxy_pool=global_proxy_pool, config=config, penalty_tracker=_pt,
    )
    logger.info("Request handler initialized successfully")


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
                ban_log_file=ban_log_file,
            )
            logger.info("Proxy pool initialized successfully")
            logger.info(f"Cooldown: {PROXY_POOL_COOLDOWN_SECONDS}s, Max failures before cooldown: {PROXY_POOL_MAX_FAILURES}")
        elif PROXY_MODE == 'single':
            logger.info("Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
                cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
                max_failures=PROXY_POOL_MAX_FAILURES,
                ban_log_file=ban_log_file,
            )
            logger.info(f"Single proxy initialized: {PROXY_POOL[0].get('name', 'Main-Proxy')}")
    elif PROXY_HTTP or PROXY_HTTPS:
        logger.info("Using legacy PROXY_HTTP/PROXY_HTTPS configuration")
        legacy_proxy = {'name': 'Legacy-Proxy', 'http': PROXY_HTTP, 'https': PROXY_HTTPS}
        global_proxy_pool = create_proxy_pool_from_config(
            [legacy_proxy],
            cooldown_seconds=PROXY_POOL_COOLDOWN_SECONDS,
            max_failures=PROXY_POOL_MAX_FAILURES,
            ban_log_file=ban_log_file,
        )
    else:
        if use_proxy:
            logger.warning("Proxy enabled but no proxy configuration found (neither PROXY_POOL nor PROXY_HTTP/PROXY_HTTPS)")
        global_proxy_pool = None

# ---------------------------------------------------------------------------
# Directory / file helpers
# ---------------------------------------------------------------------------


def ensure_reports_dir():
    """Ensure the reports root directory exists (for history files)."""
    if not os.path.exists(REPORTS_DIR):
        os.makedirs(REPORTS_DIR)
        logger.info(f"Created directory: {REPORTS_DIR}")


def ensure_report_dated_dir(base_dir):
    """Ensure the dated subdirectory (YYYY/MM) exists for report files."""
    dated_dir = ensure_dated_dir(base_dir)
    logger.info(f"Using dated directory: {dated_dir}")
    return dated_dir


def save_proxy_ban_html(html_content, proxy_name, page_num):
    """Save the HTML content that caused a proxy to be banned."""
    if not html_content:
        logger.warning(f"No HTML content to save for banned proxy {proxy_name}")
        return None
    try:
        logs_dir = 'logs'
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_proxy_name = re.sub(r'[^\w\-]', '_', proxy_name)
        filename = f"proxy_ban_{safe_proxy_name}_page{page_num}_{timestamp}.txt"
        filepath = os.path.join(logs_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("# Proxy Ban HTML Capture\n")
            f.write(f"# Proxy: {proxy_name}\n")
            f.write(f"# Page: {page_num}\n")
            f.write(f"# Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# HTML Length: {len(html_content)} bytes\n")
            f.write("=" * 60 + "\n\n")
            f.write(html_content)
        logger.info(f"Saved proxy ban HTML to: {filepath}")
        proxy_ban_html_files.append(filepath)
        print(f"PROXY_BAN_HTML={filepath}")
        return filepath
    except Exception:
        logger.exception("Failed to save proxy ban HTML")
        return None
