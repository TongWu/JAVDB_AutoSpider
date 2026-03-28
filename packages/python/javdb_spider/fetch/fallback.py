"""Multi-level fallback logic for fetching index and detail pages."""

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_core.parser import parse_detail
from packages.python.javdb_platform.bridges.rust_adapters.parser_adapter import validate_index_html as _validate_index_html_fast
from packages.python.javdb_core.url_helper import get_page_url as _url_helper_get_page_url
from packages.python.javdb_platform.request_handler import ProxyBannedError, ProxyExhaustedError

import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_spider.fetch.session import is_login_page, can_attempt_login, attempt_login_refresh
from packages.python.javdb_spider.runtime.config import (
    BASE_URL,
    PROXY_MODE, PROXY_POOL_MAX_FAILURES,
)
from packages.python.javdb_spider.runtime.sleep import movie_sleep_mgr as _sleep_mgr

logger = get_logger(__name__)


def _login_refresh_for_spider(use_proxy):
    """Call ``attempt_login_refresh`` with proxy context matching the spider's mode.

    When *use_proxy* is ``False`` (``--no-proxy``), login runs via direct
    connection.  When ``True``, the current proxy-pool snapshot is passed as
    explicit arguments so the login endpoint matches the worker/proxy that
    triggered the fallback.
    """
    if not use_proxy:
        return attempt_login_refresh(spider_uses_proxy=False)
    if state.global_proxy_pool is not None:
        current = state.global_proxy_pool.get_current_proxy()
        if current:
            proxies = {
                k: v
                for k, v in (
                    ('http', current.get('http')),
                    ('https', current.get('https')),
                )
                if v
            }
            if proxies:
                return attempt_login_refresh(
                    explicit_proxies=proxies,
                    explicit_proxy_name=state.global_proxy_pool.get_current_proxy_name(),
                    spider_uses_proxy=True,
                )
    return attempt_login_refresh(spider_uses_proxy=True)


def _sleep_between_fetches() -> None:
    """Full adaptive sleep between consecutive top-level fetch attempts."""
    _sleep_mgr.sleep()


def validate_index_html(html: str, page_num: int = 0, context_msg: str = ''):
    """Validate index page HTML and classify the result.

    Tries the fast Rust validator first, then falls back to a full
    BeautifulSoup parse when the Rust layer returns negative.

    Returns:
        (html_or_none, has_movie_list, is_valid_empty)
        - ``html_or_none``: the original *html* on success / valid-empty,
          ``None`` when validation fails completely.
        - ``has_movie_list``: ``True`` if a non-empty movie list was found.
        - ``is_valid_empty``: ``True`` if the page is structurally valid
          but contains no entries (end-of-pagination, empty category, etc.).
    """
    has_movie_list, is_valid_empty = _validate_index_html_fast(html)
    if has_movie_list:
        logger.debug("[Page %d] Success: %s", page_num, context_msg)
        return html, True, False
    if is_valid_empty:
        logger.info("[Page %d] Valid empty page detected: %s", page_num, context_msg)
        return html, False, True

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    movie_list = soup.find('div', class_=lambda x: x and 'movie-list' in x)
    if movie_list:
        movie_items = movie_list.find_all('div', class_='item')
        if len(movie_items) > 0:
            logger.debug(
                "[Page %d] Success: %s - Found %d movie items",
                page_num, context_msg, len(movie_items),
            )
            return html, True, False
        else:
            logger.info(
                "[Page %d] movie-list exists but is empty (0 items) "
                "- treating as valid empty page", page_num,
            )
            return html, False, True

    page_text = soup.get_text()
    title = soup.find('title')
    title_text = title.text.strip() if title else ""
    age_modal = soup.find('div', class_='modal is-active over18-modal')
    empty_message_div = soup.find('div', class_='empty-message')
    has_no_content_msg = (
        'No content yet' in page_text or
        'No result' in page_text or
        '暫無內容' in page_text or
        '暂无内容' in page_text or
        empty_message_div is not None
    )

    if empty_message_div is not None:
        empty_msg_text = empty_message_div.get_text().strip()
        logger.info(
            "[Page %d] Page exists but has no content (empty-message: '%s')",
            page_num, empty_msg_text,
        )
        return html, False, True
    if not age_modal and has_no_content_msg:
        logger.info(
            "[Page %d] Page exists but has no content (text pattern detected)",
            page_num,
        )
        return html, False, True
    if not age_modal and len(html) > 20000:
        logger.debug(
            "[Page %d] Large HTML without movie list, treating as empty page",
            page_num,
        )
        return html, False, True

    logger.debug(
        "[Page %d] Validation failed (no movie list, title='%s', "
        "age_modal=%s): %s",
        page_num, title_text, age_modal is not None, context_msg,
    )
    return None, False, False


