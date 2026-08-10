"""Index-page fetching and parsing across all pages."""

import os
from threading import Event
from typing import Optional

from javdb.infra.logging import get_logger, log_section
from javdb.parsing import parse_index_page
from javdb.pipeline.index_family_blacklist import (
    filter_blacklisted_families,
    load_daily_family_blacklist,
    log_family_blacklist_summary,
)
from javdb.pipeline.index_code_blacklist import (
    filter_blacklisted_code_keywords,
    load_daily_code_keyword_blacklist,
    log_code_keyword_blacklist_summary,
)
from javdb.pipeline.index_selection import (
    count_new_release_entries,
    select_index_entries,
    IGNORE_RELEASE_DATE_FILTER,
)
from javdb.spider.url_helper import detect_url_type
from javdb.spider.filename_helper import generate_output_csv_name_from_html
from javdb.pipeline.policies import (
    has_complete_subtitles,
    should_skip_recent_yesterday_release,
    should_skip_recent_today_release,
)

import javdb.spider.runtime.state as state
from javdb.spider.fetch.fallback import get_page_url, fetch_index_page_with_fallback
from javdb.spider.fetch.page_scan import PageScanPolicy
from javdb.spider.runtime.config import (
    PAGE_SCAN_DYNAMIC, PAGE_SCAN_MAX, PAGE_SCAN_STOP_AFTER,
)
from javdb.spider.runtime.sleep import ensure_sleep_runtime, movie_sleep_mgr
from javdb.ops.sentinel import field_health as _sentinel_field_health

logger = get_logger(__name__)


def _sleep_manager(runtime=None):
    runtime = runtime or state.get_active_runtime()
    if runtime is not None:
        return ensure_sleep_runtime(runtime).movie_sleep_mgr
    return movie_sleep_mgr


def _log_page_scan_outcome(policy, last_valid_page: int, end_page: int) -> None:
    """Log where the dynamic scan stopped, warning when the cap cut it short."""
    if not policy.enabled:
        return
    if policy.hit_cap:
        logger.warning(
            "Page scan hit the hard cap at page %d (PAGE_SCAN_MAX=%d) while pages "
            "were still fresh — today's new torrents may be truncated. Raise "
            "PAGE_SCAN_MAX if this repeats.",
            last_valid_page, policy.max_page,
        )
    elif last_valid_page > end_page:
        logger.info(
            "Page scan extended past the configured end: %d -> %d (%s)",
            end_page, last_valid_page, policy.stop_reason,
        )


def _empty_index_result(
    csv_path: str, use_proxy: bool, use_cf_bypass: bool, end_page: int,
) -> dict:
    """The result shape for a scan that had no pages to fetch."""
    return {
        'all_index_results_phase1': [],
        'all_index_results_phase2': [],
        'any_proxy_banned': False,
        'use_proxy': use_proxy,
        'use_cf_bypass': use_cf_bypass,
        'csv_path': csv_path,
        'last_valid_page': 0,
        'effective_end_page': None,
        'page_scan_stop_reason': None,
    }


def build_page_scan_policy(
    *, end_page: int, parse_all: bool, custom_url: Optional[str],
    ignore_release_date: bool,
) -> PageScanPolicy:
    """Build the ADR-057 scan policy for this run.

    The dynamic extension only makes sense in daily mode: ad-hoc URLs, ``--all``
    and either release-date bypass have no today/yesterday signal to steer on,
    so the policy is built disabled and the scan keeps its fixed range.
    """
    enabled = bool(
        PAGE_SCAN_DYNAMIC
        and not parse_all
        and custom_url is None
        and not ignore_release_date
        and not IGNORE_RELEASE_DATE_FILTER
    )
    policy = PageScanPolicy(
        floor_page=end_page,
        max_page=PAGE_SCAN_MAX,
        stop_after=PAGE_SCAN_STOP_AFTER,
        enabled=enabled,
    )
    if policy.stop_after != policy.stop_after_raw:
        logger.warning(
            "PAGE_SCAN_STOP_AFTER=%s is not a usable budget — scanning with %d. "
            "Honouring it would stop the scan on its first page past page %d "
            "even if that page were full of new torrents.",
            policy.stop_after_raw, policy.stop_after, end_page,
        )
    return policy


