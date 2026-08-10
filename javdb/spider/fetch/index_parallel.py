"""Parallel index-page fetching backed by the shared FetchEngine.

Submits index-page URLs to a ``ParallelFetchBackend``, collects results,
sorts them by page number, and applies canonical index selection exactly as the
sequential path does — but with one worker per proxy running concurrently.

Two submission strategies:

* **Fixed range** (``parse_all=False``): all pages from *start_page* to
  *end_page* are submitted up-front.
* **Sliding window** (``parse_all=True``): pages are submitted in batches;
  the window advances as results arrive, and stops when consecutive empty
  pages reach the threshold.
"""

from __future__ import annotations

import os
from threading import Event
from typing import Dict, List, Optional

from javdb.infra.logging import get_logger
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
)
from javdb.spider.url_helper import detect_url_type
from javdb.spider.filename_helper import generate_output_csv_name_from_html

from javdb.spider.fetch.fallback import (
    get_page_url,
    validate_index_html,
)
from javdb.spider.fetch.backend import FetchRuntimeState
from javdb.spider.fetch.page_scan import STOP_END_OF_CONTENT, STOP_PROXIES_EXHAUSTED
# Both fetch paths share one scan policy (ADR-057 D8). ``index`` imports this
# module lazily, inside the function, so importing back at module level is safe.
from javdb.spider.fetch.index import (
    build_page_scan_policy,
    _log_page_scan_outcome,
)
from javdb.spider.fetch.fetch_engine import (
    EngineTask,
    EngineResult,
    ParallelFetchBackend,
)
from javdb.spider.runtime.config import PROXY_POOL
from javdb.ops.sentinel import field_health as _sentinel_field_health

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Parse callback for FetchEngine.simple
# ---------------------------------------------------------------------------

def _index_parse_fn(html: str, task: EngineTask) -> Optional[dict]:
    """Validate an index page and return the result dict.

    Returns ``None`` when validation fails (page is a login wall, CF
    challenge, etc.) so the engine re-queues the task to another proxy.

    The page is parsed here, in the worker, and the result is carried on the
    dict: the collection loop needs its freshness count to steer the ADR-057
    scan, and the post-loop selection needs the entries. Parsing once keeps the
    dynamic scan from doubling the parse cost of every page.
    """
    page_num = task.meta.get('page_num', 0)
    result_html, has_movie_list, is_valid_empty = validate_index_html(
        html, page_num=page_num, context_msg=f'parallel index page {page_num}',
    )
    if result_html is None:
        return None
    page_result = parse_index_page(html, page_num) if has_movie_list else None
    return {
        'html': html,
        'has_movie_list': has_movie_list,
        'is_valid_empty': is_valid_empty,
        'page_result': page_result,
        'fresh': count_new_release_entries(page_result),
    }


def _page_freshness(result) -> tuple:
    """``(fresh, end_of_content)`` for one engine result, for the scan policy.

    ``fresh`` is ``None`` whenever the page could not be read — ADR-057 D5 keeps
    those from being mistaken for "no new torrents here".
    """
    if not result.success or not result.data:
        return None, False
    data = result.data
    if data.get('is_valid_empty'):
        return None, True
    if not data.get('has_movie_list'):
        return None, False
    return data.get('fresh'), False


# ---------------------------------------------------------------------------
# Backend builder
# ---------------------------------------------------------------------------

def build_parallel_index_backend(
    *,
    runtime=None,
    use_cookie: bool,
    use_proxy: bool = True,
    use_cf_bypass: bool = False,
) -> ParallelFetchBackend:
    """Build an index-page parallel backend.

    Uses a priority queue so pages are processed in ascending page-number
    order.  This allows the sliding-window stop condition to trigger as
    early as possible (the contiguous sequence from ``start_page`` builds
    up faster when lower pages are dequeued first).
    """
    return ParallelFetchBackend.simple(
        parse_fn=_index_parse_fn,
        use_cookie=use_cookie,
        use_priority_queue=True,
        runtime_state=FetchRuntimeState(
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
        ),
        runtime=runtime,
    )


# ---------------------------------------------------------------------------
# Parallel orchestration
# ---------------------------------------------------------------------------

