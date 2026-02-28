"""Login / session-cookie management for the spider."""

from utils.logging_config import get_logger
import scripts.spider.state as state
from scripts.spider.config_loader import (
    LOGIN_FEATURE_AVAILABLE,
    JAVDB_USERNAME, JAVDB_PASSWORD,
)

logger = get_logger(__name__)

try:
    from javdb_rust_core import is_login_page as _rust_is_login_page
    _RUST_LOGIN_CHECK = True
except ImportError:
    _RUST_LOGIN_CHECK = False


def attempt_login_refresh():
    """Attempt to refresh session cookie by logging in via login.py.

    This function can only be called ONCE per spider run.  After successful
    login, login.py updates config.py with the new cookie, then we reload
    config.py to get the updated cookie value.

    Returns:
        tuple: (success: bool, new_cookie: str or None)
    """
    if state.login_attempted:
        logger.debug("Login already attempted in this run, skipping")
        return False, None

    state.login_attempted = True

    if not LOGIN_FEATURE_AVAILABLE:
        logger.warning("Login feature not available (GPT_API_KEY/GPT_API_URL not configured)")
        return False, None

    if not JAVDB_USERNAME or not JAVDB_PASSWORD:
        logger.warning("Login credentials not configured (JAVDB_USERNAME/JAVDB_PASSWORD)")
        return False, None

    logger.info("=" * 60)
    logger.info("ATTEMPTING SESSION COOKIE REFRESH VIA LOGIN")
    logger.info("=" * 60)

    login_proxies = None
    if state.global_proxy_pool is not None:
        current_proxy = state.global_proxy_pool.get_current_proxy()
        if current_proxy:
            login_proxies = {
                'http': current_proxy.get('http'),
                'https': current_proxy.get('https'),
            }
            login_proxies = {k: v for k, v in login_proxies.items() if v}
            if login_proxies:
                logger.info(f"Login will use proxy: {state.global_proxy_pool.get_current_proxy_name()}")
            else:
                login_proxies = None

    try:
        from scripts.login import login_with_retry, update_config_file

        success, session_cookie, message = login_with_retry(
            JAVDB_USERNAME, JAVDB_PASSWORD, max_retries=10, proxies=login_proxies,
        )

        if success and session_cookie:
            logger.info("✓ Login successful, new session cookie obtained")
            logger.info(f"  Cookie: {session_cookie[:10]}***{session_cookie[-10:]}")

            if update_config_file(session_cookie):
                logger.info("✓ Updated config.py with new session cookie")
                import importlib
                import config
                importlib.reload(config)
                new_cookie = getattr(config, 'JAVDB_SESSION_COOKIE', session_cookie)
                state.refreshed_session_cookie = new_cookie
                logger.info(f"✓ Reloaded config.py, cookie: {new_cookie[:10]}***{new_cookie[-10:]}")
                if state.global_request_handler:
                    state.global_request_handler.config.javdb_session_cookie = new_cookie
                    logger.info("✓ Updated request handler with new session cookie")
                logger.info("=" * 60)
                return True, new_cookie
            else:
                logger.warning("Failed to update config.py, using cookie directly for this run")
                state.refreshed_session_cookie = session_cookie
                if state.global_request_handler:
                    state.global_request_handler.config.javdb_session_cookie = session_cookie
                    logger.info("✓ Updated request handler with new session cookie")
                logger.info("=" * 60)
                return True, session_cookie
        else:
            logger.error(f"✗ Login failed: {message}")
            logger.info("=" * 60)
            return False, None

    except ImportError as e:
        logger.error(f"Failed to import login module: {e}")
        return False, None
    except Exception as e:
        logger.error(f"Unexpected error during login: {e}")
        return False, None


def is_login_page(html: str) -> bool:
    """Detect whether the returned HTML is a JavDB login page."""
    if not html:
        return False
    if _RUST_LOGIN_CHECK:
        try:
            return _rust_is_login_page(html)
        except Exception:
            pass
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    title_tag = soup.find('title')
    if title_tag:
        title_text = title_tag.get_text().strip().lower()
        if '登入' in title_text or 'login' in title_text:
            return True
    return False


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
