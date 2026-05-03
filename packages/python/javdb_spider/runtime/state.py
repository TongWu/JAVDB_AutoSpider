"""Global mutable state for the spider package.

Every module that needs to read or *mutate* shared state should
``import packages.python.javdb_spider.runtime.state as state`` and access ``state.<var>``.
"""

import os
import re
import logging
import threading
import time
import uuid
from typing import Optional, Dict
from datetime import datetime

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.login_state_client import (
    LoginStateClient,
)
from packages.python.javdb_platform.proxy_coordinator_client import (
    ProxyCoordinatorClient,
)
from packages.python.javdb_platform.proxy_pool import ProxyPool, create_proxy_pool_from_config
from packages.python.javdb_platform.proxy_policy import should_proxy_module
from packages.python.javdb_platform.request_handler import RequestHandler, RequestConfig
from packages.python.javdb_platform.path_helper import ensure_dated_dir

from packages.python.javdb_spider.runtime.config import (
    BASE_URL,
    CF_BYPASS_SERVICE_PORT, CF_BYPASS_ENABLED,
    CF_BYPASS_PORT_MAP,
    JAVDB_SESSION_COOKIE,
    PROXY_HTTP, PROXY_HTTPS, PROXY_MODULES, PROXY_MODE,
    PROXY_POOL, PROXY_POOL_MAX_FAILURES,
    REPORTS_DIR,
    LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Mutable globals
# ---------------------------------------------------------------------------

global_proxy_pool: Optional[ProxyPool] = None
global_request_handler: Optional[RequestHandler] = None
# Cross-instance proxy coordinator (Cloudflare DO).  Lazily initialised by
# :func:`setup_proxy_coordinator`; ``None`` means "use local throttling
# only" — equivalent to the pre-coordinator behaviour.
global_proxy_coordinator: Optional[ProxyCoordinatorClient] = None
# Cross-instance login-state coordinator (singleton GlobalLoginState DO on
# the same Cloudflare Worker as ``global_proxy_coordinator``).  Lazily
# initialised by :func:`setup_login_state_client`; ``None`` means
# "per-runner login only" — equivalent to the pre-DO behaviour.  Both
# coordinators are independent: either, neither, or both can be active.
global_login_state_client: Optional[LoginStateClient] = None

parsed_links: set = set()
proxy_ban_html_files: list = []

login_attempted: bool = False
refreshed_session_cookie: Optional[str] = None
logged_in_proxy_name: Optional[str] = None
# Monotonic version of the cookie published in
# :data:`global_login_state_client`.  Tracked so we can pass it back to
# :meth:`LoginStateClient.invalidate` as the optimistic lock token; ``None``
# when the DO is not configured or this runner has never observed a publish.
current_login_state_version: Optional[int] = None
# Per-process opaque identity used as the ``holder_id`` for DO leases.
# Generated once at import time so every module in this runner sees the
# same value — required by ``acquire_lease`` / ``publish`` /
# ``release_lease`` to stay matched across the re-login flow.
runtime_holder_id: str = f"runner-{uuid.uuid4().hex[:16]}"

# Per-proxy and global login budget tracking
login_attempts_per_proxy: Dict[str, int] = {}
login_failures_per_proxy: Dict[str, int] = {}
login_total_attempts: int = 0
login_total_budget: int = len(PROXY_POOL) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT if PROXY_POOL else 0

always_bypass_time: Optional[int] = None
proxies_requiring_cf_bypass: Dict[str, float] = {}

# Proxies whose remaining login budget has already been deducted from
# ``login_total_budget`` (idempotency guard for ``deduct_proxy_login_budget``).
_login_budget_deducted_proxies: set = set()
# Serialises ``deduct_proxy_login_budget`` so the check-and-update against
# ``_login_budget_deducted_proxies`` / ``login_total_budget`` is atomic
# across concurrent proxy-ban callers.
_login_budget_lock = threading.Lock()


def _deduct_proxy_login_budget_locked(proxy_name: str) -> int:
    """Core deduction logic. Caller must hold :data:`_login_budget_lock`."""
    global login_total_budget
    if proxy_name in _login_budget_deducted_proxies:
        return 0
    if login_total_budget <= 0:
        _login_budget_deducted_proxies.add(proxy_name)
        return 0

    used = login_attempts_per_proxy.get(proxy_name, 0)
    remaining = LOGIN_ATTEMPTS_PER_PROXY_LIMIT - used
    if remaining <= 0:
        _login_budget_deducted_proxies.add(proxy_name)
        return 0

    # Never let the global budget drop below total attempts already spent
    # (otherwise downstream budget checks would falsely report "exhausted").
    new_budget = max(login_total_attempts, login_total_budget - remaining)
    actually_deducted = login_total_budget - new_budget
    login_total_budget = new_budget
    _login_budget_deducted_proxies.add(proxy_name)
    if actually_deducted > 0:
        logger.info(
            "Login budget reduced by %d for banned proxy '%s' (now %d, attempts so far %d)",
            actually_deducted, proxy_name, new_budget, login_total_attempts,
        )
    return actually_deducted


def deduct_proxy_login_budget(proxy_name: Optional[str]) -> int:
    """Remove a proxy's unused login attempts from the global budget.

    Called when a proxy is banned (either pre-banned at startup or banned
    during runtime).  The proxy's *remaining* per-proxy budget
    (``LOGIN_ATTEMPTS_PER_PROXY_LIMIT - login_attempts_per_proxy[proxy]``,
    floored at 0) is subtracted from :data:`login_total_budget` so that
    banned workers no longer reserve login credits they cannot use.

    Idempotent per ``proxy_name`` — repeated calls for the same proxy are
    no-ops, even if it gets re-banned.  Thread-safe: concurrent callers
    for different (or the same) proxy cannot double-deduct.

    Args:
        proxy_name: Name of the proxy whose budget should be reclaimed.
            ``None``/empty inputs are silently ignored.

    Returns:
        The number of login attempts deducted (``0`` when nothing changed).
    """
    if not proxy_name:
        return 0
    with _login_budget_lock:
        return _deduct_proxy_login_budget_locked(proxy_name)

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


def should_use_proxy_for_module(module_name: str, use_proxy_flag) -> bool:
    if global_request_handler:
        return global_request_handler.should_use_proxy_for_module(module_name, use_proxy_flag)
    return should_proxy_module(module_name, use_proxy_flag, PROXY_MODULES, proxy_mode=PROXY_MODE)


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


def setup_proxy_coordinator() -> Optional[ProxyCoordinatorClient]:
    """Initialise the cross-instance proxy coordinator from configuration.

    Reads ``PROXY_COORDINATOR_URL`` / ``PROXY_COORDINATOR_TOKEN`` from the
    rendered ``config.py`` (i.e. injected by ``config_generator``).  Both
    must be non-empty to enable the coordinator; otherwise spider continues
    with local-only throttling (this is the supported "disabled" path).

    Returns ``None`` (and logs an ERROR) when configured but the
    ``/health`` probe fails, so deployment misconfiguration surfaces
    early without breaking the spider.

    The result is cached in :data:`global_proxy_coordinator`.  Idempotent:
    calling twice returns the existing client.
    """
    global global_proxy_coordinator
    if global_proxy_coordinator is not None:
        return global_proxy_coordinator

    from packages.python.javdb_platform.config_helper import cfg
    url = (cfg('PROXY_COORDINATOR_URL', '') or '').strip()
    token = (cfg('PROXY_COORDINATOR_TOKEN', '') or '').strip()
    if not url or not token:
        logger.info(
            "Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN unset) "
            "— using local throttling only",
        )
        global_proxy_coordinator = None
        return None

    client = ProxyCoordinatorClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "Proxy coordinator URL %s is configured but /health did not respond — "
            "falling back to local throttling for this run",
            url,
        )
        global_proxy_coordinator = None
        return None
    logger.info(
        "Proxy coordinator client initialised: base_url=%s",
        url,
    )
    global_proxy_coordinator = client
    return client