class AdhocLoginFailedError(Exception):
    """Raised when login fails on an index page in adhoc mode, causing the spider to abort."""
    pass

def get_page_url(page_num, custom_url=None):
    """Generate URL for a specific page number."""
    return _url_helper_get_page_url(page_num, BASE_URL, custom_url)


def fetch_index_page_with_fallback(page_url, session, use_cookie, use_proxy,
                                   use_cf_bypass, page_num, is_adhoc_mode=False):
    """Fetch index page with smart multi-level fallback mechanism.

    Fallback Hierarchy (per-proxy, with max_retries=1):
    1. Proxy A: direct request (or CF bypass if proxy is marked)
    2. Proxy A: CF bypass (if not already tried in step 1)
    3. Login refresh (once per runtime, adhoc mode only for index) -> retry
    4. Proxy B: direct request (or CF bypass if proxy is marked)
    5. Proxy B: CF bypass (if not already tried)
    6. Continue through all proxies in the pool

    Returns:
        tuple: (html_content, has_movie_list, proxy_was_banned,
                effective_use_proxy, effective_use_cf_bypass, is_valid_empty_page)
    """
    proxy_was_banned = False
    last_failed_html = None

    def _validate_index_html(html, context_msg):
        """Validate index page HTML. Returns (html, has_movie_list, is_valid_empty)."""
        nonlocal last_failed_html
        result_html, has_movie_list, is_valid_empty = validate_index_html(
            html, page_num=page_num, context_msg=context_msg,
        )
        if result_html is None:
            last_failed_html = html
        return result_html, has_movie_list, is_valid_empty

    def try_fetch(u_proxy, u_cf, context_msg):
        nonlocal last_failed_html
        logger.debug(f"[Page {page_num}] {context_msg}...")
        try:
            html = state.get_page(page_url, session, use_cookie=use_cookie,
                                  use_proxy=u_proxy, module_name='spider',
                                  max_retries=1, use_cf_bypass=u_cf)
            if html:
                if is_login_page(html):
                    logger.warning(f"[Page {page_num}] Login page detected: {context_msg}")
                    if can_attempt_login(is_adhoc_mode, is_index_page=True):
                        logger.info(f"[Page {page_num}] Attempting login refresh due to login page...")
                        login_ok, _, _proxy = _login_refresh_for_spider(u_proxy)
                        if login_ok:
                            _sleep_between_fetches()
                            html = state.get_page(page_url, session, use_cookie=use_cookie,
                                                  use_proxy=u_proxy, module_name='spider',
                                                  max_retries=1, use_cf_bypass=u_cf)
                            if html and not is_login_page(html):
                                return _validate_index_html(html, context_msg)
                            else:
                                logger.warning(f"[Page {page_num}] Still login page after refresh")
                                if is_adhoc_mode:
                                    raise AdhocLoginFailedError(
                                        "Login succeeded but index page still requires authentication")
                        else:
                            if is_adhoc_mode:
                                raise AdhocLoginFailedError(
                                    "Login refresh failed on index page")
                    last_failed_html = html
                    return None, False, False
                return _validate_index_html(html, context_msg)
        except (AdhocLoginFailedError, ProxyBannedError, ProxyExhaustedError):
            raise
        except Exception as e:
            logger.debug(f"[Page {page_num}] Failed {context_msg}: {e}")
        return None, False, False

    def phase0_try(u_proxy, u_cf, context_msg):
        """Like try_fetch, but handle ban: get_page() bans + switches pool then re-raises."""
        nonlocal proxy_was_banned
        try:
            return try_fetch(u_proxy, u_cf, context_msg)
        except ProxyBannedError as e:
            logger.warning(
                f"[Page {page_num}] Proxy '{e.proxy_name}' banned during initial attempt: {e.reason}"
            )
            if e.html:
                state.save_proxy_ban_html(e.html, e.proxy_name, page_num)
            proxy_was_banned = True
            return None, False, False

    def try_proxy_direct_then_cf(proxy_name):
        """Returns (html, success, is_valid_empty, used_cf, proxy_banned)."""
        try:
            needs_cf = state.proxy_needs_cf_bypass(proxy_name)
            if needs_cf:
                html, success, is_valid_empty = try_fetch(
                    True, True, f"Index: Proxy={proxy_name} + CF Bypass (marked)")
                if success or is_valid_empty:
                    return html, success, is_valid_empty, True, False
                return None, False, False, True, False
            html, success, is_valid_empty = try_fetch(
                True, False, f"Index: Proxy={proxy_name} Direct")
            if success or is_valid_empty:
                return html, success, is_valid_empty, False, False
            _sleep_between_fetches()
            html, success, is_valid_empty = try_fetch(
                True, True, f"Index: Proxy={proxy_name} + CF Bypass")
            if success or is_valid_empty:
                if success:
                    state.mark_proxy_cf_bypass(proxy_name)
                return html, success, is_valid_empty, True, False
            return None, False, False, False, False
        except ProxyBannedError as e:
            logger.warning(f"[Page {page_num}] Proxy '{proxy_name}' banned during index fetch: {e.reason}")
            if e.html:
                state.save_proxy_ban_html(e.html, proxy_name, page_num)
            return None, False, False, False, True

    # --- Phase 0: Initial Attempt with current proxy ---
    if use_proxy and state.global_proxy_pool:
        current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
        initial_cf = use_cf_bypass or state.proxy_needs_cf_bypass(current_proxy_name)
        html, success, is_valid_empty = phase0_try(
            True, initial_cf,
            f"Initial attempt (Proxy={current_proxy_name}, CF={initial_cf})")
        if success:
            return html, True, False, True, initial_cf, False
        if is_valid_empty:
            return html, False, False, True, initial_cf, True
        if not initial_cf:
            _sleep_between_fetches()
            html, success, is_valid_empty = phase0_try(
                True, True,
                f"Index: Proxy={current_proxy_name} + CF Bypass")
            if success:
                state.mark_proxy_cf_bypass(current_proxy_name)
                return html, True, False, True, True, False
            if is_valid_empty:
                return html, False, False, True, True, True
        logger.warning(f"[Page {page_num}] Initial attempt failed. Starting fallback...")
    elif not use_proxy:
        html, success, is_valid_empty = try_fetch(
            False, use_cf_bypass,
            f"Initial attempt (No Proxy, CF={use_cf_bypass})")
        if success:
            return html, True, False, False, use_cf_bypass, False
        if is_valid_empty:
            return html, False, False, False, use_cf_bypass, True
        logger.warning(f"[Page {page_num}] Initial attempt failed (no proxy). Starting fallback...")
    else:
        logger.warning(f"[Page {page_num}] No proxy pool configured for initial attempt.")

    # --- Phase 1: Login Refresh ---
    if is_adhoc_mode and can_attempt_login(is_adhoc_mode, is_index_page=True):
        logger.info(f"[Page {page_num}] Attempting login refresh...")
        login_success, _new_cookie, _proxy = _login_refresh_for_spider(use_proxy)
        if login_success and use_proxy and state.global_proxy_pool:
            current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
            _sleep_between_fetches()
            html, success, is_valid_empty, used_cf, _banned = try_proxy_direct_then_cf(current_proxy_name)
            if success:
                logger.info(f"[Page {page_num}] Login refresh + retry succeeded (Proxy={current_proxy_name}, CF={used_cf})")
                return html, True, False, True, used_cf, False
            if is_valid_empty:
                return html, False, False, True, used_cf, True
            if _banned:
                proxy_was_banned = True
            logger.warning(f"[Page {page_num}] Login refresh completed but index page still failed")
        elif login_success and not use_proxy:
            _sleep_between_fetches()
            html, success, is_valid_empty = try_fetch(
                False, use_cf_bypass,
                "Fallback: Retry with refreshed cookie (No Proxy)")
            if success:
                return html, True, False, False, use_cf_bypass, False
            if is_valid_empty:
                return html, False, False, False, use_cf_bypass, True
        elif not login_success:
            if is_adhoc_mode:
                raise AdhocLoginFailedError("Login refresh failed on index page")
            logger.warning(f"[Page {page_num}] Login refresh failed, continuing with proxy pool fallback...")

    # No-proxy mode: only login recovery allowed, skip proxy pool iteration
    if not use_proxy:
        logger.warning(f"[Page {page_num}] No-proxy mode: fallback exhausted (login-only recovery)")
        return last_failed_html, False, False, use_proxy, use_cf_bypass, False

    # --- Phase 2: Iterate through remaining proxies ---
    if state.global_proxy_pool is None:
        logger.error(f"[Page {page_num}] Fallback failed: No proxy pool configured")
        return last_failed_html, False, False, use_proxy, use_cf_bypass, False

    max_switches = state.global_proxy_pool.get_proxy_count() if PROXY_MODE == 'pool' else 1
    max_switches = min(max_switches, 10)

    if use_proxy:
        if is_adhoc_mode:
            state.global_proxy_pool.mark_failure_and_switch()
        else:
            if last_failed_html:
                current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
                state.save_proxy_ban_html(last_failed_html, current_proxy_name, page_num)
            if not proxy_was_banned:
                for _ in range(PROXY_POOL_MAX_FAILURES):
                    state.global_proxy_pool.mark_failure_and_switch()
                proxy_was_banned = True

    for _ in range(max_switches):
        _sleep_between_fetches()
        current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
        html, success, is_valid_empty, used_cf, banned = try_proxy_direct_then_cf(current_proxy_name)
        if success:
            logger.info(f"[Page {page_num}] Index Proxy fallback succeeded (Proxy={current_proxy_name}, CF={used_cf})")
            return html, True, proxy_was_banned, True, used_cf, False
        if is_valid_empty:
            return html, False, proxy_was_banned, True, used_cf, True
        if banned:
            proxy_was_banned = True
            continue
        if is_adhoc_mode:
            state.global_proxy_pool.mark_failure_and_switch()
        else:
            if html:
                state.save_proxy_ban_html(html, current_proxy_name, page_num)
            for _ in range(PROXY_POOL_MAX_FAILURES):
                state.global_proxy_pool.mark_failure_and_switch()
            proxy_was_banned = True

    logger.error(f"[Page {page_num}] All proxy attempts exhausted.")
    return last_failed_html, False, proxy_was_banned, use_proxy, use_cf_bypass, False


