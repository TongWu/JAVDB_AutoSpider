"""Login / session-cookie management for the spider."""

from typing import Iterable, Optional

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.bridges.rust_adapters.parser_adapter import is_login_page
import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_spider.runtime.config import (
    BASE_URL,
    LOGIN_FEATURE_AVAILABLE,
    JAVDB_USERNAME,
    JAVDB_PASSWORD,
    LOGIN_PROXY_NAME,
    PROXY_POOL,
    LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
    LOGIN_VERIFICATION_URLS,
)

logger = get_logger(__name__)


def resolve_login_proxy_endpoints():
    """Return ``(proxies_dict, proxy_name)`` for :data:`LOGIN_PROXY_NAME` in ``PROXY_POOL``.

    If ``LOGIN_PROXY_NAME`` is unset, returns ``(None, None)``.
    If set but not found or has no http/https URLs, logs a warning and returns ``(None, None)``.
    """
    if not LOGIN_PROXY_NAME:
        return None, None
    for entry in PROXY_POOL or []:
        if entry.get('name') == LOGIN_PROXY_NAME:
            proxies = {
                k: v
                for k, v in (
                    ('http', entry.get('http')),
                    ('https', entry.get('https')),
                )
                if v
            }
            if proxies:
                return proxies, entry.get('name') or LOGIN_PROXY_NAME
            logger.warning(
                "LOGIN_PROXY_NAME %r matches a pool entry but it has no http/https URLs",
                LOGIN_PROXY_NAME,
            )
            return None, None
    logger.warning(
        "LOGIN_PROXY_NAME %r not found in PROXY_POOL; ignoring named login proxy",
        LOGIN_PROXY_NAME,
    )
    return None, None


def attempt_login_refresh(explicit_proxies=None, explicit_proxy_name=None,
                          *, spider_uses_proxy=True):
    """Attempt to refresh session cookie by logging in via login.py.

    Can be called multiple times within a session, subject to per-proxy
    (``LOGIN_ATTEMPTS_PER_PROXY_LIMIT``) and global
    (``state.login_total_budget``) budget constraints.  Counters are tracked
    in ``state.login_attempts_per_proxy`` / ``state.login_total_attempts``.

    The cookie obtained is bound to the proxy/server that performed the login.
    ``state.logged_in_proxy_name`` is set so that parallel workers know which
    server holds the valid session.

    When ``spider_uses_proxy`` is ``False`` (i.e. spider is running with
    ``--no-proxy``), implicit proxy resolution (``LOGIN_PROXY_NAME`` and
    ``global_proxy_pool``) is skipped and login runs via direct connection,
    matching the spider's own network path.

    Args:
        explicit_proxies: If provided, use these proxies for login instead of
            the global proxy pool.  Used by parallel workers to login through
            their own proxy.
        explicit_proxy_name: Human-readable name of the proxy being used.
        spider_uses_proxy: Whether the calling spider is using proxies.
            When ``False``, login is performed via direct connection.

    Returns:
        tuple: (success: bool, new_cookie: str or None, proxy_name: str or None)
    """
    if not LOGIN_FEATURE_AVAILABLE:
        logger.warning("Login feature not available (GPT_API_KEY/GPT_API_URL not configured)")
        return False, None, None

    if not JAVDB_USERNAME or not JAVDB_PASSWORD:
        logger.warning("Login credentials not configured (JAVDB_USERNAME/JAVDB_PASSWORD)")
        return False, None, None

    if state.login_total_budget > 0 and state.login_total_attempts >= state.login_total_budget:
        logger.warning(
            "Login budget exhausted (%d/%d)",
            state.login_total_attempts, state.login_total_budget,
        )
        return False, None, None

    login_proxies = explicit_proxies
    used_proxy_name = explicit_proxy_name

    if login_proxies is None and spider_uses_proxy:
        named_proxies, named_nm = resolve_login_proxy_endpoints()
        if named_proxies:
            login_proxies = named_proxies
            used_proxy_name = named_nm

    if login_proxies is None and spider_uses_proxy and state.global_proxy_pool is not None:
        current_proxy = state.global_proxy_pool.get_current_proxy()
        if current_proxy:
            login_proxies = {
                'http': current_proxy.get('http'),
                'https': current_proxy.get('https'),
            }
            login_proxies = {k: v for k, v in login_proxies.items() if v}
            if login_proxies:
                used_proxy_name = state.global_proxy_pool.get_current_proxy_name()
            else:
                login_proxies = None

    if used_proxy_name:
        proxy_count = state.login_attempts_per_proxy.get(used_proxy_name, 0)
        if proxy_count >= LOGIN_ATTEMPTS_PER_PROXY_LIMIT:
            logger.warning(
                "Proxy %s reached login limit (%d/%d)",
                used_proxy_name, proxy_count, LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
            )
            return False, None, None

    attempt_num = state.login_total_attempts + 1
    budget_str = str(state.login_total_budget) if state.login_total_budget > 0 else 'unlimited'
    proxy_attempt = (
        state.login_attempts_per_proxy.get(used_proxy_name, 0) + 1
        if used_proxy_name else '?'
    )

    logger.info("=" * 60)
    logger.info(
        "ATTEMPTING SESSION COOKIE REFRESH VIA LOGIN "
        "(attempt %s/%s, proxy %s: %s/%s)",
        attempt_num, budget_str,
        used_proxy_name or 'default', proxy_attempt, LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
    )
    logger.info("=" * 60)

    if not spider_uses_proxy:
        logger.info("Login will use direct connection (no proxy)")
    elif login_proxies and used_proxy_name:
        logger.info(f"Login will use proxy: {used_proxy_name}")

    state.login_attempted = True

    try:
        from packages.python.javdb_integrations.login import login_with_retry, update_config_file

        success, session_cookie, message = login_with_retry(
            JAVDB_USERNAME, JAVDB_PASSWORD, max_retries=10, proxies=login_proxies,
        )

        state.login_total_attempts += 1
        if used_proxy_name:
            state.login_attempts_per_proxy[used_proxy_name] = (
                state.login_attempts_per_proxy.get(used_proxy_name, 0) + 1
            )

        if success and session_cookie:
            logger.info("✓ Login successful, new session cookie obtained")
            state.logged_in_proxy_name = used_proxy_name
            if used_proxy_name:
                state.login_failures_per_proxy[used_proxy_name] = 0

            if update_config_file(session_cookie):
                logger.info("✓ Updated config.py with new session cookie")
                import importlib
                import config
                importlib.reload(config)
                new_cookie = getattr(config, 'JAVDB_SESSION_COOKIE', session_cookie)
                state.refreshed_session_cookie = new_cookie
                logger.info("✓ Reloaded config.py with new session cookie")
                if state.global_request_handler:
                    state.global_request_handler.config.javdb_session_cookie = new_cookie
                    logger.info("✓ Updated request handler with new session cookie")
                logger.info("=" * 60)
                return True, new_cookie, used_proxy_name
            else:
                logger.warning("Failed to update config.py, using cookie directly for this run")
                state.refreshed_session_cookie = session_cookie
                if state.global_request_handler:
                    state.global_request_handler.config.javdb_session_cookie = session_cookie
                    logger.info("✓ Updated request handler with new session cookie")
                logger.info("=" * 60)
                return True, session_cookie, used_proxy_name
        else:
            logger.error(f"✗ Login failed: {message}")
            logger.info("=" * 60)
            return False, None, None

    except ImportError as e:
        logger.error(f"Failed to import login module: {e}")
        return False, None, None
    except Exception as e:
        logger.error(f"Unexpected error during login: {e}")
        return False, None, None


