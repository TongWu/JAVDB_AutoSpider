"""Multi-level fallback logic for fetching index and detail pages."""

from utils.logging_config import get_logger
from utils.parser import parse_detail
from utils.url_helper import get_page_url as _url_helper_get_page_url

import scripts.spider.state as state
from scripts.spider.session import is_login_page, can_attempt_login, attempt_login_refresh
from scripts.spider.config_loader import (
    BASE_URL,
    PROXY_MODE, PROXY_POOL_MAX_FAILURES,
)

logger = get_logger(__name__)


class AdhocLoginFailedError(Exception):
    """Raised when login fails on an index page in adhoc mode, causing the spider to abort."""
    pass

try:
    from javdb_rust_core import validate_index_html as _rust_validate_index_html
    _RUST_VALIDATE = True
except ImportError:
    _RUST_VALIDATE = False


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

        if _RUST_VALIDATE:
            try:
                has_movie_list, is_valid_empty = _rust_validate_index_html(html)
                if has_movie_list:
                    logger.debug(f"[Page {page_num}] Success: {context_msg}")
                    return html, True, False
                if is_valid_empty:
                    logger.info(f"[Page {page_num}] Valid empty page detected: {context_msg}")
                    return html, False, True
                last_failed_html = html
                logger.debug(f"[Page {page_num}] Validation failed: {context_msg}")
                return None, False, False
            except Exception:
                pass  # fall through to Python

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        movie_list = soup.find('div', class_=lambda x: x and 'movie-list' in x)
        if movie_list:
            movie_items = movie_list.find_all('div', class_='item')
            if len(movie_items) > 0:
                logger.debug(f"[Page {page_num}] Success: {context_msg} - Found {len(movie_items)} movie items")
                return html, True, False
            else:
                logger.info(f"[Page {page_num}] movie-list exists but is empty (0 items) - treating as valid empty page")
                return html, False, True
        else:
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
                logger.info(f"[Page {page_num}] Page exists but has no content (empty-message: '{empty_msg_text}')")
                return html, False, True
            elif not age_modal and has_no_content_msg:
                logger.info(f"[Page {page_num}] Page exists but has no content (text pattern detected)")
                return html, False, True
            elif not age_modal and len(html) > 20000:
                logger.debug(f"[Page {page_num}] Large HTML without movie list, treating as empty page")
                return html, False, True
            else:
                last_failed_html = html
                logger.debug(f"[Page {page_num}] Validation failed (no movie list, title='{title_text}', age_modal={age_modal is not None}): {context_msg}")
        return None, False, False

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
                        login_ok, _, _proxy = attempt_login_refresh()
                        if login_ok:
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
        except AdhocLoginFailedError:
            raise
        except Exception as e:
            logger.debug(f"[Page {page_num}] Failed {context_msg}: {e}")
        return None, False, False

    def try_proxy_direct_then_cf(proxy_name):
        needs_cf = state.proxy_needs_cf_bypass(proxy_name)
        if needs_cf:
            html, success, is_valid_empty = try_fetch(
                True, True, f"Index: Proxy={proxy_name} + CF Bypass (marked)")
            if success or is_valid_empty:
                return html, success, is_valid_empty, True
            return None, False, False, True
        html, success, is_valid_empty = try_fetch(
            True, False, f"Index: Proxy={proxy_name} Direct")
        if success or is_valid_empty:
            return html, success, is_valid_empty, False
        html, success, is_valid_empty = try_fetch(
            True, True, f"Index: Proxy={proxy_name} + CF Bypass")
        if success or is_valid_empty:
            if success:
                state.mark_proxy_cf_bypass(proxy_name)
            return html, success, is_valid_empty, True
        return None, False, False, False

    # --- Phase 0: Initial Attempt with current proxy ---
    if use_proxy and state.global_proxy_pool:
        current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
        initial_cf = use_cf_bypass or state.proxy_needs_cf_bypass(current_proxy_name)
        html, success, is_valid_empty = try_fetch(
            True, initial_cf,
            f"Initial attempt (Proxy={current_proxy_name}, CF={initial_cf})")
        if success:
            return html, True, False, True, initial_cf, False
        if is_valid_empty:
            return html, False, False, True, initial_cf, True
        if not initial_cf:
            html, success, is_valid_empty = try_fetch(
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
        login_success, _new_cookie, _proxy = attempt_login_refresh()
        if login_success and use_proxy and state.global_proxy_pool:
            current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
            html, success, is_valid_empty, used_cf = try_proxy_direct_then_cf(current_proxy_name)
            if success:
                logger.info(f"[Page {page_num}] Login refresh + retry succeeded (Proxy={current_proxy_name}, CF={used_cf})")
                return html, True, False, True, used_cf, False
            if is_valid_empty:
                return html, False, False, True, used_cf, True
            logger.warning(f"[Page {page_num}] Login refresh completed but index page still failed")
        elif login_success and not use_proxy:
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
            for _ in range(PROXY_POOL_MAX_FAILURES):
                state.global_proxy_pool.mark_failure_and_switch()
            proxy_was_banned = True

    for _ in range(max_switches):
        current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
        html, success, is_valid_empty, used_cf = try_proxy_direct_then_cf(current_proxy_name)
        if success:
            logger.info(f"[Page {page_num}] Index Proxy fallback succeeded (Proxy={current_proxy_name}, CF={used_cf})")
            return html, True, proxy_was_banned, True, used_cf, False
        if is_valid_empty:
            return html, False, proxy_was_banned, True, used_cf, True
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
        tuple: (magnets, actor_info, parse_success,
                effective_use_proxy, effective_use_cf_bypass)
    """
    last_result = ([], '', False)

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
                        login_ok, _, _proxy = attempt_login_refresh()
                        if login_ok:
                            html = state.get_page(detail_url, session, use_cookie=use_cookie,
                                                  use_proxy=u_proxy, module_name='spider',
                                                  max_retries=1, use_cf_bypass=u_cf)
                            if html and not is_login_page(html):
                                magnets, actor_info, parse_success = parse_detail(html, entry_index, skip_sleep=skip_sleep)
                                if parse_success:
                                    logger.info(f"[{entry_index}] Login refresh succeeded: {context_msg}")
                                    return magnets, actor_info, True
                                else:
                                    last_result = (magnets, actor_info, False)
                            else:
                                logger.warning(f"[{entry_index}] Still login page after refresh")
                    return [], '', False

                magnets, actor_info, parse_success = parse_detail(html, entry_index, skip_sleep=skip_sleep)
                if parse_success:
                    logger.debug(f"[{entry_index}] Success: {context_msg}")
                    return magnets, actor_info, True
                else:
                    last_result = (magnets, actor_info, False)
                    logger.debug(f"[{entry_index}] Parse validation failed (missing magnets): {context_msg}")
            else:
                logger.debug(f"[{entry_index}] Failed to fetch HTML: {context_msg}")
        except Exception as e:
            logger.debug(f"[{entry_index}] Failed {context_msg}: {e}")
        return [], '', False

    def try_proxy_direct_then_cf(proxy_name, skip_sleep=True):
        needs_cf = state.proxy_needs_cf_bypass(proxy_name)
        if needs_cf:
            magnets, actor_info, success = try_fetch_and_parse(
                True, True,
                f"Detail: Proxy={proxy_name} + CF Bypass (marked)",
                skip_sleep=skip_sleep)
            if success:
                return magnets, actor_info, True, True
            return [], '', False, True
        magnets, actor_info, success = try_fetch_and_parse(
            True, False,
            f"Detail: Proxy={proxy_name} Direct",
            skip_sleep=skip_sleep)
        if success:
            return magnets, actor_info, True, False
        magnets, actor_info, success = try_fetch_and_parse(
            True, True,
            f"Detail: Proxy={proxy_name} + CF Bypass",
            skip_sleep=skip_sleep)
        if success:
            state.mark_proxy_cf_bypass(proxy_name)
            return magnets, actor_info, True, True
        return [], '', False, False

    # --- Phase 0: Initial Attempt ---
    if use_proxy and state.global_proxy_pool:
        current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
        initial_cf = use_cf_bypass or state.proxy_needs_cf_bypass(current_proxy_name)
        magnets, actor_info, success = try_fetch_and_parse(
            True, initial_cf,
            f"Detail Initial (Proxy={current_proxy_name}, CF={initial_cf})",
            skip_sleep=False)
        if success:
            return magnets, actor_info, True, True, initial_cf
        if not initial_cf:
            magnets, actor_info, success = try_fetch_and_parse(
                True, True,
                f"Detail: Proxy={current_proxy_name} + CF Bypass",
                skip_sleep=True)
            if success:
                state.mark_proxy_cf_bypass(current_proxy_name)
                logger.info(f"[{entry_index}] Detail CF Bypass succeeded with initial proxy={current_proxy_name}")
                return magnets, actor_info, True, True, True
        logger.warning(f"[{entry_index}] Detail page initial attempt failed. Starting fallback...")
    elif not use_proxy:
        magnets, actor_info, success = try_fetch_and_parse(
            False, use_cf_bypass,
            f"Detail Initial (No Proxy, CF={use_cf_bypass})",
            skip_sleep=False)
        if success:
            return magnets, actor_info, True, False, use_cf_bypass
        logger.warning(f"[{entry_index}] Detail page initial attempt failed (no proxy). Starting fallback...")
    else:
        logger.warning(f"[{entry_index}] No proxy pool configured for initial attempt.")

    # --- Phase 1: Login Refresh ---
    if can_attempt_login(is_adhoc_mode, is_index_page=False):
        logger.info(f"[{entry_index}] Attempting login refresh...")
        login_success, _new_cookie, _proxy = attempt_login_refresh()
        if login_success and use_proxy and state.global_proxy_pool:
            current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
            magnets, actor_info, success, used_cf = try_proxy_direct_then_cf(current_proxy_name, skip_sleep=True)
            if success:
                logger.info(f"[{entry_index}] Login refresh + retry succeeded (Proxy={current_proxy_name}, CF={used_cf})")
                return magnets, actor_info, True, True, used_cf
            logger.warning(f"[{entry_index}] Login refresh completed but detail page still failed")
        elif login_success and not use_proxy:
            magnets, actor_info, success = try_fetch_and_parse(
                False, use_cf_bypass,
                "Detail: Retry with refreshed cookie (No Proxy)",
                skip_sleep=True)
            if success:
                return magnets, actor_info, True, False, use_cf_bypass
        elif not login_success:
            logger.warning(f"[{entry_index}] Login refresh failed, continuing with proxy pool fallback...")

    # --- Phase 2: Iterate through remaining proxies ---
    if state.global_proxy_pool is None:
        logger.error(f"[{entry_index}] Fallback failed: No proxy pool configured")
        return last_result[0], last_result[1], last_result[2], use_proxy, use_cf_bypass

    max_switches = state.global_proxy_pool.get_proxy_count() if PROXY_MODE == 'pool' else 1
    max_switches = min(max_switches, 10)

    for _ in range(max_switches):
        switched = state.global_proxy_pool.mark_failure_and_switch()
        if not switched:
            logger.warning(f"[{entry_index}] No more proxies available in pool")
            break
        current_proxy_name = state.global_proxy_pool.get_current_proxy_name()
        magnets, actor_info, success, used_cf = try_proxy_direct_then_cf(current_proxy_name, skip_sleep=True)
        if success:
            logger.info(f"[{entry_index}] Detail Proxy fallback succeeded (Proxy={current_proxy_name}, CF={used_cf})")
            return magnets, actor_info, True, True, used_cf

    logger.warning(f"[{entry_index}] Detail page fallback exhausted. Returning best available result.")
    return last_result[0], last_result[1], last_result[2], use_proxy, use_cf_bypass
