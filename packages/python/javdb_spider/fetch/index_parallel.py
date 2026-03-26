"""Parallel index-page fetching backed by the shared FetchEngine.

Submits index-page URLs to a ``ParallelFetchBackend``, collects results,
sorts them by page number, and applies ``parse_index`` exactly as the
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
from typing import Dict, List, Optional

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_core.parser import parse_index
from packages.python.javdb_core.url_helper import detect_url_type
from packages.python.javdb_core.filename_helper import generate_output_csv_name_from_html

from packages.python.javdb_spider.fetch.fallback import (
    get_page_url,
    validate_index_html,
)
from packages.python.javdb_spider.fetch.backend import FetchRuntimeState
from packages.python.javdb_spider.fetch.fetch_engine import (
    EngineTask,
    EngineResult,
    ParallelFetchBackend,
)
from packages.python.javdb_spider.runtime.config import PROXY_POOL

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Parse callback for FetchEngine.simple
# ---------------------------------------------------------------------------

def _index_parse_fn(html: str, task: EngineTask) -> Optional[dict]:
    """Validate an index page and return the result dict.

    Returns ``None`` when validation fails (page is a login wall, CF
    challenge, etc.) so the engine re-queues the task to another proxy.
    """
    page_num = task.meta.get('page_num', 0)
    result_html, has_movie_list, is_valid_empty = validate_index_html(
        html, page_num=page_num, context_msg=f'parallel index page {page_num}',
    )
    if result_html is None:
        return None
    return {
        'html': html,
        'has_movie_list': has_movie_list,
        'is_valid_empty': is_valid_empty,
    }


# ---------------------------------------------------------------------------
# Backend builder
# ---------------------------------------------------------------------------

def build_parallel_index_backend(
    *,
    use_cookie: bool,
    use_proxy: bool = True,
    use_cf_bypass: bool = False,
) -> ParallelFetchBackend:
    """Build an index-page parallel backend."""
    return ParallelFetchBackend.simple(
        parse_fn=_index_parse_fn,
        use_cookie=use_cookie,
        runtime_state=FetchRuntimeState(
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
        ),
    )


# ---------------------------------------------------------------------------
# Parallel orchestration
# ---------------------------------------------------------------------------

def fetch_all_index_pages_parallel(
    *,
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
) -> dict:
    """Fetch index pages in parallel and return the same dict as the
    sequential ``fetch_all_index_pages``.

    Returns dict with keys:
        all_index_results_phase1, all_index_results_phase2,
        any_proxy_banned, use_proxy, use_cf_bypass, csv_path,
        last_valid_page
    """

    backend = build_parallel_index_backend(
        use_cookie=custom_url is not None,
        use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
    )
    backend.start()

    # -- submit tasks -------------------------------------------------------

    if parse_all:
        window_size = max(len(PROXY_POOL) * 2, 4) if PROXY_POOL else 4
        next_page = start_page
        in_flight = 0
        for _ in range(window_size):
            _submit_page(backend, next_page, custom_url)
            next_page += 1
            in_flight += 1
    else:
        for p in range(start_page, end_page + 1):
            _submit_page(backend, p, custom_url)
        backend.mark_done()

    # -- collect results (may arrive out of order) --------------------------

    results_by_page: Dict[int, EngineResult] = {}
    any_proxy_banned = False
    csv_name_resolved = False
    all_index_results_phase1: List[dict] = []
    all_index_results_phase2: List[dict] = []
    last_valid_page = 0
    stop_collecting = False

    for result in backend.results():
        page_num = result.task.meta.get('page_num', 0)
        results_by_page[page_num] = result

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

        # -- sliding window: advance or stop --------------------------------

        if parse_all and not stop_collecting:
            should_stop = _check_stop_condition(
                results_by_page, start_page, max_consecutive_empty,
            )
            if should_stop:
                stop_collecting = True
                backend.mark_done()
            else:
                in_flight -= 1
                while in_flight < window_size:
                    _submit_page(backend, next_page, custom_url)
                    next_page += 1
                    in_flight += 1

    # -- shutdown engine and export login state -----------------------------

    backend.shutdown()
    backend.export_login_state()

    # -- process results in page order --------------------------------------

    sorted_pages = sorted(results_by_page.keys())
    consecutive_empty = 0

    for page_num in sorted_pages:
        result = results_by_page[page_num]

        if not result.success or not result.data:
            logger.info("[Page %d] Failed or empty (parallel)", page_num)
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

        if phase_mode in ['1', 'all']:
            page_results = parse_index(
                html, page_num, phase=1,
                disable_new_releases_filter=(custom_url is not None or ignore_release_date),
                is_adhoc_mode=(custom_url is not None),
            )
            p1_count = len(page_results)
            if p1_count > 0:
                all_index_results_phase1.extend(page_results)

        if phase_mode in ['2', 'all']:
            page_results_p2 = parse_index(
                html, page_num, phase=2,
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

    logger.info(
        "Fetched and parsed %d pages (parallel)",
        last_valid_page - start_page + 1 if last_valid_page >= start_page else 0,
    )

    return {
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
    url = get_page_url(page_num, custom_url=custom_url)
    backend.submit(
        url,
        meta={'page_num': page_num},
        entry_index=f'page-{page_num}',
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