def _absolute_url(url: str) -> str:
    """Return *url* prefixed with :data:`BASE_URL` when it is a path."""
    if not url:
        return url
    if url.startswith(('http://', 'https://')):
        return url
    base = (BASE_URL or '').rstrip('/')
    if not base:
        return url
    if url.startswith('/'):
        return f"{base}{url}"
    return f"{base}/{url}"


def verify_login_via_fixed_pages(
    handler,
    proxy_name: Optional[str] = None,
    *,
    urls: Optional[Iterable[str]] = None,
    use_cf_bypass: bool = False,
) -> bool:
    """Verify a freshly-obtained session cookie against fixed login-required pages.

    The freshly logged-in *handler* must already carry the new
    ``javdb_session_cookie``.  Each URL in ``urls`` (defaulting to
    :data:`LOGIN_VERIFICATION_URLS`) is fetched through *handler* and the
    response must be non-empty and **not** look like a login wall for the
    login to be considered verified.

    Returns ``True`` when every URL passes (or when no verification URLs are
    configured — caller falls back to the legacy "trust the login response"
    behaviour).  Returns ``False`` on the first URL that fails to fetch or
    returns a login page.

    Notes:
        * The handler's own retry logic (``max_retries=2``) is used so a
          single transient network blip does not invalidate a fresh cookie.
        * No further re-login is triggered from inside this helper — the
          caller decides what to do on failure (clear cookie, switch proxy,
          etc.).
    """
    verification_urls = list(urls) if urls is not None else list(LOGIN_VERIFICATION_URLS or [])
    if not verification_urls:
        logger.debug("Login verification skipped: LOGIN_VERIFICATION_URLS is empty")
        return True

    label = proxy_name or 'default'
    for raw_url in verification_urls:
        url = _absolute_url(raw_url)
        logger.info(
            "[%s] Verifying login via fixed page: %s", label, url,
        )
        try:
            html = handler.get_page(
                url,
                use_cookie=True,
                use_proxy=True,
                module_name='spider',
                max_retries=2,
                use_cf_bypass=use_cf_bypass,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] Login verification request raised %s for %s",
                label, exc, url,
            )
            return False

        if not html:
            logger.warning(
                "[%s] Login verification fetch returned no content for %s",
                label, url,
            )
            return False
        if is_login_page(html):
            logger.warning(
                "[%s] Login verification page %s still shows login wall — "
                "session cookie not actually authenticated",
                label, url,
            )
            return False

    logger.info(
        "[%s] Login verified against %d fixed page(s)",
        label, len(verification_urls),
    )
    return True


def can_attempt_login(is_adhoc_mode: bool, is_index_page: bool = False) -> bool:
    """Check if login attempt is allowed based on mode and context."""
    if state.login_attempted:
        return False
    if not LOGIN_FEATURE_AVAILABLE:
        return False
    if not is_adhoc_mode:
        if is_index_page:
            return False
        return True
    return True
