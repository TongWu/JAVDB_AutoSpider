"""Sequential (single-threaded) detail-page processing."""

from typing import List

from packages.python.javdb_spider.detail.runner import (
    process_detail_entries,
)
from packages.python.javdb_spider.fetch.sequential_backend import SequentialFetchBackend


def build_sequential_detail_backend(
    session,
    *,
    use_cookie: bool,
    is_adhoc_mode: bool,
    use_proxy: bool,
    use_cf_bypass: bool,
) -> SequentialFetchBackend:
    """Build the spider detail backend for sequential execution."""

    return SequentialFetchBackend(
        session,
        use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
        use_cookie=use_cookie,
        is_adhoc_mode=is_adhoc_mode,
    )


def process_phase_entries_sequential(
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
    session,
    use_proxy: bool,
    use_cf_bypass: bool,
    rclone_inventory: dict = None,
    rclone_filter: bool = True,
    enable_dedup: bool = False,
    dedup_csv_path: str = '',
    enable_redownload: bool = False,
    redownload_threshold: float = 0.30,
) -> dict:
    """Process detail entries sequentially (single-threaded mode).

    Returns a dict with keys:
        rows, skipped_history, failed, no_new_torrents,
        failed_movies, use_proxy, use_cf_bypass
    """
    backend = build_sequential_detail_backend(
        session,
        use_cookie=use_cookie,
        is_adhoc_mode=is_adhoc_mode,
        use_proxy=use_proxy,
        use_cf_bypass=use_cf_bypass,
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
        include_recent_release_filters=False,
        log_duplicate_skips=True,
    )
