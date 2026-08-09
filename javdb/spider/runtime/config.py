"""Configuration loading and initialisation for the spider package.

All config constants are imported here (with safe fallbacks) so that other
submodules can do ``from javdb.spider.runtime.config import X``.
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

from javdb.infra.config import cfg

# Core spider settings
BASE_URL = cfg('BASE_URL', 'https://javdb.com')
# Prefer PAGE_START / PAGE_END; fall back to legacy START_PAGE / END_PAGE if unset
PAGE_START = cfg('PAGE_START', cfg('START_PAGE', 1))
# 10 matches what config_generator writes for GitHub Actions when the PAGE_END
# variable is unset. Under ADR-057 this value is the scan *floor*, so a mismatch
# would silently give local runs a deeper base scan than production.
PAGE_END = cfg('PAGE_END', cfg('END_PAGE', 10))
# ADR-057: treat PAGE_START..PAGE_END as a floor and keep scanning past it while
# the pages still carry today/yesterday badges, so a heavy day is not truncated.
PAGE_SCAN_DYNAMIC = cfg('PAGE_SCAN_DYNAMIC', True)
PAGE_SCAN_MAX = cfg('PAGE_SCAN_MAX', 30)
PAGE_SCAN_STOP_AFTER = cfg('PAGE_SCAN_STOP_AFTER', 2)
REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
DAILY_REPORT_DIR = cfg('DAILY_REPORT_DIR', 'reports/DailyReport')
AD_HOC_DIR = cfg('AD_HOC_DIR', 'reports/AdHoc')
PARSED_MOVIES_CSV = cfg('PARSED_MOVIES_CSV', 'parsed_movies_history.csv')
SPIDER_LOG_FILE = cfg('SPIDER_LOG_FILE', 'logs/spider.log')
LOG_LEVEL = cfg('LOG_LEVEL', 'INFO')
MOVIE_SLEEP_MIN = cfg('MOVIE_SLEEP_MIN', None)
MOVIE_SLEEP_MAX = cfg('MOVIE_SLEEP_MAX', None)
JAVDB_SESSION_COOKIE = cfg('JAVDB_SESSION_COOKIE', None)
PHASE2_MIN_RATE = cfg('PHASE2_MIN_RATE', 4.0)
PHASE2_MIN_COMMENTS = cfg('PHASE2_MIN_COMMENTS', 100)
DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = cfg(
    "DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST",
    ["western_studio_date"],
)
if DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST is None:
    DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = []
elif not isinstance(DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST, (list, tuple, set)):
    DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = [DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST]
DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = [
    stripped
    for item in DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST
    if (stripped := str(item).strip())
]

# Hardcoded studio/label code-prefix blacklist (video_code leading letters, e.g.
# "IDBD" matches "IDBD-123"). No cfg()/env var by design — edit this list directly.
BLACKLIST_CODE_KEYWORDS = frozenset({
    "IDBD", "OFJE", "MIZD", "REBDB", "SIVR", "OVVR", "RBB", "THU", "JUMS", "PRX",
    "HEO", "TABF", "FTKTABF", "MDBK", "UMSO", "DAZD", "SODS", "MBDD", "KCKC",
    "JUSD", "ATKD", "KTRA", "MMPB", "CADV", "KWBD", "BOMNVD", "HNDB", "NSFS",
    "MQNC", "MBF", "BBSS", "HDKA", "CJOB", "LZFB", "MUCD", "ETQR", "PKGF",
    "OSCO", "IMO", "FCDSS", "PCB", "FIG", "YRK", "ICE", "MKCK", "ATAD", "SDAM",
    "SDTH", "BMW", "PBD", "SDNM", "GMEM", "RROY", "KIBD", "DVAJ", "ROE", "PPX",
    "HHF", "STOL", "MDVR", "PWIFE", "DJCY", "TMRD", "OEM", "CMA", "STBD",
    "LUNS", "SMOM", "BJD", "TPI", "TSSR", "GML", "JUYU", "PYM", "BKD", "RD",
    "UGH", "PARATHD",
})

# Hardcoded actor-name blacklist. No cfg()/env var by design — edit this list
# directly to add or remove names.
BLACKLIST_ACTOR_NAMES = frozenset({
    "長谷川律子", "富岡よし子", "瀬川志穂", "如月千鶴", "内原美智子", "海江田弘菜",
    "上島美都子", "筒美かえで", "折原ゆかり", "伊織涼子", "北村敏世", "宝田もなみ",
    "城眞紀", "真矢織江", "藤木静子", "翔田千里", "白鳥寿美礼", "松沢ゆかり",
    "岩崎千鶴", "高杉美穂", "桐島美奈子", "青井マリ", "浅井舞香", "服部圭子",
    "赤坂ルナ", "宮前幸恵", "音羽文子", "北川礼子", "杉岡恵美子", "柊もみじ",
    "花園もあ", "西村妮娜",
})

PROXY_HTTP = cfg('PROXY_HTTP', None)
PROXY_HTTPS = cfg('PROXY_HTTPS', None)
PROXY_MODULES = cfg('PROXY_MODULES', ['spider'])
GIT_USERNAME = cfg('GIT_USERNAME', 'github-actions')
GIT_PASSWORD = cfg('GIT_PASSWORD', '')
GIT_REPO_URL = cfg('GIT_REPO_URL', '')
GIT_BRANCH = cfg('GIT_BRANCH', 'main')

# CF bypass
CF_BYPASS_SERVICE_PORT = cfg('CF_BYPASS_SERVICE_PORT', 8000)
CF_BYPASS_ENABLED = cfg('CF_BYPASS_ENABLED', True)
CF_BYPASS_PORT_MAP = cfg('CF_BYPASS_PORT_MAP', {})
CF_BYPASS_VIA_PROXY = cfg('CF_BYPASS_VIA_PROXY', False)

# Proxy pool
from javdb.proxy.policy import normalize_proxy_mode
PROXY_MODE = normalize_proxy_mode(cfg('PROXY_MODE', 'pool'))
PROXY_POOL = cfg('PROXY_POOL', [])
PROXY_POOL_MAX_FAILURES = cfg('PROXY_POOL_MAX_FAILURES', 3)
_raw_login_proxy_name = cfg('LOGIN_PROXY_NAME', None)
LOGIN_PROXY_NAME = (
    _raw_login_proxy_name.strip() if isinstance(_raw_login_proxy_name, str) and _raw_login_proxy_name.strip() else None
)

# Login retry policy
LOGIN_ATTEMPTS_PER_PROXY_LIMIT = cfg('LOGIN_ATTEMPTS_PER_PROXY_LIMIT', 6)
LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH = cfg('LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH', 3)

# Login verification: after a successful login refresh, fetch each of these
# URLs (must be login-required pages, e.g. user profile / want-watch list)
# through the worker that just logged in.  Login is only considered
# "verified" if every URL returns non-login HTML.  Empty list disables
# verification (legacy behaviour: trust the login response).  Items can be
# absolute URLs or paths relative to ``BASE_URL``.
LOGIN_VERIFICATION_URLS = cfg(
    'LOGIN_VERIFICATION_URLS',
    ['/users/want_watch_videos', '/users'],
)
# Normalise: cfg() may return None (explicit empty), a scalar string (which
# would be iterated character-by-character when fed to the verifier), or a
# list/tuple.  Consumers always expect a list of URL strings.
if LOGIN_VERIFICATION_URLS is None:
    LOGIN_VERIFICATION_URLS = []
elif not isinstance(LOGIN_VERIFICATION_URLS, (list, tuple)):
    LOGIN_VERIFICATION_URLS = [LOGIN_VERIFICATION_URLS]
LOGIN_VERIFICATION_URLS = [str(x) for x in LOGIN_VERIFICATION_URLS]

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

from javdb.infra.logging import setup_logging, get_logger  # noqa: E402

setup_logging(SPIDER_LOG_FILE, LOG_LEVEL)


def _log_rust_status():
    _logger = get_logger(__name__)
    try:
        from javdb.parsing import RUST_PARSERS_AVAILABLE
        if RUST_PARSERS_AVAILABLE:
            _logger.debug("✅ Spider using Rust parsers - high-performance HTML parsing enabled")
        else:
            _logger.debug("⚠️  Spider using Python parsers - Rust parsers not available")
    except Exception:
        _logger.debug("⚠️  Could not determine parser implementation status")
    try:
        from javdb.storage.history_manager import RUST_HISTORY_AVAILABLE
        if RUST_HISTORY_AVAILABLE:
            _logger.debug("✅ Spider using Rust history manager - high-performance CSV I/O enabled")
        else:
            _logger.debug("⚠️  Spider using Python history manager - Rust not available")
    except Exception:
        _logger.debug("⚠️  Could not determine history manager implementation status")


_log_rust_status()