def fetch_all_index_pages_parallel(
    *,
    runtime=None,
    start_page: int,
    end_page: int,
    parse_all: bool,
    phase_mode: str,
    custom_url: Optional[str],
    ignore_release_date: bool,
    use_proxy: bool,
    use_cf_bypass: bool,
    max_consecutive_empty: int,
    output_csv: str,
    output_dated_dir: str,
    csv_path: str,
    user_specified_output: bool,
    cancel_event: Event | None = None,
) -> dict:
    """Fetch index pages in parallel and return the same dict as the
    sequential ``fetch_all_index_pages``.

    Returns dict with keys:
        all_index_results_phase1, all_index_results_phase2,
        any_proxy_banned, use_proxy, use_cf_bypass, csv_path,
        last_valid_page
    """

    policy = build_page_scan_policy(
        end_page=end_page, parse_all=parse_all, custom_url=custom_url,
        ignore_release_date=ignore_release_date,
    )
    if policy.enabled:
        logger.info(
            "Dynamic page scan: floor=%d, cap=%d, stop after %d fresh-free pages",
            policy.floor_page, policy.max_page, policy.stop_after,
        )

    backend = build_parallel_index_backend(
        runtime=runtime,
        use_cookie=custom_url is not None,
        use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
    )
    backend.start()

    try:
        # -- submit tasks ---------------------------------------------------
        if cancel_event is not None and cancel_event.is_set():
            logger.info("Parallel index fetch cancelled before submission")
            raise SystemExit(124)

        # Cookie mode (ad-hoc URLs) is served by whichever worker holds the
        # session cookie, so submit a single page first and let its result
        # settle whether one is needed.  If the pages want a login, the wall
        # that page hits designates the owner and every page released after
        # it is pinned to that worker — without the probe a cold start (no
        # login state inherited from the DO) would submit the whole window
        # or the whole range before any owner exists, and each of those
        # pages would pay its own login wall.  If the cookie works on every
        # proxy no owner appears and the rest go out with the usual
        # fan-out, so nothing is serialised needlessly.
        probe_first = custom_url is not None
        pending_pages: List[int] = []

        if parse_all:
            window_size = max(len(PROXY_POOL) * 2, 4) if PROXY_POOL else 4
            next_page = start_page
            in_flight = 0
            for _ in range(1 if probe_first else window_size):
                _submit_page(backend, next_page, custom_url)
                next_page += 1
                in_flight += 1
        else:
            all_pages = list(range(start_page, end_page + 1))
            if probe_first and len(all_pages) > 1:
                _submit_page(backend, all_pages[0], custom_url)
                pending_pages = all_pages[1:]
            else:
                for p in all_pages:
                    _submit_page(backend, p, custom_url)
                # ADR-057: with the dynamic scan on, end_page is only a floor —
                # hold the queue open so the collection loop can extend it.
                # An empty range (start_page > end_page) has nothing to extend
                # from and would never reach the loop body, so close the queue
                # immediately: results() only returns once mark_done() is in.
                if not policy.enabled or not all_pages:
                    backend.mark_done()

        # -- collect results (may arrive out of order) ----------------------

        # With the dynamic scan the page count is not known up front, so the
        # progress line shows a running count instead of a wrong denominator.
        total_expected = (
            end_page - start_page + 1
            if not parse_all and not policy.enabled else 0
        )
        results_by_page: Dict[int, EngineResult] = {}
        any_proxy_banned = False
        csv_name_resolved = False
        all_index_results_phase1: List[dict] = []
        all_index_results_phase2: List[dict] = []
        last_valid_page = 0
        stop_collecting = False
        # ADR-057 dynamic scan bookkeeping: results arrive out of order, so the
        # policy is fed the contiguous prefix and extends one page at a time.
        next_observe_page = start_page
        next_submit_page = end_page + 1
        extension_closed = False
        # The page the policy actually stopped on. Pages submitted for the floor
        # keep landing after an early stop, so the highest result is not the
        # place the scan ended — e.g. page 1 reporting end-of-content while
        # pages 2-3 were already in flight.
        scan_end_page = 0

        for result in backend.results():
            if cancel_event is not None and cancel_event.is_set():
                logger.info("Parallel index fetch cancelled")
                raise SystemExit(124)

            page_num = result.task.meta.get('page_num', 0)
            results_by_page[page_num] = result

            collected = len(results_by_page)
            worker_tag = f"[worker={result.worker_name}]" if result.worker_name else ""
            progress = f"{collected}/{total_expected}" if total_expected else str(collected)
            if result.success:
                has_movies = bool(result.data and result.data.get('has_movie_list'))
                logger.info(
                    "[Page %2d]%s Received (%s) — %s pages collected",
                    page_num, worker_tag,
                    "has content" if has_movies else "empty",
                    progress,
                )
            else:
                logger.warning(
                    "[Page %2d]%s Failed (%s) — %s pages collected",
                    page_num, worker_tag,
                    result.error or "unknown", progress,
                )

            if result.success and result.data:
                data = result.data
                if data.get('has_movie_list'):
                    html = data['html']

                    if custom_url is not None and not csv_name_resolved and not user_specified_output:
                        url_type = detect_url_type(custom_url)
                        if url_type in ('actors', 'makers', 'publishers', 'series', 'directors', 'video_codes'):
                            resolved_csv_name = generate_output_csv_name_from_html(custom_url, html)
                            if resolved_csv_name != output_csv:
                                output_csv = resolved_csv_name
                                csv_path = os.path.join(output_dated_dir, output_csv)
                                logger.info("[AdHoc] Updated CSV path: %s", csv_path)
                        csv_name_resolved = True

            if not result.success:
                if result.error == 'all_proxies_banned':
                    any_proxy_banned = True
                    # The engine drains the queue as it stands and its workers
                    # exit, so a page submitted after this point has nobody to
                    # fetch it and never produces a result — and the stalled-task
                    # flush only runs once the backend is done. Submitting
                    # anything more hangs the run outright, so everything still
                    # unsent is abandoned here. That covers the rest of a fixed
                    # range as well as the extension: an ad-hoc URL disables the
                    # policy but still holds pending_pages.
                    if not extension_closed:
                        extension_closed = True
                        # Without this the reported end page stays 0 when the ban
                        # lands before the first observation.
                        scan_end_page = max(scan_end_page, page_num)
                        # D9: without a reason, a run cut short by a dead proxy
                        # pool reports none at all and reads as a clean scan.
                        policy.force_stop(STOP_PROXIES_EXHAUSTED)
                        dropped = pending_pages
                        pending_pages = []
                        backend.mark_done()
                        logger.warning(
                            "All proxies banned — ending the page scan at page "
                            "%d; %d queued page(s) abandoned",
                            scan_end_page, len(dropped),
                        )

            # -- probe answered: release the rest of a fixed range ----------

            if pending_pages:
                for p in pending_pages:
                    _submit_page(backend, p, custom_url)
                pending_pages = []
                backend.mark_done()

            # -- dynamic scan: extend the fixed range while pages are fresh --

            if policy.enabled and not extension_closed:
                while next_observe_page in results_by_page:
                    fresh, end_of_content = _page_freshness(
                        results_by_page[next_observe_page],
                    )
                    policy.observe(
                        next_observe_page, fresh=fresh,
                        end_of_content=end_of_content,
                    )
                    scan_end_page = next_observe_page
                    next_observe_page += 1
                    if policy.stop_reason == STOP_END_OF_CONTENT:
                        break
                highest_observed = next_observe_page - 1

                # One page at a time, gated on the page before it: prefetching a
                # window would burn a guaranteed extra fetch on every quiet day
                # (the scan already pays one confirmation page at the floor) to
                # save latency only on the rarer days that actually extend.
                while (
                    next_submit_page <= highest_observed + 1
                    and policy.should_continue_after(highest_observed)
                ):
                    _submit_page(backend, next_submit_page, custom_url)
                    next_submit_page += 1

                if not policy.should_continue_after(highest_observed):
                    extension_closed = True
                    backend.mark_done()

            # -- sliding window: advance or stop ----------------------------

            if parse_all and not stop_collecting:
                # A dead proxy pool already marked the backend done, and the
                # engine rejects a submit after that. The window has to stop
                # feeding it rather than crash the run on its way out.
                if extension_closed:
                    stop_collecting = True
                    break
                should_stop = _check_stop_condition(
                    results_by_page, start_page, max_consecutive_empty,
                )
                if should_stop:
                    stop_collecting = True
                    backend.mark_done()
                    logger.info(
                        "Stop condition met after %d results — "
                        "exiting collection loop (remaining in-flight "
                        "tasks will be cancelled)",
                        len(results_by_page),
                    )
                    break
                else:
                    in_flight -= 1
                    while in_flight < window_size:
                        _submit_page(backend, next_page, custom_url)
                        next_page += 1
                        in_flight += 1
    finally:
        backend.shutdown()
        backend.export_login_state()

    # -- process results in page order --------------------------------------

    sorted_pages = sorted(results_by_page.keys())
    consecutive_empty = 0

    _sentinel_field_health.start_run()  # ADR-035: begin per-run field-health (parallel path)
    family_blacklist_counts: dict[str, int] = {}
    daily_family_blacklist = load_daily_family_blacklist(custom_url)
    code_keyword_blacklist_counts: dict[str, int] = {}
    daily_code_keyword_blacklist = load_daily_code_keyword_blacklist(custom_url)

    for page_num in sorted_pages:
        result = results_by_page[page_num]

        if not result.success:
            logger.warning("[Page %d] Fetch failed (parallel), skipping", page_num)
            continue

        if not result.data:
            logger.info("[Page %d] No data returned (parallel)", page_num)
            consecutive_empty += 1
            if consecutive_empty >= max_consecutive_empty:
                break
            continue

        data = result.data
        if data.get('is_valid_empty'):
            logger.info("[Page %d] End of content reached (no more pages available)", page_num)
            break

        if not data.get('has_movie_list'):
            consecutive_empty += 1
            if consecutive_empty >= max_consecutive_empty:
                break
            continue

        consecutive_empty = 0
        last_valid_page = page_num
        html = data['html']

        p1_count = 0
        p2_count = 0
        # Parsed once already, in the worker (see _index_parse_fn).
        page_result = data.get('page_result')
        if page_result is None:
            page_result = parse_index_page(html, page_num)
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
            logger.info("[Page %2d] Found %3d entries for phase 1, %3d for phase 2", page_num, p1_count, p2_count)
        elif phase_mode == '1':
            logger.info("[Page %2d] Found %3d entries for phase 1", page_num, p1_count)
        elif phase_mode == '2':
            logger.info("[Page %2d] Found %3d entries for phase 2", page_num, p2_count)

    _log_page_scan_outcome(policy, scan_end_page, end_page)
    logger.info(
        "Fetched and parsed %d pages (parallel)",
        last_valid_page - start_page + 1 if last_valid_page >= start_page else 0,
    )
    log_family_blacklist_summary(logger, family_blacklist_counts)
    log_code_keyword_blacklist_summary(logger, code_keyword_blacklist_counts)

    # ADR-035: do NOT persist field-health here — the report session does not
    # exist yet at index-fetch time (run_service creates it afterwards). The
    # accumulator stays in the process-global _CURRENT; run_service persists it
    # once the active session id is set. (Persisting here would no-op.)
    return {
        'effective_end_page': scan_end_page if policy.enabled else None,
        'page_scan_stop_reason': policy.stop_reason,
        'all_index_results_phase1': all_index_results_phase1,
        'all_index_results_phase2': all_index_results_phase2,
        'any_proxy_banned': any_proxy_banned,
        'use_proxy': use_proxy,
        'use_cf_bypass': use_cf_bypass,
        'csv_path': csv_path,
        'last_valid_page': last_valid_page,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _submit_page(
    backend: ParallelFetchBackend,
    page_num: int,
    custom_url: Optional[str],
) -> None:
    """Submit one index page, pinning it to the logged-in worker when needed.

    Ad-hoc URLs (``custom_url``) need the session cookie, and the cookie is
    injected into the single worker whose proxy performed the login — every
    other worker carries a stale one and is guaranteed to hit the login wall,
    burn a fetch, and hand the page back via ``login_queue`` anyway. Submit
    those pages straight to that worker (sequentially, in page order) instead.

    Before a login owner exists the page goes to the shared queue as usual —
    that first login wall is what designates the owner.
    """
    url = get_page_url(page_num, custom_url=custom_url)
    login_only = custom_url is not None and backend.has_login_worker
    backend.submit(
        url,
        meta={'page_num': page_num},
        entry_index=f'page-{page_num}',
        priority=page_num,
        login_only=login_only,
    )


def _check_stop_condition(
    results: Dict[int, EngineResult],
    start_page: int,
    max_consecutive_empty: int,
) -> bool:
    """Check if enough consecutive empty/failed pages have been seen
    (in page-number order) to justify stopping."""
    consecutive = 0
    page = start_page
    while page in results:
        r = results[page]
        if r.success and r.data and r.data.get('has_movie_list'):
            consecutive = 0
        elif r.success and r.data and r.data.get('is_valid_empty'):
            return True
        else:
            consecutive += 1
            if consecutive >= max_consecutive_empty:
                return True
        page += 1
    return False
