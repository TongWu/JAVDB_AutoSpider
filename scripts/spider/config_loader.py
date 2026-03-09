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
    import config as _config_module
except ImportError:
    _config_module = None


def _cfg(name, default):
    """Get a config value with fallback default (resilient to partial config).

    Unlike grouped ``from config import A, B, C`` inside a single try/except,
    this retrieves each variable independently so that a missing *new* variable
    (e.g. ``DEDUP_CSV``) never causes an already-configured variable (e.g.
    ``REPORTS_DIR``) to silently fall back to its hardcoded default.
    """
    if _config_module is None:
        return default
    return getattr(_config_module, name, default)


# Core spider settings
BASE_URL = _cfg('BASE_URL', 'https://javdb.com')
START_PAGE = _cfg('START_PAGE', 1)
END_PAGE = _cfg('END_PAGE', 20)
REPORTS_DIR = _cfg('REPORTS_DIR', 'reports')
DAILY_REPORT_DIR = _cfg('DAILY_REPORT_DIR', 'reports/DailyReport')
AD_HOC_DIR = _cfg('AD_HOC_DIR', 'reports/AdHoc')
PARSED_MOVIES_CSV = _cfg('PARSED_MOVIES_CSV', 'parsed_movies_history.csv')
SPIDER_LOG_FILE = _cfg('SPIDER_LOG_FILE', 'logs/spider.log')
LOG_LEVEL = _cfg('LOG_LEVEL', 'INFO')
PAGE_SLEEP = _cfg('PAGE_SLEEP', 2)
MOVIE_SLEEP_MIN = _cfg('MOVIE_SLEEP_MIN', 5)
MOVIE_SLEEP_MAX = _cfg('MOVIE_SLEEP_MAX', 15)
JAVDB_SESSION_COOKIE = _cfg('JAVDB_SESSION_COOKIE', None)
PHASE2_MIN_RATE = _cfg('PHASE2_MIN_RATE', 4.0)
PHASE2_MIN_COMMENTS = _cfg('PHASE2_MIN_COMMENTS', 100)
PROXY_HTTP = _cfg('PROXY_HTTP', None)
PROXY_HTTPS = _cfg('PROXY_HTTPS', None)
PROXY_MODULES = _cfg('PROXY_MODULES', ['all'])
CF_TURNSTILE_COOLDOWN = _cfg('CF_TURNSTILE_COOLDOWN', 10)
FALLBACK_COOLDOWN = _cfg('FALLBACK_COOLDOWN', 30)
GIT_USERNAME = _cfg('GIT_USERNAME', 'github-actions')
GIT_PASSWORD = _cfg('GIT_PASSWORD', '')
GIT_REPO_URL = _cfg('GIT_REPO_URL', '')
GIT_BRANCH = _cfg('GIT_BRANCH', 'main')

# CF bypass
CF_BYPASS_SERVICE_PORT = _cfg('CF_BYPASS_SERVICE_PORT', 8000)
CF_BYPASS_ENABLED = _cfg('CF_BYPASS_ENABLED', True)

# Proxy pool
PROXY_MODE = _cfg('PROXY_MODE', 'single')
PROXY_POOL = _cfg('PROXY_POOL', [])
PROXY_POOL_COOLDOWN_SECONDS = _cfg('PROXY_POOL_COOLDOWN_SECONDS', 691200)  # 8 days
PROXY_POOL_MAX_FAILURES = _cfg('PROXY_POOL_MAX_FAILURES', 3)

# GPT / Login
GPT_API_KEY = _cfg('GPT_API_KEY', None)
GPT_API_URL = _cfg('GPT_API_URL', None)
LOGIN_FEATURE_AVAILABLE = bool(GPT_API_KEY and GPT_API_URL)

# JavDB credentials
JAVDB_USERNAME = _cfg('JAVDB_USERNAME', None)
JAVDB_PASSWORD = _cfg('JAVDB_PASSWORD', None)

# Report options
INCLUDE_DOWNLOADED_IN_REPORT = _cfg('INCLUDE_DOWNLOADED_IN_REPORT', False)

# Dedup
ENABLE_DEDUP = _cfg('ENABLE_DEDUP', False)
RCLONE_INVENTORY_CSV = _cfg('RCLONE_INVENTORY_CSV', 'rclone_inventory.csv')
DEDUP_CSV = _cfg('DEDUP_CSV', 'dedup.csv')

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