def setup_login_state_client() -> Optional[LoginStateClient]:
    """Initialise the cross-instance login-state coordinator.

    Sister function of :func:`setup_proxy_coordinator`: reads the **same**
    ``PROXY_COORDINATOR_URL`` / ``PROXY_COORDINATOR_TOKEN`` (the Worker
    hosts both the per-proxy throttle DO and the singleton login-state
    DO).  Returns ``None`` (the supported disabled path) when env vars
    are unset; returns ``None`` and logs an ERROR when configured but the
    Worker's ``/health`` probe fails.

    The result is cached in :data:`global_login_state_client`; idempotent.
    """
    global global_login_state_client
    if global_login_state_client is not None:
        return global_login_state_client

    from packages.python.javdb_platform.config_helper import cfg
    url = (cfg('PROXY_COORDINATOR_URL', '') or '').strip()
    token = (cfg('PROXY_COORDINATOR_TOKEN', '') or '').strip()
    if not url or not token:
        logger.info(
            "Login-state client not configured (PROXY_COORDINATOR_URL/TOKEN unset) "
            "— using per-runner login only",
        )
        global_login_state_client = None
        return None

    client = LoginStateClient(base_url=url, token=token)
    if not client.health_check():
        logger.error(
            "Login-state Worker URL %s is configured but /health did not respond — "
            "falling back to per-runner login for this run",
            url,
        )
        client.close()
        global_login_state_client = None
        return None
    logger.info(
        "Login-state client initialised: base_url=%s, holder_id=%s",
        url, runtime_holder_id,
    )
    global_login_state_client = client
    return client


