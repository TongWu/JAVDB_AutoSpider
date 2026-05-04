"""Global mutable state for the spider package.

Every module that needs to read or *mutate* shared state should
``import packages.python.javdb_spider.runtime.state as state`` and access ``state.<var>``.
"""

import atexit
import json
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
from packages.python.javdb_platform.movie_claim_client import (
    MOVIE_CLAIM_MODE_AUTO,
    MOVIE_CLAIM_MODE_FORCE_ON,
    MOVIE_CLAIM_MODE_OFF,
    MovieClaimClient,
    create_movie_claim_client_from_env,
    create_movie_claim_client_with_mode_from_env,
)
from packages.python.javdb_platform.proxy_ban_manager import (
    set_remote_ban_hook,
    set_remote_unban_hook,
)
from packages.python.javdb_platform.proxy_coordinator_client import (
    ProxyCoordinatorClient,
)
from packages.python.javdb_platform.runner_registry_client import (
    RunnerRegistryClient,
    RunnerRegistryUnavailable,
    create_runner_registry_client_from_env,
    proxy_pool_hash,
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
# Cross-instance movie-detail claim coordinator (P1-B; per-day-sharded
# ``MovieClaimState`` DO on the same Worker as the proxy/login DOs).
# Lazily initialised by :func:`setup_movie_claim_client`; ``None`` means
# "per-process dedup only" — equivalent to the pre-DO behaviour.  Default
# OFF: only enabled when ``MOVIE_CLAIM_ENABLED`` is truthy AND the URL/token
# pair is configured AND the Worker's ``/health`` probe succeeds.
global_movie_claim_client: Optional[MovieClaimClient] = None
# Auto-toggle scaffolding for ``global_movie_claim_client``.  See
# :func:`_apply_movie_claim_recommendation` for the state machine.
#
# - ``_movie_claim_client_pending`` keeps a constructed-but-not-yet-public
#   client when the runner is in ``auto`` mode and the registry has not
#   yet recommended activation; mounting / unmounting flips the public
#   ``global_movie_claim_client`` reference but never recreates the
#   underlying ``requests.Session``.
# - ``_movie_claim_mode`` is the resolved tri-state from
#   :data:`MOVIE_CLAIM_MODE_AUTO` / ``FORCE_ON`` / ``OFF``.  ``force_on``
#   reproduces the legacy P1-B behaviour (always mounted, signal
#   ignored); ``off`` is the never-mount path.
# - ``_movie_claim_last_recommended`` caches the most recent
#   ``movie_claim_recommended`` flag the registry surfaced; the
#   heartbeat loop reads it (under the lock) to pick the next sleep
#   interval, and ``_apply_movie_claim_recommendation`` updates it on
#   every successful signal.
# - ``_movie_claim_lock`` serialises mount/unmount transitions so a
#   concurrent heartbeat tick + atexit unregister cannot toggle the
#   global out from under each other.
_movie_claim_client_pending: Optional[MovieClaimClient] = None
_movie_claim_mode: str = MOVIE_CLAIM_MODE_OFF
_movie_claim_last_recommended: bool = False
_movie_claim_lock: threading.Lock = threading.Lock()
# Cross-instance runner registry (P2-E; singleton ``RunnerRegistry`` DO).
# Lazily initialised by :func:`setup_runner_registry_client`; ``None``
# means "no registry, runner is invisible to peers" — equivalent to the
# pre-DO behaviour.  Default OFF: only enabled when
# ``RUNNER_REGISTRY_ENABLED`` is truthy AND the URL/token pair is
# configured AND the Worker's ``/health`` probe succeeds.
global_runner_registry_client: Optional[RunnerRegistryClient] = None
# Background daemon thread that pings ``/heartbeat`` every 60 s once a
# registry client is configured.  Holds a reference here so the atexit
# handler can flag it for shutdown without leaking a reference cycle.
_runner_heartbeat_thread: Optional[threading.Thread] = None
_runner_heartbeat_stop = threading.Event()
# Track that we already ran ``unregister`` so atexit + signal handlers
# can both fire without sending two requests.
_runner_unregistered: bool = False

# Heartbeat cadences.
#
# ``_HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC`` (60 s) matches the Worker's
# ``RUNNER_STALE_TTL_MS / 5`` so a single missed heartbeat never evicts a
# healthy runner.  Used in ``force_on`` / ``off`` modes and in ``auto``
# mode once the registry has recommended activation.
#
# ``_HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC`` (15 s) is used in ``auto`` mode
# while the registry still says "single runner": the cohort is one tick
# away from crossing the threshold, and the worst-case "lock-leak"
# window when a peer joins but neither side has refreshed yet is bounded
# by the heartbeat cadence.  15 s keeps that window small without
# materially increasing cost — heartbeats hit a singleton DO, far cheaper
# than per-href claim DO calls.
#
# ``_RUNNER_HEARTBEAT_INTERVAL_SEC`` is preserved as an alias for the
# multi-runner cadence so existing tests / docs that monkeypatch it keep
# working unchanged.
_HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC = 60.0
_HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC = 15.0
_RUNNER_HEARTBEAT_INTERVAL_SEC = _HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC

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
    """Mark a proxy for CF bypass reuse according to --always-bypass-time.

    Side effect (P1-A): when the cross-instance proxy coordinator is wired
    up, the requirement is also published to the Worker DO via
    :meth:`ProxyCoordinatorClient.mark_cf_bypass` so peer runners pick it
    up on their next ``/lease``.  This is fire-and-forget and never raises;
    when the coordinator is not configured the call is a no-op and the
    behaviour is identical to the pre-DO world.
    """
    if always_bypass_time is None:
        return

    proxies_requiring_cf_bypass[proxy_name] = time.time()
    if always_bypass_time == 0:
        logger.info(f"Proxy '{proxy_name}' marked as requiring CF bypass for this runtime")
    else:
        logger.info(
            f"Proxy '{proxy_name}' marked for CF bypass reuse for {always_bypass_time} minute(s)"
        )

    coord = global_proxy_coordinator
    if coord is not None and proxy_name:
        # ``always_bypass_time``:
        #   - 0       → permanent for this session  → DO ttl_ms = 0
        #   - N (min) → expires after N minutes     → DO ttl_ms = N * 60_000
        # The Worker stores the tri-state so peers see the right window.
        ttl_ms = (
            0 if always_bypass_time == 0 else int(always_bypass_time) * 60 * 1000
        )
        try:
            coord.mark_cf_bypass(proxy_name, ttl_ms=ttl_ms)
        except Exception:  # noqa: BLE001 — fail-open; never break local marker
            logger.warning(
                "Failed to dispatch CF bypass marker for '%s' to coordinator",
                proxy_name, exc_info=True,
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
    # P1-A — wire the ProxyBanManager → coordinator bridge.  Bound to ``client``
    # via closure so a later disable / re-init naturally rebinds; pure
    # fire-and-forget so a coordinator outage cannot stall the ban path.
    set_remote_ban_hook(lambda name: client.mark_proxy_banned(name))
    set_remote_unban_hook(lambda name: client.mark_proxy_unbanned(name))
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


def _apply_movie_claim_recommendation(recommended: bool) -> None:
    """Mount or unmount :data:`global_movie_claim_client` per *recommended*.

    Called from three places: once at the end of
    :func:`setup_runner_registry_client` (the first ``register`` response),
    on every successful heartbeat in :func:`_runner_heartbeat_loop`, and
    on every successful re-register inside the same loop.  The function
    is idempotent and edge-triggered: the mount/unmount transition only
    happens when the public ``global_movie_claim_client`` actually
    changes, so a steady-state cluster doesn't spam INFO logs.

    Mode semantics (matches the docs in §15.4 of
    ``docs/PROXY_COORDINATOR_DEPLOY.md``):

    - :data:`MOVIE_CLAIM_MODE_OFF` — never mount; signal is ignored.
      ``_movie_claim_last_recommended`` is still updated so the
      heartbeat-interval helper can run uniformly across modes.
    - :data:`MOVIE_CLAIM_MODE_FORCE_ON` — always mount (idempotently);
      signal is ignored.  Reproduces the legacy P1-B "operator
      explicitly enabled it" behaviour.  Mounts ``_movie_claim_client_pending``
      onto the global if the global is still ``None`` (defensive: keeps
      the function safe even if a future caller blanks the global).
    - :data:`MOVIE_CLAIM_MODE_AUTO` — drive the global purely from the
      recommendation: ``True`` mounts pending → global, ``False``
      unmounts global (keeps pending alive so the next ``True`` is a
      cheap pointer copy, no new HTTP session).

    Thread safety: held under :data:`_movie_claim_lock` so a concurrent
    heartbeat tick + atexit unregister cannot toggle the global out
    from under each other.  Callers must NEVER hold the lock across
    network I/O.
    """
    global global_movie_claim_client, _movie_claim_last_recommended

    with _movie_claim_lock:
        _movie_claim_last_recommended = bool(recommended)
        mode = _movie_claim_mode

        if mode == MOVIE_CLAIM_MODE_OFF:
            # Signal ignored; never mount.  Update the cached flag so
            # the heartbeat interval helper still has a value to read,
            # even though it will only honour 60 s for non-auto modes.
            return

        if mode == MOVIE_CLAIM_MODE_FORCE_ON:
            # Force-on: idempotently mount pending → global if the
            # global got blanked for any reason.  Do NOT log on the
            # steady-state path (already-mounted is the common case).
            if (
                global_movie_claim_client is None
                and _movie_claim_client_pending is not None
            ):
                global_movie_claim_client = _movie_claim_client_pending
                logger.info(
                    "movie-claim force_on: mounted (signal recommended=%s ignored)",
                    recommended,
                )
            return

        # Auto mode: drive the global from the registry signal,
        # edge-triggered so steady state stays log-quiet.
        if recommended:
            if (
                global_movie_claim_client is None
                and _movie_claim_client_pending is not None
            ):
                global_movie_claim_client = _movie_claim_client_pending
                logger.info(
                    "movie-claim auto: mounted (active_runners >= threshold)",
                )
        else:
            if global_movie_claim_client is not None:
                # Keep ``_movie_claim_client_pending`` alive (do NOT
                # close it) so a subsequent recommended=True can mount
                # the same client without rebuilding the session.
                global_movie_claim_client = None
                logger.info(
                    "movie-claim auto: unmounted (active_runners < threshold)",
                )


def setup_movie_claim_client() -> Optional[MovieClaimClient]:
    """Initialise the cross-instance movie-detail claim coordinator (P1-B).

    Companion of :func:`setup_proxy_coordinator` /
    :func:`setup_login_state_client`: the three DO clients live behind the
    same Cloudflare Worker but each gates independently so an operator
    can roll any subset out without touching the others.

    Behaviour now branches on the resolved
    ``MOVIE_CLAIM_ENABLED`` mode (see
    :func:`packages.python.javdb_platform.movie_claim_client.parse_movie_claim_mode`):

    - :data:`MOVIE_CLAIM_MODE_OFF` — return ``None`` and leave
      :data:`global_movie_claim_client` un-mounted, identical to the
      pre-auto world.  This is the explicit ``MOVIE_CLAIM_ENABLED=false``
      / ``0`` / ``no`` / ``""`` path.
    - :data:`MOVIE_CLAIM_MODE_FORCE_ON` — create the client, run the
      ``/health`` probe, then mount immediately on the global so peers
      observe claim coordination from the very first detail page (this
      is the legacy P1-B contract preserved for the mixed old-Worker /
      new-client window).
    - :data:`MOVIE_CLAIM_MODE_AUTO` — same construction + ``/health`` as
      force-on, BUT mount the client *optimistically* on the global and
      keep a copy in :data:`_movie_claim_client_pending`.  The first
      ``register`` response from the registry then drives the final
      mount/unmount via :func:`_apply_movie_claim_recommendation` —
      single-runner deployments unmount within seconds, multi-runner
      deployments stay mounted with zero operator action.

    Cached in :data:`global_movie_claim_client`.  Idempotent.

    Thread safety: the network I/O (cfg lookup, env splice,
    ``/health`` probe inside :func:`create_movie_claim_client_with_mode_from_env`)
    must NOT run under :data:`_movie_claim_lock` — a slow Worker would
    otherwise stall every other lock acquirer (including the heartbeat
    thread reading interval state and :func:`_apply_movie_claim_recommendation`).
    The fix is to bracket the I/O with two short critical sections:

    1. First section: handle the idempotent fast paths.
    2. I/O outside the lock.
    3. Second section: re-check (another thread may have raced past us
       during the I/O window) and commit ``_movie_claim_mode``,
       ``_movie_claim_client_pending``, ``global_movie_claim_client``
       atomically. Any client we constructed but lost the race for is
       :meth:`MovieClaimClient.close`-d so we don't leak its session.
    """
    global global_movie_claim_client, _movie_claim_client_pending, _movie_claim_mode
    with _movie_claim_lock:
        if global_movie_claim_client is not None:
            return global_movie_claim_client
        if _movie_claim_client_pending is not None:
            if (
                _movie_claim_mode == MOVIE_CLAIM_MODE_FORCE_ON
                or (
                    _movie_claim_mode == MOVIE_CLAIM_MODE_AUTO
                    and _movie_claim_last_recommended
                )
            ):
                global_movie_claim_client = _movie_claim_client_pending
            return _movie_claim_client_pending

    from packages.python.javdb_platform.config_helper import cfg
    url = (cfg('PROXY_COORDINATOR_URL', '') or '').strip()
    token = (cfg('PROXY_COORDINATOR_TOKEN', '') or '').strip()
    raw_enabled_cfg = cfg('MOVIE_CLAIM_ENABLED', None)

    # Set the env vars expected by the factory for the duration of the call,
    # then delegate to ``create_movie_claim_client_with_mode_from_env`` so
    # the disable paths and ``/health`` semantics stay defined in one place.
    prior = (
        os.environ.get('PROXY_COORDINATOR_URL'),
        os.environ.get('PROXY_COORDINATOR_TOKEN'),
        os.environ.get('MOVIE_CLAIM_ENABLED'),
    )
    try:
        if url:
            os.environ['PROXY_COORDINATOR_URL'] = url
        else:
            os.environ.pop('PROXY_COORDINATOR_URL', None)
        if token:
            os.environ['PROXY_COORDINATOR_TOKEN'] = token
        else:
            os.environ.pop('PROXY_COORDINATOR_TOKEN', None)
        # Pass the cfg value through verbatim so the factory can
        # distinguish "var not set in config" (→ auto default) from
        # "var explicitly empty" (→ off, matches operator intuition).
        if raw_enabled_cfg is None:
            os.environ.pop('MOVIE_CLAIM_ENABLED', None)
        else:
            os.environ['MOVIE_CLAIM_ENABLED'] = str(raw_enabled_cfg)
        client, mode = create_movie_claim_client_with_mode_from_env()
    finally:
        for key, value in zip(
            ('PROXY_COORDINATOR_URL', 'PROXY_COORDINATOR_TOKEN', 'MOVIE_CLAIM_ENABLED'),
            prior,
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # Re-acquire the lock to commit the resolved state atomically with the
    # other readers (``_apply_movie_claim_recommendation``,
    # ``_next_heartbeat_interval``, the early-return paths above, and any
    # idempotent re-entry of this function).
    with _movie_claim_lock:
        # Double-checked locking: another thread may have completed
        # ``setup_movie_claim_client`` while we were doing I/O. If so,
        # mirror its decision and dispose of our duplicate client to
        # avoid leaking the underlying ``requests.Session``.
        if global_movie_claim_client is not None:
            if client is not None and client is not global_movie_claim_client:
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
            return global_movie_claim_client
        if _movie_claim_client_pending is not None:
            if client is not None and client is not _movie_claim_client_pending:
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
            if (
                _movie_claim_mode == MOVIE_CLAIM_MODE_FORCE_ON
                or (
                    _movie_claim_mode == MOVIE_CLAIM_MODE_AUTO
                    and _movie_claim_last_recommended
                )
            ):
                global_movie_claim_client = _movie_claim_client_pending
            return _movie_claim_client_pending

        # We're the winner — persist the resolved mode so the
        # registry-signal handler and the heartbeat-interval picker can
        # branch on it later.
        _movie_claim_mode = mode

        if client is None or mode == MOVIE_CLAIM_MODE_OFF:
            # Off path covers: explicit disable, missing URL/token,
            # /health failure, or auto/force_on resolved → off due to
            # the gates. Make sure the pending slot is empty so a stale
            # handle from a prior ``setup_movie_claim_client`` doesn't
            # accidentally mount.
            _movie_claim_client_pending = None
            global_movie_claim_client = None
            return None

        _movie_claim_client_pending = client

        if mode == MOVIE_CLAIM_MODE_FORCE_ON:
            # Legacy P1-B contract: mount immediately so the first
            # detail page coordinates with peers. Subsequent registry
            # signals are ignored by ``_apply_movie_claim_recommendation``.
            global_movie_claim_client = client
            logger.info(
                "Movie-claim client mounted (force_on): base_url=%s, holder_id=%s",
                url, runtime_holder_id,
            )
            return client

        # Auto mode: mount optimistically so the runner doesn't spend
        # the first few seconds (before the first ``register`` response
        # lands) racing peers without coordination. The maximum cost
        # is N claim DO calls per page during that startup window for
        # a single-runner deploy, immediately reverted on the first
        # ``register`` response.
        global_movie_claim_client = client
        logger.info(
            "Movie-claim client optimistically mounted (auto, awaiting registry signal): "
            "base_url=%s, holder_id=%s",
            url, runtime_holder_id,
        )
        return client


def _resolve_proxy_pool_json() -> str:
    """Pull the rendered ``PROXY_POOL_JSON`` payload (or its env override).

    Used for the cross-runner ``proxy_pool_hash`` drift signal — falls back
    to a JSON-serialised view of :data:`PROXY_POOL` so the hash still
    catches drift even when ``config.PROXY_POOL_JSON`` is empty.  Returns
    an empty string when nothing is configured (no drift detection
    available); callers must accept the empty hash as "no info".
    """
    from packages.python.javdb_platform.config_helper import cfg
    raw = (cfg('PROXY_POOL_JSON', '') or '').strip()
    if raw:
        return raw
    if PROXY_POOL:
        try:
            return json.dumps(PROXY_POOL, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return ""
    return ""


def _next_heartbeat_interval() -> float:
    """Pick the next ``_runner_heartbeat_stop.wait`` duration.

    The auto-toggle wants two cadences:

    - 60 s (multi-runner) — the cohort has crossed the threshold and the
      claim mutex is mounted; further refresh is purely for liveness so
      the canonical TTL/5 cadence is enough.
    - 15 s (single-runner) — the cohort is below the threshold and the
      next peer to join would otherwise have to wait up to a heartbeat
      interval before either side notices.  15 s caps that "lock-leak"
      window without inflating heartbeat costs (the heartbeat hits a
      singleton DO, far cheaper than per-href claim DO calls).

    Force-on / off modes always use the multi-runner cadence — there is
    no recommendation edge to detect, so the faster cadence buys nothing.

    The multi-runner branch reads :data:`_RUNNER_HEARTBEAT_INTERVAL_SEC`
    (rather than :data:`_HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC` directly)
    so existing tests that monkeypatch the legacy alias to a small
    value keep speeding the loop up as before.

    Thread safety: ``_movie_claim_mode`` and ``_movie_claim_last_recommended``
    are mutated under :data:`_movie_claim_lock` (by
    :func:`setup_movie_claim_client` and :func:`_apply_movie_claim_recommendation`),
    so we briefly take the lock here too in order to read a consistent
    snapshot. The lock is released before returning so a slow caller
    (e.g. ``_runner_heartbeat_stop.wait`` followed by an HTTP heartbeat)
    doesn't block writers.
    """
    with _movie_claim_lock:
        mode = _movie_claim_mode
        recommended = _movie_claim_last_recommended
    if mode != MOVIE_CLAIM_MODE_AUTO:
        return _RUNNER_HEARTBEAT_INTERVAL_SEC
    return (
        _RUNNER_HEARTBEAT_INTERVAL_SEC
        if recommended
        else _HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC
    )


def _runner_heartbeat_loop(client: RunnerRegistryClient, holder_id: str) -> None:
    """Background thread: ping ``/heartbeat`` until stopped.

    Cadence is dynamic via :func:`_next_heartbeat_interval` so that auto
    mode can shorten the polling interval while it's still single-runner
    (= claim coordination off) and fall back to the canonical 60 s once a
    peer joins.

    On ``alive=False`` (the holder was evicted by the registry's GC alarm
    after we missed the staleness window) we re-``register`` so the
    registry recovers without operator intervention.  All exceptions are
    swallowed — the registry is purely operational metadata and must
    NEVER take down the spider.

    The successful ``heartbeat`` and re-``register`` paths each forward
    the response's ``movie_claim_recommended`` flag into
    :func:`_apply_movie_claim_recommendation` so an `auto`-mode runner
    mounts / unmounts ``global_movie_claim_client`` automatically.
    Failure paths (network blip, malformed response) intentionally do
    NOT update the cached recommendation — a single transient hiccup
    must not unmount an already-active claim coordinator.
    """
    while not _runner_heartbeat_stop.wait(_next_heartbeat_interval()):
        try:
            result = client.heartbeat(holder_id)
        except RunnerRegistryUnavailable:
            # Transient outage; just try again on the next tick.
            logger.debug("Runner-registry heartbeat unavailable; will retry")
            continue
        except Exception:  # noqa: BLE001
            logger.warning(
                "Unexpected runner-registry heartbeat error; will retry",
                exc_info=True,
            )
            continue

        if not result.alive:
            # Registry GC'd us (heartbeat lapsed past stale TTL) — try to
            # rejoin so ops dashboards stop showing us as missing.  No
            # need to re-emit drift summaries on reconnection: the
            # response was already logged at original ``register`` time.
            try:
                rereg = client.register(
                    holder_id=holder_id,
                    workflow_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                    workflow_name=os.environ.get("GITHUB_WORKFLOW", ""),
                    proxy_pool_hash=proxy_pool_hash(_resolve_proxy_pool_json()),
                )
                logger.info("Runner-registry recovered after eviction")
                # Feed the registry's fresh recommendation so the auto-
                # toggle re-syncs after eviction (the cohort may have
                # changed while we were missing).
                _apply_movie_claim_recommendation(rereg.movie_claim_recommended)
            except RunnerRegistryUnavailable:
                logger.debug("Runner-registry re-register unavailable; will retry")
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Unexpected runner-registry re-register error",
                    exc_info=True,
                )
            continue

        # Healthy heartbeat: forward the cohort signal to the auto-toggle.
        # Old Workers omit the field; the parser defaults it to False
        # which the auto path treats as "single runner, unmount".
        _apply_movie_claim_recommendation(result.movie_claim_recommended)


def _unregister_runner_at_exit() -> None:
    """Best-effort cleanup of this runner's registry entry.

    Registered via :func:`atexit.register` from :func:`setup_runner_registry_client`.
    Idempotent (``_runner_unregistered`` flag) so atexit + a signal
    handler both calling this is safe.  Never raises — registry hygiene
    must not block the spider's shutdown.
    """
    global global_runner_registry_client, _runner_heartbeat_thread, _runner_unregistered
    if _runner_unregistered:
        return
    client = global_runner_registry_client
    if client is None:
        return
    _runner_heartbeat_stop.set()
    thread = _runner_heartbeat_thread
    if (
        thread is not None
        and thread.is_alive()
        and thread is not threading.current_thread()
    ):
        thread.join(timeout=5.0)
        if thread.is_alive():
            logger.warning("Runner-registry heartbeat thread did not stop before unregister")
        else:
            _runner_heartbeat_thread = None
    try:
        client.unregister(runtime_holder_id)
    except RunnerRegistryUnavailable:
        logger.debug("Runner-registry unregister unavailable at exit")
    except Exception:  # noqa: BLE001
        logger.warning("Unexpected runner-registry unregister error", exc_info=True)
    else:
        _runner_unregistered = True
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    global_runner_registry_client = None
    if _runner_heartbeat_thread is not None and not _runner_heartbeat_thread.is_alive():
        _runner_heartbeat_thread = None


def _warn_on_proxy_pool_drift(
    self_hash: str,
    summary,  # type: ignore[no-untyped-def] — sequence of PoolHashBucket
) -> None:
    """Emit a single WARNING when *self_hash* is not the majority hash.

    Mirrors the contract of the Worker's ``pool_hash_summary`` field
    (subsumes the original P3-B "drift detection" plan item): a runner
    joining with a hash that doesn't match the rest of the cohort gets
    one log line at startup so ops can act before throughput skews.
    """
    if not summary or not self_hash:
        return
    by_hash = {bucket.hash: bucket.count for bucket in summary}
    own_count = by_hash.get(self_hash, 0)
    other_total = sum(c for h, c in by_hash.items() if h != self_hash)
    if other_total > 0 and own_count <= other_total:
        # Render compact summary for the ops log.  Keep it on one line so
        # log aggregators can grep for "proxy_pool_hash drift".
        rendered = ", ".join(
            f"{b.hash or '<empty>'}={b.count}"
            for b in summary
        )
        logger.warning(
            "proxy_pool_hash drift detected: this runner has %s but cohort is {%s} — "
            "check PROXY_POOL_JSON across workflows",
            self_hash, rendered,
        )


def setup_runner_registry_client() -> Optional[RunnerRegistryClient]:
    """Initialise the singleton runner-registry client (P2-E).

    Companion of :func:`setup_proxy_coordinator` /
    :func:`setup_login_state_client` / :func:`setup_movie_claim_client`:
    all four DO clients live behind the same Cloudflare Worker but each
    gates independently so an operator can roll any subset out without
    touching the others.

    On a successful initialisation we ALSO:

    1. Send a ``/register`` call with this runner's GH Actions metadata
       (``GITHUB_RUN_ID`` / ``GITHUB_WORKFLOW``) and the canonical
       ``proxy_pool_hash`` of ``PROXY_POOL_JSON``.  The server response
       is inspected for cohort drift — if peers have a different hash
       we emit one ``WARNING`` log line so ops can investigate.
    2. Spawn a daemon thread that calls ``/heartbeat`` every 60 s.
    3. Register an :mod:`atexit` hook that calls ``/unregister``.

    All three are best-effort: any failure is logged and the spider
    continues exactly as if the registry were not configured.

    Cached in :data:`global_runner_registry_client`.  Idempotent.
    """
    global global_runner_registry_client, _runner_heartbeat_thread

    if global_runner_registry_client is not None:
        return global_runner_registry_client

    from packages.python.javdb_platform.config_helper import cfg
    url = (cfg('PROXY_COORDINATOR_URL', '') or '').strip()
    token = (cfg('PROXY_COORDINATOR_TOKEN', '') or '').strip()
    enabled_raw = (str(cfg('RUNNER_REGISTRY_ENABLED', '') or '')).strip().lower()
    if enabled_raw not in {"1", "true", "yes"}:
        logger.info(
            "Runner-registry client disabled (RUNNER_REGISTRY_ENABLED=%r) — "
            "runner is invisible to peers",
            enabled_raw,
        )
        global_runner_registry_client = None
        return None
    if not url or not token:
        logger.info(
            "Runner-registry client not configured (PROXY_COORDINATOR_URL/TOKEN "
            "unset) — runner is invisible to peers",
        )
        global_runner_registry_client = None
        return None

    # Delegate to the factory so the disable paths and ``/health`` semantics
    # stay defined in one place.  Mirror the trick used in setup_movie_claim_client.
    prior = (
        os.environ.get('PROXY_COORDINATOR_URL'),
        os.environ.get('PROXY_COORDINATOR_TOKEN'),
        os.environ.get('RUNNER_REGISTRY_ENABLED'),
    )
    try:
        os.environ['PROXY_COORDINATOR_URL'] = url
        os.environ['PROXY_COORDINATOR_TOKEN'] = token
        os.environ['RUNNER_REGISTRY_ENABLED'] = enabled_raw
        client = create_runner_registry_client_from_env()
    finally:
        for key, value in zip(
            ('PROXY_COORDINATOR_URL', 'PROXY_COORDINATOR_TOKEN', 'RUNNER_REGISTRY_ENABLED'),
            prior,
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    if client is None:
        global_runner_registry_client = None
        return None

    # Best-effort startup register — failure here MUST NOT block the spider.
    pool_json = _resolve_proxy_pool_json()
    self_hash = proxy_pool_hash(pool_json)
    try:
        result = client.register(
            holder_id=runtime_holder_id,
            workflow_run_id=os.environ.get("GITHUB_RUN_ID", ""),
            workflow_name=os.environ.get("GITHUB_WORKFLOW", ""),
            proxy_pool_hash=self_hash,
        )
        logger.info(
            "Runner-registry client initialised: base_url=%s, holder_id=%s, "
            "active_runners=%d, movie_claim_recommended=%s",
            url, runtime_holder_id, len(result.active_runners),
            result.movie_claim_recommended,
        )
        _warn_on_proxy_pool_drift(self_hash, result.pool_hash_summary)
        # Feed the auto-toggle with the very first cohort snapshot.  In
        # auto mode this either keeps the optimistically-mounted client
        # in place (≥2 runners) or unmounts it (single runner) within
        # milliseconds of startup; force_on / off modes ignore it.
        _apply_movie_claim_recommendation(result.movie_claim_recommended)
    except RunnerRegistryUnavailable:
        logger.warning(
            "Runner-registry register failed at startup; continuing without "
            "registry coordination this run",
        )
        client.close()
        global_runner_registry_client = None
        return None
    except Exception:  # noqa: BLE001
        logger.warning(
            "Unexpected runner-registry register error; continuing without "
            "registry coordination this run",
            exc_info=True,
        )
        client.close()
        global_runner_registry_client = None
        return None

    global_runner_registry_client = client
    _runner_heartbeat_stop.clear()
    if _runner_heartbeat_thread is None or not _runner_heartbeat_thread.is_alive():
        _runner_heartbeat_thread = threading.Thread(
            target=_runner_heartbeat_loop,
            args=(client, runtime_holder_id),
            name="runner-heartbeat",
            daemon=True,
        )
        _runner_heartbeat_thread.start()
    atexit.register(_unregister_runner_at_exit)
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

    # P2-D — fold per-attempt success/failure + latency into the per-proxy
    # health snapshot.  Like ``_global_cf_event_cb`` above, this fallback
    # handler walks the proxy pool so the proxy_name is per-call, not bound
    # at construction time.  No-op when the coordinator is absent or the
    # request was direct (proxy_name=None) — the spider falls back to the
    # local proxy_pool's success/failure counters in that case.
    def _global_request_complete_cb(proxy_name, kind, latency_ms):
        coord = global_proxy_coordinator
        if coord is None or not proxy_name:
            return
        coord.report_async(proxy_name, kind, latency_ms=latency_ms)

    global_request_handler = RequestHandler(
        proxy_pool=global_proxy_pool, config=config, penalty_tracker=_pt,
        on_cf_event=_global_cf_event_cb,
        on_request_complete=_global_request_complete_cb,
    )
    logger.info("Request handler initialized successfully")


def setup_proxy_pool(use_proxy) -> None:
    """Initialize the global proxy pool from configuration.

    Also lazily initialises all four cross-instance coordinators
    (per-proxy throttle, global login state, per-day movie-claim
    mutex, and runner registry) so every worker thread spawned later
    automatically picks them up via :data:`global_proxy_coordinator`,
    :data:`global_login_state_client`,
    :data:`global_movie_claim_client`, and
    :data:`global_runner_registry_client`.  All four are independent
    and may be ``None`` (fail-open).
    """
    from packages.python.javdb_platform.proxy_policy import is_proxy_mode_disabled
    global global_proxy_pool

    setup_proxy_coordinator()
    setup_login_state_client()
    setup_movie_claim_client()
    setup_runner_registry_client()

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

    # P2-D — when both the pool and the cross-instance coordinator are
    # available AND the pool exposes ``set_health_provider`` (Python
    # ProxyPool only — the Rust pool currently keeps round-robin), wire
    # the coordinator's per-proxy health cache as the weighting source.
    # Reads from the cache populated by ``lease()`` so weighted selection
    # piggy-backs on the requests the spider was already making.
    if (
        global_proxy_pool is not None
        and global_proxy_coordinator is not None
        and hasattr(global_proxy_pool, "set_health_provider")
    ):
        try:
            global_proxy_pool.set_health_provider(
                global_proxy_coordinator.get_proxy_health_score
            )
            logger.info(
                "Proxy pool health-weighted selection enabled (P2-D coordinator)"
            )
        except Exception:  # noqa: BLE001 — wiring must not block startup
            logger.warning(
                "Failed to wire proxy health provider; falling back to round-robin",
                exc_info=True,
            )

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