def fetch_all_index_pages(
    *,
    runtime=None,
    session, start_page: int, end_page: int, parse_all: bool,
    phase_mode: str, custom_url: Optional[str], ignore_release_date: bool,
    use_proxy: bool, use_cf_bypass: bool, max_consecutive_empty: int,
    output_csv: str, output_dated_dir: str, csv_path: str,
    user_specified_output: bool,
    parsed_movies_history_phase1: dict, parsed_movies_history_phase2: dict,
    use_parallel: bool = False,
    cancel_event: Event | None = None,
) -> dict:
    """Fetch and parse all index pages, collecting entries for both phases.

    When *use_parallel* is ``True`` the request work is delegated to a
    :class:`ParallelFetchBackend` with one worker per proxy, mirroring
    the detail-page parallel model.

    Returns a dict with keys:
        all_index_results_phase1, all_index_results_phase2,
        any_proxy_banned, use_proxy, use_cf_bypass, csv_path
    """

    log_section(
        logger,
        f"INDEX · pages {start_page}-{end_page if not parse_all else '*'}"
        f"{' (parallel)' if use_parallel else ''}",
        emoji='🌐',
    )

    if not parse_all and start_page > end_page:
        # A reversed range used to stop after one page on the `page_num >=
        # end_page` check. Under ADR-057 every page sits past the floor, so a
        # fresh listing would extend the scan to the cap — reject it instead.
        logger.warning(
            "Empty page range requested (start_page=%d > end_page=%d) — nothing to scan",
            start_page, end_page,
        )
        return _empty_index_result(csv_path, use_proxy, use_cf_bypass, end_page)

    from javdb.spider.runtime.config import PROXY_POOL
    active_workers = len(PROXY_POOL) if (use_parallel and PROXY_POOL) else 1

    if use_parallel:
        from javdb.spider.fetch.index_parallel import (
            fetch_all_index_pages_parallel,
        )
        idx_result = fetch_all_index_pages_parallel(
            runtime=runtime,
            start_page=start_page, end_page=end_page,
            parse_all=parse_all, phase_mode=phase_mode,
            custom_url=custom_url, ignore_release_date=ignore_release_date,
            use_proxy=use_proxy, use_cf_bypass=use_cf_bypass,
            max_consecutive_empty=max_consecutive_empty,
            output_csv=output_csv, output_dated_dir=output_dated_dir,
            csv_path=csv_path, user_specified_output=user_specified_output,
            cancel_event=cancel_event,
        )
        return _post_process_index_results(
            idx_result, custom_url,
            parsed_movies_history_phase1, parsed_movies_history_phase2,
            num_workers=active_workers,
            runtime=runtime,
        )

    return _fetch_all_index_pages_sequential(
        runtime=runtime,
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
        cancel_event=cancel_event,
    )