def initialize_request_handler():
    """Create the global RequestHandler from configuration."""
    global global_request_handler
    from packages.python.javdb_spider.runtime.sleep import (
        penalty_tracker as _pt,
        movie_sleep_mgr as _mgr,
    )
    _cd = _mgr.get_cooldown()
    config = RequestConfig(
        base_url=BASE_URL,
        cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
        cf_bypass_port_map=CF_BYPASS_PORT_MAP,
        cf_bypass_enabled=CF_BYPASS_ENABLED,
        cf_bypass_max_failures=3,
        cf_turnstile_cooldown=_cd,
        fallback_cooldown=_cd,
        javdb_session_cookie=JAVDB_SESSION_COOKIE,
        proxy_http=PROXY_HTTP,
        proxy_https=PROXY_HTTPS,
        proxy_modules=PROXY_MODULES,
        proxy_mode=PROXY_MODE,
        between_attempt_sleep=_mgr.sleep,
    )
    # Cross-instance CF event callback for the global handler.  Unlike the
    # per-worker handlers in fetch_engine.py — which bind a single proxy via
    # closure — the global handler walks the proxy pool, so the proxy that
    # actually triggered the CF event is only known per-call.  We therefore
    # accept the positional ``proxy_name`` from RequestHandler and forward
    # it to the coordinator at report time.  A live ``global_proxy_coordinator``
    # is required; without it (or without a proxy_name) reports are skipped
    # silently — matching the local-only fallback semantics elsewhere.
    def _global_cf_event_cb(proxy_name):
        coord = global_proxy_coordinator
        if coord is None or not proxy_name:
            return
        coord.report_async(proxy_name, "cf")

    global_request_handler = RequestHandler(
        proxy_pool=global_proxy_pool, config=config, penalty_tracker=_pt,
        on_cf_event=_global_cf_event_cb,
    )
    logger.info("Request handler initialized successfully")


def setup_proxy_pool(use_proxy) -> None:
    """Initialize the global proxy pool from configuration.

    Also lazily initialises both cross-instance coordinators
    (per-proxy throttle + global login state) so every worker thread
    spawned later automatically picks them up via
    :data:`global_proxy_coordinator` and :data:`global_login_state_client`.
    Both are independent and may be ``None`` (fail-open).
    """
    from packages.python.javdb_platform.proxy_policy import is_proxy_mode_disabled
    global global_proxy_pool

    setup_proxy_coordinator()
    setup_login_state_client()

    if is_proxy_mode_disabled(PROXY_MODE):
        logger.info("Proxy globally disabled (PROXY_MODE='%s') - skipping pool init", PROXY_MODE)
        global_proxy_pool = None
        return

    if not use_proxy:
        logger.info("Proxy disabled for this run (--no-proxy) - skipping pool init")
        global_proxy_pool = None
        return

    if PROXY_POOL and len(PROXY_POOL) > 0:
        if PROXY_MODE == 'pool':
            logger.info(f"Initializing proxy pool with {len(PROXY_POOL)} proxies...")
            global_proxy_pool = create_proxy_pool_from_config(
                PROXY_POOL,
                max_failures=PROXY_POOL_MAX_FAILURES,
            )
            logger.info("Proxy pool initialized successfully")
            logger.info("Max failures before ban: %d (session-scoped)", PROXY_POOL_MAX_FAILURES)
        elif PROXY_MODE == 'single':
            logger.info("Initializing single proxy mode (using first proxy from pool)...")
            global_proxy_pool = create_proxy_pool_from_config(
                [PROXY_POOL[0]],
                max_failures=PROXY_POOL_MAX_FAILURES,
            )
            logger.info(f"Single proxy initialized: {PROXY_POOL[0].get('name', 'Main-Proxy')}")
    elif PROXY_HTTP or PROXY_HTTPS:
        logger.info("Using legacy PROXY_HTTP/PROXY_HTTPS configuration")
        legacy_proxy = {'name': 'Legacy-Proxy', 'http': PROXY_HTTP, 'https': PROXY_HTTPS}
        global_proxy_pool = create_proxy_pool_from_config(
            [legacy_proxy],
            max_failures=PROXY_POOL_MAX_FAILURES,
        )
    else:
        if should_proxy_module('spider', use_proxy, PROXY_MODULES, proxy_mode=PROXY_MODE):
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
        logger.debug(f"Saved proxy ban HTML to: {filepath}")
        proxy_ban_html_files.append(filepath)
        return filepath
    except Exception:
        logger.exception("Failed to save proxy ban HTML")
        return None
