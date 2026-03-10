"""Index-page fetching and parsing across all pages."""

import os
import time
from typing import Optional

from utils.logging_config import get_logger
from utils.parser import parse_index
from utils.url_helper import detect_url_type
from utils.filename_helper import generate_output_csv_name_from_html
from utils.history_manager import has_complete_subtitles, should_skip_recent_yesterday_release

import scripts.spider.state as state
from scripts.spider.fallback import get_page_url, fetch_index_page_with_fallback
from scripts.spider.sleep_manager import movie_sleep_mgr
from scripts.spider.config_loader import PAGE_SLEEP

logger = get_logger(__name__)


def fetch_all_index_pages(
    session, start_page: int, end_page: int, parse_all: bool,
    phase_mode: str, custom_url: Optional[str], ignore_release_date: bool,
    use_proxy: bool, use_cf_bypass: bool, max_consecutive_empty: int,
    output_csv: str, output_dated_dir: str, csv_path: str,
    user_specified_output: bool,
    parsed_movies_history_phase1: dict, parsed_movies_history_phase2: dict,
) -> dict:
    """Fetch and parse all index pages, collecting entries for both phases.

    Returns a dict with keys:
        all_index_results_phase1, all_index_results_phase2,
        any_proxy_banned, use_proxy, use_cf_bypass, csv_path
    """
    all_index_results_phase1: list = []
    all_index_results_phase2: list = []
    any_proxy_banned = False
    last_valid_page = 0

    logger.info("=" * 75)
    logger.info("Fetching and parsing index pages")
    logger.info("=" * 75)

    page_num = start_page
    consecutive_empty_pages = 0
    csv_name_resolved = False

    while True:
        page_url = get_page_url(page_num, custom_url=custom_url)
        logger.debug(f"[Page {page_num}] Fetching: {page_url}")

        index_html, has_movie_list, proxy_was_banned, effective_use_proxy, effective_use_cf_bypass, is_valid_empty_page = fetch_index_page_with_fallback(
            page_url, session,
            use_cookie=custom_url is not None,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
            page_num=page_num,
            is_adhoc_mode=custom_url is not None,
        )

        if has_movie_list and effective_use_cf_bypass != use_cf_bypass:
            use_cf_bypass = effective_use_cf_bypass
        if has_movie_list and effective_use_proxy != use_proxy:
            use_proxy = effective_use_proxy

        if proxy_was_banned:
            any_proxy_banned = True

        if is_valid_empty_page:
            logger.info(f"[Page {page_num}] End of content reached (no more pages available)")
            break

        if not index_html:
            logger.info(f"[Page {page_num}] no movie list found (page fetch failed or does not exist)")
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= max_consecutive_empty:
                logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping fetch")
                break
            page_num += 1
            continue

        if not has_movie_list:
            logger.warning(f"[Page {page_num}] No movie list found after all fallback attempts")
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= max_consecutive_empty:
                logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping fetch")
                break
            page_num += 1
            continue

        p1_count = 0
        p2_count = 0

        if phase_mode in ['1', 'all']:
            page_results = parse_index(index_html, page_num, phase=1,
                                       disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                                       is_adhoc_mode=(custom_url is not None))
            p1_count = len(page_results)
            if p1_count > 0:
                all_index_results_phase1.extend(page_results)

        if phase_mode in ['2', 'all']:
            page_results_p2 = parse_index(index_html, page_num, phase=2,
                                          disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                                          is_adhoc_mode=(custom_url is not None))
            p2_count = len(page_results_p2)
            if p2_count > 0:
                all_index_results_phase2.extend(page_results_p2)

        if phase_mode == 'all':
            logger.info(f"[Page {page_num:2d}] Found {p1_count:3d} entries for phase 1, {p2_count:3d} for phase 2")
        elif phase_mode == '1':
            logger.info(f"[Page {page_num:2d}] Found {p1_count:3d} entries for phase 1")
        elif phase_mode == '2':
            logger.info(f"[Page {page_num:2d}] Found {p2_count:3d} entries for phase 2")

        last_valid_page = page_num
        consecutive_empty_pages = 0

        if custom_url is not None and not csv_name_resolved and not user_specified_output:
            url_type = detect_url_type(custom_url)
            if url_type in ('actors', 'makers', 'publishers', 'series', 'directors', 'video_codes'):
                resolved_csv_name = generate_output_csv_name_from_html(custom_url, index_html)
                if resolved_csv_name != output_csv:
                    output_csv = resolved_csv_name
                    csv_path = os.path.join(output_dated_dir, output_csv)
                    logger.info(f"[AdHoc] Updated CSV path: {csv_path}")
            csv_name_resolved = True

        if not parse_all and page_num >= end_page:
            break

        page_num += 1
        time.sleep(PAGE_SLEEP)

    logger.info(f"Fetched and parsed {last_valid_page - start_page + 1 if last_valid_page >= start_page else 0} pages")

    def _should_pre_skip(e, history):
        if has_complete_subtitles(e['href'], history):
            return True
        if custom_url is None and should_skip_recent_yesterday_release(
            e['href'], history, e.get('is_yesterday_release', False)
        ):
            return True
        return False

    _est_skip = sum(
        1 for e in all_index_results_phase1 if _should_pre_skip(e, parsed_movies_history_phase1)
    ) + sum(
        1 for e in all_index_results_phase2 if _should_pre_skip(e, parsed_movies_history_phase2)
    )
    _est_n = len(all_index_results_phase1) + len(all_index_results_phase2) - _est_skip
    logger.info(f"Estimated processing volume: N={_est_n} (total={len(all_index_results_phase1)+len(all_index_results_phase2)}, pre-skip={_est_skip})")
    movie_sleep_mgr.apply_volume_multiplier(_est_n)

    return {
        'all_index_results_phase1': all_index_results_phase1,
        'all_index_results_phase2': all_index_results_phase2,
        'any_proxy_banned': any_proxy_banned,
        'use_proxy': use_proxy,
        'use_cf_bypass': use_cf_bypass,
        'csv_path': csv_path,
        'last_valid_page': last_valid_page,
    }
