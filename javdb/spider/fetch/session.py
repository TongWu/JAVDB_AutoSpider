"""Login / session-cookie management for the spider."""

from typing import Iterable, Optional

from javdb.infra.logging import get_logger, log_section
from javdb.proxy.coordinator.login_state_client import LoginStateUnavailable
from javdb.spider.html_validators import is_login_page
import javdb.spider.runtime.state as state
from javdb.spider.runtime.config import (
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

DIRECT_LOGIN_PROXY_NAME = "direct"


def _resolve_runtime(runtime=None):
    return runtime or state.get_active_runtime()


def _login_state(runtime=None):
    runtime = _resolve_runtime(runtime)
    return runtime.login if runtime is not None else state


def _runtime_services(runtime=None):
    runtime = _resolve_runtime(runtime)
    return runtime.services if runtime is not None else state


def _runtime_holder_id(runtime=None) -> str:
    runtime = _resolve_runtime(runtime)
    if runtime is not None:
        return runtime.runner_registry.holder_id
    return state.get_legacy_runtime_holder_id()


def _publish_login_state_to_do(
    proxy_name: Optional[str],
    cookie: str,
    *,
    runtime=None,
) -> None:
    """Best-effort publish of a freshly-obtained cookie to the GlobalLoginState DO.

    Called after every successful :func:`attempt_login_refresh`; silently
    no-ops when the DO is not configured (the legacy login-state client
    is None`` is the supported "per-runner login only" path).

    Failure modes that are explicitly tolerated:

    - ``LoginStateUnavailable`` from a network/server error → fail-open;
      this runner's local cookie still works, only other runners miss out.
    - ``409 lease_required`` (also surfaced as ``LoginStateUnavailable``)
      when the caller did not first acquire the re-login lease — typical
      for the legacy single-process fallback paths in
      ``fetch/fallback.py``.  The :class:`LoginCoordinator` parallel path
      always acquires the lease before login, so its publishes succeed.

    On success, the runtime login-state version is updated so
    downstream :meth:`LoginStateClient.invalidate` calls have the correct
    optimistic-lock token.
    """
    runtime = _resolve_runtime(runtime)
    login_ctx = _login_state(runtime)
    services = _runtime_services(runtime)
    client = (
        services.login_state_client
        if runtime is not None
        else services.global_login_state_client
    )
    if client is None or not cookie:
        return
    publish_proxy_name = proxy_name or DIRECT_LOGIN_PROXY_NAME
    try:
        result = client.publish(
            holder_id=_runtime_holder_id(runtime),
            proxy_name=publish_proxy_name,
            cookie=cookie,
        )
    except LoginStateUnavailable as exc:
        logger.warning(
            "Failed to publish login state to DO (proxy=%s): %s — "
            "this runner still has the cookie locally; other runners may "
            "re-login independently",
            publish_proxy_name, exc,
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Unexpected error publishing login state to DO (proxy=%s): %s — "
            "this runner still has the cookie locally; other runners may "
            "re-login independently",
            publish_proxy_name, exc,
            exc_info=True,
        )
        return
    login_ctx.current_login_state_version = result.version
    logger.info(
        "Published login state to DO: proxy=%s, version=%d",
        publish_proxy_name, result.version,
    )


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
                          *, spider_uses_proxy=True, publish_to_do=True,
                          runtime=None):
    """Attempt to refresh session cookie by logging in via login.py.

    Can be called multiple times within a session, subject to per-proxy
    (``LOGIN_ATTEMPTS_PER_PROXY_LIMIT``) and global
    global budget constraints. Counters are tracked in runtime login state.

    The cookie obtained is bound to the proxy/server that performed the login.
    The runtime login proxy name is set so that parallel workers know which
    server holds the valid session.

    When ``spider_uses_proxy`` is ``False`` (i.e. spider is running with
    ``--no-proxy``), implicit proxy resolution (``LOGIN_PROXY_NAME`` and
    ``global_proxy_pool``) is skipped and login runs via direct connection,
    matching the spider's own network path. The published proxy label is
    the stable ``direct`` sentinel.

    Args:
        explicit_proxies: If provided, use these proxies for login instead of
            the global proxy pool.  Used by parallel workers to login through
            their own proxy.
        explicit_proxy_name: Human-readable name of the proxy being used.
        spider_uses_proxy: Whether the calling spider is using proxies.
            When ``False``, login is performed via direct connection.
        publish_to_do: Whether to publish the cookie immediately. Parallel
            login coordination disables this and publishes only after fixed-page
            verification succeeds.

    Returns:
        tuple: (success: bool, new_cookie: str or None, proxy_name: str or None)
    """
    runtime = _resolve_runtime(runtime)
    login_ctx = _login_state(runtime)
    services = _runtime_services(runtime)

    if not LOGIN_FEATURE_AVAILABLE:
        logger.warning("Login feature not available (GPT_API_KEY/GPT_API_URL not configured)")
        return False, None, None

    if not JAVDB_USERNAME or not JAVDB_PASSWORD:
        logger.warning("Login credentials not configured (JAVDB_USERNAME/JAVDB_PASSWORD)")
        return False, None, None

    if (
        login_ctx.login_total_budget > 0
        and login_ctx.login_total_attempts >= login_ctx.login_total_budget
    ):
        logger.warning(
            "Login budget exhausted (%d/%d)",
            login_ctx.login_total_attempts, login_ctx.login_total_budget,
        )
        return False, None, None

    login_proxies = explicit_proxies
    used_proxy_name = explicit_proxy_name
    if not spider_uses_proxy and not used_proxy_name:
        used_proxy_name = DIRECT_LOGIN_PROXY_NAME

    if login_proxies is None and spider_uses_proxy:
        named_proxies, named_nm = resolve_login_proxy_endpoints()
        if named_proxies:
            login_proxies = named_proxies
            used_proxy_name = named_nm

    proxy_pool = (
        services.proxy_pool
        if runtime is not None
        else state.get_legacy_proxy_pool()
    )
    if login_proxies is None and spider_uses_proxy and proxy_pool is not None:
        current_proxy = proxy_pool.get_current_proxy()
        if current_proxy:
            login_proxies = {
                'http': current_proxy.get('http'),
                'https': current_proxy.get('https'),
            }
            login_proxies = {k: v for k, v in login_proxies.items() if v}
            if login_proxies:
                used_proxy_name = proxy_pool.get_current_proxy_name()
            else:
                login_proxies = None

    if used_proxy_name:
        proxy_count = login_ctx.login_attempts_per_proxy.get(used_proxy_name, 0)
        if proxy_count >= LOGIN_ATTEMPTS_PER_PROXY_LIMIT:
            logger.warning(
                "Proxy %s reached login limit (%d/%d)",
                used_proxy_name, proxy_count, LOGIN_ATTEMPTS_PER_PROXY_LIMIT,
            )
            return False, None, None

    attempt_num = login_ctx.login_total_attempts + 1
    budget_str = str(login_ctx.login_total_budget) if login_ctx.login_total_budget > 0 else 'unlimited'
    proxy_attempt = (
        login_ctx.login_attempts_per_proxy.get(used_proxy_name, 0) + 1
        if used_proxy_name else '?'
    )

    log_section(
        logger,
        f"LOGIN · attempt {attempt_num}/{budget_str} · proxy {used_proxy_name or 'default'} "
        f"({proxy_attempt}/{LOGIN_ATTEMPTS_PER_PROXY_LIMIT})",
        emoji='🔑',
    )

    if not spider_uses_proxy:
        logger.info("Login will use direct connection (no proxy)")
    elif login_proxies and used_proxy_name:
        logger.info(f"Login will use proxy: {used_proxy_name}")

    login_ctx.login_attempted = True

    try:
        from javdb.spider.auth.login import login_with_retry, update_config_file

        success, session_cookie, message = login_with_retry(
            JAVDB_USERNAME, JAVDB_PASSWORD, max_retries=10, proxies=login_proxies,
        )

        login_ctx.login_total_attempts += 1
        if used_proxy_name:
            login_ctx.login_attempts_per_proxy[used_proxy_name] = (
                login_ctx.login_attempts_per_proxy.get(used_proxy_name, 0) + 1
            )

        if success and session_cookie:
            logger.info("✓ Login successful, new session cookie obtained")
            login_ctx.logged_in_proxy_name = used_proxy_name
            if used_proxy_name:
                login_ctx.login_failures_per_proxy[used_proxy_name] = 0

            if update_config_file(session_cookie):
                logger.info("✓ Updated config.py with new session cookie")
                import importlib
                import config
                importlib.reload(config)
                new_cookie = getattr(config, 'JAVDB_SESSION_COOKIE', session_cookie)
                login_ctx.refreshed_session_cookie = new_cookie
                logger.info("✓ Reloaded config.py with new session cookie")
                request_handler = (
                    services.request_handler
                    if runtime is not None
                    else services.global_request_handler
                )
                if request_handler:
                    request_handler.config.javdb_session_cookie = new_cookie
                    logger.info("✓ Updated request handler with new session cookie")
                if publish_to_do:
                    _publish_login_state_to_do(
                        used_proxy_name, new_cookie, runtime=runtime,
                    )
                return True, new_cookie, used_proxy_name
            else:
                logger.warning("Failed to update config.py, using cookie directly for this run")
                login_ctx.refreshed_session_cookie = session_cookie
                request_handler = (
                    services.request_handler
                    if runtime is not None
                    else services.global_request_handler
                )
                if request_handler:
                    request_handler.config.javdb_session_cookie = session_cookie
                    logger.info("✓ Updated request handler with new session cookie")
                if publish_to_do:
                    _publish_login_state_to_do(
                        used_proxy_name, session_cookie, runtime=runtime,
                    )
                return True, session_cookie, used_proxy_name
        else:
            logger.error(f"✗ Login failed: {message}")
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


def can_attempt_login(
    is_adhoc_mode: bool,
    is_index_page: bool = False,
    *,
    runtime=None,
) -> bool:
    """Check if login attempt is allowed based on mode and context."""
    login_ctx = _login_state(runtime)
    if login_ctx.login_attempted:
        return False
    if not LOGIN_FEATURE_AVAILABLE:
        return False
    if not is_adhoc_mode:
        if is_index_page:
            return False
        return True
    return True
