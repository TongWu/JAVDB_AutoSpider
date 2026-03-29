"""Index-page fetching and parsing across all pages."""

import os
from typing import Optional

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_core.parser import parse_index
from packages.python.javdb_core.url_helper import detect_url_type
from packages.python.javdb_core.filename_helper import generate_output_csv_name_from_html
from packages.python.javdb_ingestion.policies import (
    has_complete_subtitles,
    should_skip_recent_yesterday_release,
    should_skip_recent_today_release,
)

import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_spider.fetch.fallback import get_page_url, fetch_index_page_with_fallback
from packages.python.javdb_spider.runtime.sleep import movie_sleep_mgr

logger = get_logger(__name__)


def fetch_all_index_pages(
    session, start_page: int, end_page: int, parse_all: bool,
    phase_mode: str, custom_url: Optional[str], ignore_release_date: bool,
    use_proxy: bool, use_cf_bypass: bool, max_consecutive_empty: int,
    output_csv: str, output_dated_dir: str, csv_path: str,
    user_specified_output: bool,
    parsed_movies_history_phase1: dict, parsed_movies_history_phase2: dict,
    use_parallel: bool = False,
) -> dict:
    """Fetch and parse all index pages, collecting entries for both phases.

    When *use_parallel* is ``True`` the request work is delegated to a
    :class:`ParallelFetchBackend` with one worker per proxy, mirroring
    the detail-page parallel model.

    Returns a dict with keys:
        all_index_results_phase1, all_index_results_phase2,
        any_proxy_banned, use_proxy, use_cf_bypass, csv_path
    """

    logger.info("=" * 75)
    logger.info("Fetching and parsing index pages%s", " (parallel)" if use_parallel else "")
    logger.info("=" * 75)

    from packages.python.javdb_spider.runtime.config import PROXY_POOL
    active_workers = len(PROXY_POOL) if (use_parallel and PROXY_POOL) else 1

    if use_parallel:
        from packages.python.javdb_spider.fetch.index_parallel import (
            fetch_all_index_pages_parallel,
        )
        idx_result = fetch_all_index_pages_parallel(
            start_page=start_page, end_page=end_page,
            parse_all=parse_all, phase_mode=phase_mode,
            custom_url=custom_url, ignore_release_date=ignore_release_date,
            use_proxy=use_proxy, use_cf_bypass=use_cf_bypass,
            max_consecutive_empty=max_consecutive_empty,
            output_csv=output_csv, output_dated_dir=output_dated_dir,
            csv_path=csv_path, user_specified_output=user_specified_output,
        )
        return _post_process_index_results(
            idx_result, custom_url,
            parsed_movies_history_phase1, parsed_movies_history_phase2,
            num_workers=active_workers,
        )

    return _fetch_all_index_pages_sequential(
        session=session, start_page=start_page, end_page=end_page,
        parse_all=parse_all, phase_mode=phase_mode, custom_url=custom_url,
        ignore_release_date=ignore_release_date, use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
        max_consecutive_empty=max_consecutive_empty,
        output_csv=output_csv, output_dated_dir=output_dated_dir,
        csv_path=csv_path, user_specified_output=user_specified_output,
        parsed_movies_history_phase1=parsed_movies_history_phase1,
        parsed_movies_history_phase2=parsed_movies_history_phase2,
        num_workers=active_workers,
    )


def _fetch_all_index_pages_sequential(
    session, start_page: int, end_page: int, parse_all: bool,
    phase_mode: str, custom_url: Optional[str], ignore_release_date: bool,
    use_proxy: bool, use_cf_bypass: bool, max_consecutive_empty: int,
    output_csv: str, output_dated_dir: str, csv_path: str,
    user_specified_output: bool,
    parsed_movies_history_phase1: dict, parsed_movies_history_phase2: dict,
    num_workers: int = 1,
) -> dict:
    """Original sequential index fetch logic."""

    all_index_results_phase1: list = []
    all_index_results_phase2: list = []
    any_proxy_banned = False
    last_valid_page = 0

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
        movie_sleep_mgr.sleep()

    logger.info(f"Fetched and parsed {last_valid_page - start_page + 1 if last_valid_page >= start_page else 0} pages")

    return _post_process_index_results(
        {
            'all_index_results_phase1': all_index_results_phase1,
            'all_index_results_phase2': all_index_results_phase2,
            'any_proxy_banned': any_proxy_banned,
            'use_proxy': use_proxy,
            'use_cf_bypass': use_cf_bypass,
            'csv_path': csv_path,
            'last_valid_page': last_valid_page,
        },
        custom_url,
        parsed_movies_history_phase1,
        parsed_movies_history_phase2,
        num_workers=num_workers,
    )


def _post_process_index_results(
    idx_result: dict,
    custom_url: Optional[str],
    parsed_movies_history_phase1: dict,
    parsed_movies_history_phase2: dict,
    *,
    num_workers: int = 1,
) -> dict:
    """Estimate processing volume and apply the sleep volume multiplier."""
    all_p1 = idx_result['all_index_results_phase1']
    all_p2 = idx_result['all_index_results_phase2']

    def _should_pre_skip(e, history):
        if has_complete_subtitles(e['href'], history):
            return True
        if custom_url is None and should_skip_recent_yesterday_release(
            e['href'], history, e.get('is_yesterday_release', False)
        ):
            return True
        if custom_url is None and should_skip_recent_today_release(
            e['href'], history, e.get('is_today_release', False)
        ):
            return True
        return False

    _est_skip = sum(
        1 for e in all_p1 if _should_pre_skip(e, parsed_movies_history_phase1)
    ) + sum(
        1 for e in all_p2 if _should_pre_skip(e, parsed_movies_history_phase2)
    )
    _est_n = len(all_p1) + len(all_p2) - _est_skip
    logger.info(
        "Estimated processing volume: N=%d (total=%d, pre-skip=%d)",
        _est_n, len(all_p1) + len(all_p2), _est_skip,
    )
    movie_sleep_mgr.apply_volume_multiplier(
        _est_n, num_workers=max(1, num_workers),
    )

    return idx_result
