"""Parallel detail-page processing backed by FetchEngine."""

from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin

from utils.infra.logging_config import get_logger
from utils.parser import parse_detail
from utils.domain.magnet_extractor import extract_magnets

from scripts.spider.detail.runner import (
    finalize_detail_phase,
    persist_parsed_detail_result,
    prepare_detail_entries,
)
from scripts.spider.fetch.fetch_engine import FetchEngine, EngineTask
from scripts.spider.runtime.sleep import (
    penalty_tracker as _shared_penalty_tracker,
    dual_window_throttle as _shared_throttle,
)
from scripts.spider.runtime.config import BASE_URL

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
    ban_log_file: str,
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
    total_entries = len(entries)

    # -- filter entries and submit to engine ---------------------------------

    engine = FetchEngine.simple(
        parse_fn=_spider_parse_fn,
        use_cookie=use_cookie,
        ban_log_file=ban_log_file,
        penalty_tracker=_shared_penalty_tracker,
        throttle=_shared_throttle,
    )

    prepared_entries, skipped_history = prepare_detail_entries(
        entries,
        history_data=history_data,
        is_adhoc_mode=is_adhoc_mode,
        rclone_inventory=rclone_inventory,
        rclone_filter=rclone_filter,
        enable_dedup=enable_dedup,
        enable_redownload=enable_redownload,
        include_recent_release_filters=True,
    )

    tasks_submitted = 0

    for candidate in prepared_entries:
        detail_url = urljoin(BASE_URL, candidate.href)
        logger.debug(
            f"[{candidate.entry_index}] [Page {candidate.page_num}] "
            f"Queued {candidate.entry.get('video_code') or candidate.href}"
        )
        engine.submit(
            detail_url,
            entry_index=candidate.entry_index,
            meta={
                'entry': candidate.entry,
                'phase': phase,
                'video_code': candidate.entry.get('video_code', ''),
            },
        )
        tasks_submitted += 1

    if tasks_submitted == 0:
        logger.info(f"Phase {phase}: No detail tasks to process (all filtered)")
        return {'rows': [], 'skipped_history': skipped_history, 'failed': 0, 'failed_movies': [], 'no_new_torrents': 0}

    # -- start engine and consume results ------------------------------------

    engine.start()
    engine.mark_done()

    logger.info(
        f"Phase {phase}: Started {len(engine._workers)} workers for {tasks_submitted} detail tasks "
        f"({skipped_history} skipped by history)"
    )

    phase_rows: list = []
    visited_hrefs: set = set()
    actor_updates: List[tuple] = []
    failed = 0
    failed_movies: list = []
    no_new_torrents = 0

    for result in engine.results():
        entry = result.task.meta['entry']
        href = entry['href']
        page_num = entry['page']
        idx_str = result.task.entry_index

        if not result.success:
            detail_url = urljoin(BASE_URL, href)
            logger.error(f"[{idx_str}] [Page {page_num}] Failed: {entry.get('video_code', '?')} ({detail_url})")
            failed += 1
            failed_movies.append({'video_code': entry.get('video_code', '?'), 'url': detail_url, 'phase': phase})
            continue

        cf_tag = " +CF" if result.used_cf else ""
        logger.info(
            f"[{idx_str}] Parsed {entry.get('video_code', '')}{cf_tag}"
        )

        data = result.data
        magnet_links = extract_magnets(data['magnets'], idx_str)
        outcome = persist_parsed_detail_result(
            entry=entry,
            phase=phase,
            entry_index=idx_str,
            history_data=history_data,
            history_file=history_file,
            csv_path=csv_path,
            fieldnames=fieldnames,
            dry_run=dry_run,
            use_history_for_saving=use_history_for_saving,
            is_adhoc_mode=is_adhoc_mode,
            rclone_inventory=rclone_inventory,
            enable_dedup=enable_dedup,
            dedup_csv_path=dedup_csv_path,
            enable_redownload=enable_redownload,
            redownload_threshold=redownload_threshold,
            actor_info=data['actor_info'],
            actor_gender=data['actor_gender'],
            actor_link=data['actor_link'],
            supporting_actors=data['supporting'],
            magnet_links=magnet_links,
        )
        skipped_history += outcome.skipped_history
        no_new_torrents += outcome.no_new_torrents
        if outcome.visited_href:
            visited_hrefs.add(outcome.visited_href)
        if outcome.actor_update:
            actor_updates.append(outcome.actor_update)

        if outcome.row is not None:
            phase_rows.append(outcome.row)

    engine.shutdown()

    finalize_detail_phase(
        use_history_for_saving=use_history_for_saving,
        dry_run=dry_run,
        history_file=history_file,
        visited_hrefs=visited_hrefs,
        actor_updates=actor_updates,
    )

    logger.info(
        f"Phase {phase} completed: {total_entries} movies discovered, "
        f"{len(phase_rows)} processed, {skipped_history} skipped (history), "
        f"{no_new_torrents} no new torrents, {failed} failed"
    )
    return {
        'rows': phase_rows,
        'skipped_history': skipped_history,
        'failed': failed,
        'failed_movies': failed_movies,
        'no_new_torrents': no_new_torrents,
    }