def _fetch_all_index_pages_sequential(
    runtime, session, start_page: int, end_page: int, parse_all: bool,
    phase_mode: str, custom_url: Optional[str], ignore_release_date: bool,
    use_proxy: bool, use_cf_bypass: bool, max_consecutive_empty: int,
    output_csv: str, output_dated_dir: str, csv_path: str,
    user_specified_output: bool,
    parsed_movies_history_phase1: dict, parsed_movies_history_phase2: dict,
    num_workers: int = 1,
    cancel_event: Event | None = None,
) -> dict:
    """Original sequential index fetch logic."""

    all_index_results_phase1: list = []
    all_index_results_phase2: list = []
    any_proxy_banned = False
    last_valid_page = 0

    page_num = start_page
    last_scanned_page = 0
    consecutive_empty_pages = 0
    csv_name_resolved = False

    _sentinel_field_health.start_run()  # ADR-035: begin per-run field-health
    family_blacklist_counts: dict[str, int] = {}
    daily_family_blacklist = load_daily_family_blacklist(custom_url)
    code_keyword_blacklist_counts: dict[str, int] = {}
    daily_code_keyword_blacklist = load_daily_code_keyword_blacklist(custom_url)

    # ADR-057: end_page is a floor — the policy decides how far past it the
    # scan reaches, based on each page's raw today/yesterday badge count.
    policy = build_page_scan_policy(
        end_page=end_page, parse_all=parse_all, custom_url=custom_url,
        ignore_release_date=ignore_release_date,
    )
    if policy.enabled:
        logger.info(
            "Dynamic page scan: floor=%d, cap=%d, stop after %d fresh-free pages",
            policy.floor_page, policy.max_page, policy.stop_after,
        )
        # The empty-page tolerance is a breaker for an unreachable site, but it
        # must not fire before the policy's own unreadable budget when that is
        # configured higher — the run would stop early with no stop reason.
        max_consecutive_empty = max(max_consecutive_empty, policy.stop_after)

    while True:
        if cancel_event is not None and cancel_event.is_set():
            logger.info("Index page fetch cancelled")
            raise SystemExit(124)

        page_url = get_page_url(page_num, custom_url=custom_url)
        last_scanned_page = page_num
        logger.debug(f"[Page {page_num}] Fetching: {page_url}")

        index_html, has_movie_list, proxy_was_banned, effective_use_proxy, effective_use_cf_bypass, is_valid_empty_page = fetch_index_page_with_fallback(
            page_url, session,
            use_cookie=custom_url is not None,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
            page_num=page_num,
            is_adhoc_mode=custom_url is not None,
            runtime=runtime,
        )

        if has_movie_list and effective_use_cf_bypass != use_cf_bypass:
            use_cf_bypass = effective_use_cf_bypass
        if has_movie_list and effective_use_proxy != use_proxy:
            use_proxy = effective_use_proxy

        if proxy_was_banned:
            any_proxy_banned = True

        if is_valid_empty_page:
            logger.info(f"[Page {page_num}] End of content reached (no more pages available)")
            policy.observe(page_num, fresh=None, end_of_content=True)
            break

        if not index_html:
            logger.info(f"[Page {page_num}] no movie list found (page fetch failed or does not exist)")
            consecutive_empty_pages += 1
            # ADR-057 D5: a page we could not read is unknown, not fresh-free.
            # The policy is consulted before the empty-page tolerance so a
            # configured unreadable budget is spent in full and the run reports
            # why it stopped, instead of the tolerance ending it silently.
            policy.observe(page_num, fresh=None)
            # `not parse_all` rather than `policy.enabled`: a fixed range has
            # to end at its floor even when that page failed to load, or the
            # loop walks past PAGE_END on the empty-page tolerance alone.
            if not parse_all and not policy.should_continue_after(page_num):
                break
            if consecutive_empty_pages >= max_consecutive_empty:
                logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping fetch")
                break
            page_num += 1
            continue

        if not has_movie_list:
            logger.warning(f"[Page {page_num}] No movie list found after all fallback attempts")
            consecutive_empty_pages += 1
            policy.observe(page_num, fresh=None)
            if not parse_all and not policy.should_continue_after(page_num):
                break
            if consecutive_empty_pages >= max_consecutive_empty:
                logger.info(f"[Page {page_num}] Reached maximum tolerance ({max_consecutive_empty} consecutive empty pages), stopping fetch")
                break
            page_num += 1
            continue

        p1_count = 0
        p2_count = 0

        page_result = parse_index_page(index_html, page_num)

        # ADR-057 D2: count freshness before the blacklists run — a blacklisted
        # entry still proves the page sits inside the fresh block.
        policy.observe(page_num, fresh=count_new_release_entries(page_result))

        _acc = _sentinel_field_health.current()
        if _acc is not None and page_result is not None:
            _acc.observe("index", page_result.movies)  # ADR-035 piggyback

        if page_result is not None and daily_family_blacklist:
            page_result.movies = filter_blacklisted_families(
                page_result.movies,
                daily_family_blacklist,
                family_blacklist_counts,
            )

        if page_result is not None and daily_code_keyword_blacklist:
            page_result.movies = filter_blacklisted_code_keywords(
                page_result.movies,
                daily_code_keyword_blacklist,
                code_keyword_blacklist_counts,
            )

        if phase_mode in ['1', 'all']:
            page_results = select_index_entries(
                page_result,
                page_num=page_num,
                phase=1,
                disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                is_adhoc_mode=(custom_url is not None),
            )
            p1_count = len(page_results)
            if p1_count > 0:
                all_index_results_phase1.extend(page_results)

        if phase_mode in ['2', 'all']:
            page_results_p2 = select_index_entries(
                page_result,
                page_num=page_num,
                phase=2,
                disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                is_adhoc_mode=(custom_url is not None),
            )
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

        if not parse_all and not policy.should_continue_after(page_num):
            break

        page_num += 1
        _sleep_manager(runtime).sleep()

    _log_page_scan_outcome(policy, last_scanned_page, end_page)
    logger.info(f"Fetched and parsed {last_valid_page - start_page + 1 if last_valid_page >= start_page else 0} pages")
    log_family_blacklist_summary(logger, family_blacklist_counts)
    log_code_keyword_blacklist_summary(logger, code_keyword_blacklist_counts)

    # ADR-035: do NOT persist field-health here — the report session does not
    # exist yet at index-fetch time (run_service creates it afterwards, since
    # the CSV name can only be resolved from this result). The accumulator stays
    # in the process-global _CURRENT; run_service persists it once the active
    # session id is set. (Persisting here would no-op: no active session.)
    return _post_process_index_results(
        {
            # Dynamic-scan only: with a fixed range the configured end already
            # describes the run, and callers fall back to their own value.
            'effective_end_page': last_scanned_page if policy.enabled else None,
            'page_scan_stop_reason': policy.stop_reason,
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
        runtime=runtime,
    )


def _post_process_index_results(
    idx_result: dict,
    custom_url: Optional[str],
    parsed_movies_history_phase1: dict,
    parsed_movies_history_phase2: dict,
    *,
    num_workers: int = 1,
    runtime=None,
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
    _sleep_manager(runtime).apply_volume_multiplier(
        _est_n, num_workers=max(1, num_workers),
    )

    return idx_result
