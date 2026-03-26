"""Parallel detail-page processing backed by FetchEngine."""

from dataclasses import dataclass, field
from typing import List

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_core.parser import parse_detail

from packages.python.javdb_spider.detail.runner import (
    process_detail_entries,
)
from packages.python.javdb_spider.fetch.backend import FetchRuntimeState
from packages.python.javdb_spider.fetch.fetch_engine import (
    EngineTask,
    ParallelFetchBackend,
)
from packages.python.javdb_spider.runtime.sleep import (
    penalty_tracker as _shared_penalty_tracker,
    dual_window_throttle as _shared_throttle,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Legacy data structures (kept for test compatibility)
# ---------------------------------------------------------------------------


@dataclass
class DetailTask:
    """A detail page to be fetched by a worker thread."""
    url: str
    entry: dict
    phase: int
    entry_index: str
    retry_count: int = 0
    failed_proxies: set = field(default_factory=set)


# ---------------------------------------------------------------------------
# Parse callback for FetchEngine.simple
# ---------------------------------------------------------------------------


def _spider_parse_fn(html: str, task: EngineTask):
    """Call ``parse_detail`` on raw HTML.

    Returns a dict with parsed fields on success, ``None`` on failure so the
    engine re-queues the task to another proxy.
    """
    magnets, actor_info, actor_gender, actor_link, supporting, ok = (
        parse_detail(html, task.entry_index, skip_sleep=True)
    )
    if not ok:
        return None
    return {
        'magnets': magnets,
        'actor_info': actor_info,
        'actor_gender': actor_gender or '',
        'actor_link': actor_link or '',
        'supporting': supporting or '',
    }


def build_parallel_detail_backend(
    *,
    use_cookie: bool,
    use_proxy: bool = True,
    use_cf_bypass: bool = False,
) -> ParallelFetchBackend:
    """Build the spider detail backend for parallel execution."""

    return ParallelFetchBackend.simple(
        parse_fn=_spider_parse_fn,
        use_cookie=use_cookie,
        penalty_tracker=_shared_penalty_tracker,
        throttle=_shared_throttle,
        runtime_state=FetchRuntimeState(
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
        ),
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def process_detail_entries_parallel(
    entries: List[dict],
    phase: int,
    history_data: dict,
    history_file: str,
    csv_path: str,
    fieldnames: list,
    dry_run: bool,
    use_history_for_saving: bool,
    use_cookie: bool,
    is_adhoc_mode: bool,
    rclone_inventory: dict = None,
    rclone_filter: bool = True,
    enable_dedup: bool = False,
    dedup_csv_path: str = '',
    enable_redownload: bool = False,
    redownload_threshold: float = 0.30,
) -> dict:
    """Process detail entries in parallel using one worker per proxy.

    Returns a dict with statistics keys:
        rows, skipped_history, failed, failed_movies, no_new_torrents
    """
    backend = build_parallel_detail_backend(
        use_cookie=use_cookie,
    )
    return process_detail_entries(
        backend=backend,
        entries=entries,
        phase=phase,
        history_data=history_data,
        history_file=history_file,
        csv_path=csv_path,
        fieldnames=fieldnames,
        dry_run=dry_run,
        use_history_for_saving=use_history_for_saving,
        is_adhoc_mode=is_adhoc_mode,
        rclone_inventory=rclone_inventory,
        rclone_filter=rclone_filter,
        enable_dedup=enable_dedup,
        dedup_csv_path=dedup_csv_path,
        enable_redownload=enable_redownload,
        redownload_threshold=redownload_threshold,
        include_recent_release_filters=True,
    )