def fetch_detail_page_with_fallback(detail_url, session, use_cookie, use_proxy,
                                    use_cf_bypass, entry_index, is_adhoc_mode=False):
    """Fetch detail page with smart multi-level fallback mechanism.

    Returns:
        tuple: (magnets, actor_info, actor_gender, actor_link, supporting_actors, parse_success,
                effective_use_proxy, effective_use_cf_bypass)
    """
    last_result = ([], '', '', '', '', False)

    def try_fetch_and_parse(u_proxy, u_cf, context_msg, skip_sleep=False):
        nonlocal last_result
        logger.debug(f"[{entry_index}] {context_msg}...")
        try:
            html = state.get_page(detail_url, session, use_cookie=use_cookie,
                                  use_proxy=u_proxy, module_name='spider',
                                  max_retries=1, use_cf_bypass=u_cf)
            if html:
                if is_login_page(html):
                    logger.warning(f"[{entry_index}] Login page detected: {context_msg}")
                    if can_attempt_login(is_adhoc_mode, is_index_page=False):
                        logger.info(f"[{entry_index}] Attempting login refresh due to login page...")
                        login_ok, _, _proxy = _login_refresh_for_spider(u_proxy)
                        if login_ok:
                            _sleep_between_fetches()
                            html = state.get_page(detail_url, session, use_cookie=use_cookie,
                                                  use_proxy=u_proxy, module_name='spider',
                                                  max_retries=1, use_cf_bypass=u_cf)
                            if html and not is_login_page(html):
                                m = parse_detail(html, entry_index, skip_sleep=skip_sleep)
                                magnets, actor_info, actor_gender, actor_link, supporting, parse_success = m
                                if parse_success:
                                    logger.info(f"[{entry_index}] Login refresh succeeded: {context_msg}")
                                    return magnets, actor_info, actor_gender, actor_link, supporting, True
                                last_result = (
                                    magnets, actor_info, actor_gender, actor_link, supporting, False,
                                )
                            else:
                                logger.warning(f"[{entry_index}] Still login page after refresh")
                    return [], '', '', '', '', False

                m = parse_detail(html, entry_index, skip_sleep=skip_sleep)
                magnets, actor_info, actor_gender, actor_link, supporting, parse_success = m
                if parse_success:
                    logger.debug(f"[{entry_index}] Success: {context_msg}")
                    return magnets, actor_info, actor_gender, actor_link, supporting, True
                last_result = (magnets, actor_info, actor_gender, actor_link, supporting, False)
                logger.debug(f"[{entry_index}] Parse validation failed (missing magnets): {context_msg}")
            else:
                logger.debug(f"[{entry_index}] Failed to fetch HTML: {context_msg}")
        except (ProxyBannedError, ProxyExhaustedError):
            raise
        except Exception as e:
            logger.debug(f"[{entry_index}] Failed {context_msg}: {e}")
        return [], '', '', '', '', False

    def try_proxy_direct_then_cf(proxy_name, skip_sleep=True):
        """Returns (magnets, actor_info, ag, al, sup, success, used_cf, proxy_banned)."""
        try:
            needs_cf = state.proxy_needs_cf_bypass(proxy_name)
            if needs_cf:
                magnets, actor_info, ag, al, sup, success = try_fetch_and_parse(
                    True, True,
                    f"Detail: Proxy={proxy_name} + CF Bypass (marked)",
                    skip_sleep=skip_sleep)
                if success:
                    return magnets, actor_info, ag, al, sup, True, True, False
                return [], '', '', '', '', False, True, False
            magnets, actor_info, ag, al, sup, success = try_fetch_and_parse(
                True, False,
                f"Detail: Proxy={proxy_name} Direct",
                skip_sleep=skip_sleep)
            if success:
                return magnets, actor_info, ag, al, sup, True, False, False
            _sleep_between_fetches()
            magnets, actor_info, ag, al, sup, success = try_fetch_and_parse(
                True, True,
                f"Detail: Proxy={proxy_name} + CF Bypass",
                skip_sleep=skip_sleep)
            if success:
                state.mark_proxy_cf_bypass(proxy_name)
                return magnets, actor_info, ag, al, sup, True, True, False
            return [], '', '', '', '', False, False, False
        except ProxyBannedError as e:
            logger.warning(f"[{entry_index}] Proxy '{proxy_name}' banned during detail fetch: {e.reason}")
            return [], '', '', '', '', False, False, True

    # --- Phase 0: Initial Attempt ---
    detail_proxy_was_banned = False
    if use_proxy and state.global_proxy_pool:
        current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
        initial_cf = use_cf_bypass or state.proxy_needs_cf_bypass(current_proxy_name)
        try:
            magnets, actor_info, ag, al, sup, success = try_fetch_and_parse(
                True, initial_cf,
                f"Detail Initial (Proxy={current_proxy_name}, CF={initial_cf})",
                skip_sleep=False)
            if success:
                return magnets, actor_info, ag, al, sup, True, True, initial_cf
            if not initial_cf:
                _sleep_between_fetches()
                magnets, actor_info, ag, al, sup, success = try_fetch_and_parse(
                    True, True,
                    f"Detail: Proxy={current_proxy_name} + CF Bypass",
                    skip_sleep=True)
                if success:
                    state.mark_proxy_cf_bypass(current_proxy_name)
                    logger.info(f"[{entry_index}] Detail CF Bypass succeeded with initial proxy={current_proxy_name}")
                    return magnets, actor_info, ag, al, sup, True, True, True
        except ProxyBannedError as e:
            logger.warning(
                f"[{entry_index}] Proxy '{e.proxy_name}' banned during detail initial attempt: {e.reason}"
            )
            detail_proxy_was_banned = True
        logger.warning(f"[{entry_index}] Detail page initial attempt failed. Starting fallback...")
    elif not use_proxy:
        magnets, actor_info, ag, al, sup, success = try_fetch_and_parse(
            False, use_cf_bypass,
            f"Detail Initial (No Proxy, CF={use_cf_bypass})",
            skip_sleep=False)
        if success:
            return magnets, actor_info, ag, al, sup, True, False, use_cf_bypass
        logger.warning(f"[{entry_index}] Detail page initial attempt failed (no proxy). Starting fallback...")
    else:
        logger.warning(f"[{entry_index}] No proxy pool configured for initial attempt.")

    # --- Phase 1: Login Refresh ---
    if can_attempt_login(is_adhoc_mode, is_index_page=False):
        logger.info(f"[{entry_index}] Attempting login refresh...")
        login_success, _new_cookie, _proxy = _login_refresh_for_spider(use_proxy)
        if login_success and use_proxy and state.global_proxy_pool:
            current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
            _sleep_between_fetches()
            magnets, actor_info, ag, al, sup, success, used_cf, _banned = try_proxy_direct_then_cf(
                current_proxy_name, skip_sleep=True)
            if success:
                logger.info(f"[{entry_index}] Login refresh + retry succeeded (Proxy={current_proxy_name}, CF={used_cf})")
                return magnets, actor_info, ag, al, sup, True, True, used_cf
            logger.warning(f"[{entry_index}] Login refresh completed but detail page still failed")
        elif login_success and not use_proxy:
            _sleep_between_fetches()
            magnets, actor_info, ag, al, sup, success = try_fetch_and_parse(
                False, use_cf_bypass,
                "Detail: Retry with refreshed cookie (No Proxy)",
                skip_sleep=True)
            if success:
                return magnets, actor_info, ag, al, sup, True, False, use_cf_bypass
        elif not login_success:
            logger.warning(f"[{entry_index}] Login refresh failed, continuing with proxy pool fallback...")

    # No-proxy mode: only login recovery allowed, skip proxy pool iteration
    if not use_proxy:
        logger.warning(f"[{entry_index}] No-proxy mode: fallback exhausted (login-only recovery)")
        return (
            last_result[0], last_result[1], last_result[2], last_result[3], last_result[4],
            last_result[5], use_proxy, use_cf_bypass,
        )

    # --- Phase 2: Iterate through remaining proxies ---
    if state.global_proxy_pool is None:
        logger.error(f"[{entry_index}] Fallback failed: No proxy pool configured")
        return (
            last_result[0], last_result[1], last_result[2], last_result[3], last_result[4],
            last_result[5], use_proxy, use_cf_bypass,
        )

    max_switches = state.global_proxy_pool.get_proxy_count() if PROXY_MODE == 'pool' else 1
    max_switches = min(max_switches, 10)

    skip_switch = detail_proxy_was_banned
    for _ in range(max_switches):
        if skip_switch:
            skip_switch = False
        else:
            switched = state.global_proxy_pool.mark_failure_and_switch()
            if not switched:
                logger.warning(f"[{entry_index}] No more proxies available in pool")
                break
        current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
        _sleep_between_fetches()
        magnets, actor_info, ag, al, sup, success, used_cf, banned = try_proxy_direct_then_cf(
            current_proxy_name, skip_sleep=True)
        if success:
            logger.info(f"[{entry_index}] Detail Proxy fallback succeeded (Proxy={current_proxy_name}, CF={used_cf})")
            return magnets, actor_info, ag, al, sup, True, True, used_cf
        if banned:
            skip_switch = True
            continue

    logger.warning(f"[{entry_index}] Detail page fallback exhausted. Returning best available result.")
    return (
        last_result[0], last_result[1], last_result[2], last_result[3], last_result[4],
        last_result[5], use_proxy, use_cf_bypass,
    )
