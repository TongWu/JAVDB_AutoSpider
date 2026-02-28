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

try:
    from config import CF_BYPASS_SERVICE_PORT, CF_BYPASS_ENABLED
except ImportError:
    CF_BYPASS_SERVICE_PORT = 8000
    CF_BYPASS_ENABLED = True

try:
    from config import PROXY_MODE, PROXY_POOL, PROXY_POOL_COOLDOWN_SECONDS, PROXY_POOL_MAX_FAILURES
except ImportError:
    PROXY_MODE = 'single'
    PROXY_POOL = []
    PROXY_POOL_COOLDOWN_SECONDS = 691200  # 8 days
    PROXY_POOL_MAX_FAILURES = 3

try:
    from config import GPT_API_KEY, GPT_API_URL
    LOGIN_FEATURE_AVAILABLE = bool(GPT_API_KEY and GPT_API_URL)
except ImportError:
    GPT_API_KEY = None
    GPT_API_URL = None
    LOGIN_FEATURE_AVAILABLE = False

try:
    from config import JAVDB_USERNAME, JAVDB_PASSWORD
except ImportError:
    JAVDB_USERNAME = None
    JAVDB_PASSWORD = None

try:
    from config import INCLUDE_DOWNLOADED_IN_REPORT
except ImportError:
    INCLUDE_DOWNLOADED_IN_REPORT = False

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
        _logger.info("⚠️  Could not determine parser implementation status")
    try:
        from utils.history_manager import RUST_HISTORY_AVAILABLE
        if RUST_HISTORY_AVAILABLE:
            _logger.debug("✅ Spider using Rust history manager - high-performance CSV I/O enabled")
        else:
            _logger.debug("⚠️  Spider using Python history manager - Rust not available")
    except Exception:
        _logger.info("⚠️  Could not determine history manager implementation status")


_log_rust_status()
