"""Configuration loading and initialisation for the spider package.

All config constants are imported here (with safe fallbacks) so that other
submodules can do ``from scripts.spider.config_loader import X``.
"""

import os
import sys

# Ensure project root is in sys.path (idempotent)
_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ---------------------------------------------------------------------------
# Unified configuration (with fallback defaults)
# ---------------------------------------------------------------------------

from utils.config_helper import cfg

# Core spider settings
BASE_URL = cfg('BASE_URL', 'https://javdb.com')
# Prefer PAGE_START / PAGE_END; fall back to legacy START_PAGE / END_PAGE if unset
PAGE_START = cfg('PAGE_START', cfg('START_PAGE', 1))
PAGE_END = cfg('PAGE_END', cfg('END_PAGE', 20))
REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
DAILY_REPORT_DIR = cfg('DAILY_REPORT_DIR', 'reports/DailyReport')
AD_HOC_DIR = cfg('AD_HOC_DIR', 'reports/AdHoc')
PARSED_MOVIES_CSV = cfg('PARSED_MOVIES_CSV', 'parsed_movies_history.csv')
SPIDER_LOG_FILE = cfg('SPIDER_LOG_FILE', 'logs/spider.log')
LOG_LEVEL = cfg('LOG_LEVEL', 'INFO')
PAGE_SLEEP = cfg('PAGE_SLEEP', 2)
MOVIE_SLEEP_MIN = cfg('MOVIE_SLEEP_MIN', None)
MOVIE_SLEEP_MAX = cfg('MOVIE_SLEEP_MAX', None)
JAVDB_SESSION_COOKIE = cfg('JAVDB_SESSION_COOKIE', None)
PHASE2_MIN_RATE = cfg('PHASE2_MIN_RATE', 4.0)
PHASE2_MIN_COMMENTS = cfg('PHASE2_MIN_COMMENTS', 100)
PROXY_HTTP = cfg('PROXY_HTTP', None)
PROXY_HTTPS = cfg('PROXY_HTTPS', None)
PROXY_MODULES = cfg('PROXY_MODULES', ['all'])
CF_TURNSTILE_COOLDOWN = cfg('CF_TURNSTILE_COOLDOWN', 10)
FALLBACK_COOLDOWN = cfg('FALLBACK_COOLDOWN', 30)
GIT_USERNAME = cfg('GIT_USERNAME', 'github-actions')
GIT_PASSWORD = cfg('GIT_PASSWORD', '')
GIT_REPO_URL = cfg('GIT_REPO_URL', '')
GIT_BRANCH = cfg('GIT_BRANCH', 'main')

# CF bypass
CF_BYPASS_SERVICE_PORT = cfg('CF_BYPASS_SERVICE_PORT', 8000)
CF_BYPASS_ENABLED = cfg('CF_BYPASS_ENABLED', True)

# Proxy pool
PROXY_MODE = cfg('PROXY_MODE', 'single')
PROXY_POOL = cfg('PROXY_POOL', [])
PROXY_POOL_COOLDOWN_SECONDS = cfg('PROXY_POOL_COOLDOWN_SECONDS', 691200)  # 8 days
PROXY_POOL_MAX_FAILURES = cfg('PROXY_POOL_MAX_FAILURES', 3)
_raw_login_proxy_name = cfg('LOGIN_PROXY_NAME', None)
LOGIN_PROXY_NAME = (
    _raw_login_proxy_name.strip() if isinstance(_raw_login_proxy_name, str) and _raw_login_proxy_name.strip() else None
)

# Login retry policy
LOGIN_ATTEMPTS_PER_PROXY_LIMIT = cfg('LOGIN_ATTEMPTS_PER_PROXY_LIMIT', 6)
LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH = cfg('LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH', 3)

# GPT / Login
GPT_API_KEY = cfg('GPT_API_KEY', None)
GPT_API_URL = cfg('GPT_API_URL', None)
LOGIN_FEATURE_AVAILABLE = bool(GPT_API_KEY and GPT_API_URL)

# JavDB credentials
JAVDB_USERNAME = cfg('JAVDB_USERNAME', None)
JAVDB_PASSWORD = cfg('JAVDB_PASSWORD', None)

# Report options
INCLUDE_DOWNLOADED_IN_REPORT = cfg('INCLUDE_DOWNLOADED_IN_REPORT', False)

# Re-download (洗版)
ENABLE_REDOWNLOAD = cfg('ENABLE_REDOWNLOAD', False)
REDOWNLOAD_SIZE_THRESHOLD = cfg('REDOWNLOAD_SIZE_THRESHOLD', 0.30)

# Dedup
RCLONE_INVENTORY_CSV = cfg('RCLONE_INVENTORY_CSV', 'rclone_inventory.csv')
DEDUP_CSV = cfg('DEDUP_CSV', 'dedup.csv')
DEDUP_DIR = cfg('DEDUP_DIR', 'reports/Dedup')

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

from utils.logging_config import setup_logging, get_logger  # noqa: E402

setup_logging(SPIDER_LOG_FILE, LOG_LEVEL)


def _log_rust_status():
    _logger = get_logger(__name__)
    try:
        from api.parsers import RUST_PARSERS_AVAILABLE
        if RUST_PARSERS_AVAILABLE:
            _logger.debug("✅ Spider using Rust parsers - high-performance HTML parsing enabled")
        else:
            _logger.debug("⚠️  Spider using Python parsers - Rust parsers not available")
    except Exception:
        _logger.debug("⚠️  Could not determine parser implementation status")
    try:
        from utils.history_manager import RUST_HISTORY_AVAILABLE
        if RUST_HISTORY_AVAILABLE:
            _logger.debug("✅ Spider using Rust history manager - high-performance CSV I/O enabled")
        else:
            _logger.debug("⚠️  Spider using Python history manager - Rust not available")
    except Exception:
        _logger.debug("⚠️  Could not determine history manager implementation status")


_log_rust_status()
