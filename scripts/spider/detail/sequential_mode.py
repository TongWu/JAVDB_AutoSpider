"""Sequential (single-threaded) detail-page processing."""

import time
from typing import List
from urllib.parse import urljoin

from utils.infra.logging_config import get_logger
from utils.domain.magnet_extractor import extract_magnets

from scripts.spider.detail.runner import (
    finalize_detail_phase,
    persist_parsed_detail_result,
    prepare_detail_entries,
)
from scripts.spider.fetch.fallback import fetch_detail_page_with_fallback
from scripts.spider.runtime.sleep import movie_sleep_mgr
from scripts.spider.runtime.config import BASE_URL, FALLBACK_COOLDOWN

logger = get_logger(__name__)


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
    total_entries = len(entries)
    phase_rows: list = []
    visited_hrefs: set = set()
    actor_updates: list = []
    skipped_history = 0
    failed = 0
    failed_movies: list = []
    no_new_torrents = 0
    pending_movie_sleep = False

    prepared_entries, skipped_history = prepare_detail_entries(
        entries,
        history_data=history_data,
        is_adhoc_mode=is_adhoc_mode,
        rclone_inventory=rclone_inventory,
        rclone_filter=rclone_filter,
        enable_dedup=enable_dedup,
        enable_redownload=enable_redownload,
        include_recent_release_filters=False,
        log_duplicate_skips=True,
    )

    for candidate in prepared_entries:
        entry = candidate.entry
        href = candidate.href
        page_num = candidate.page_num
        entry_index = candidate.entry_index

        if pending_movie_sleep:
            movie_sleep_mgr.sleep()
            pending_movie_sleep = False

        detail_url = urljoin(BASE_URL, href)
        logger.info(f"[{entry_index}] [Page {page_num}] Processing {entry['video_code'] or href}")

        (
            magnets, actor_info, actor_gender, actor_link, supporting_actors, parse_success,
            effective_use_proxy, effective_use_cf_bypass,
        ) = fetch_detail_page_with_fallback(
            detail_url, session,
            use_cookie=use_cookie,
            use_proxy=use_proxy,
            use_cf_bypass=use_cf_bypass,
            entry_index=entry_index,
            is_adhoc_mode=is_adhoc_mode,
        )

        fallback_triggered = parse_success and (effective_use_proxy != use_proxy or effective_use_cf_bypass != use_cf_bypass)
        if parse_success and effective_use_cf_bypass != use_cf_bypass:
            use_cf_bypass = effective_use_cf_bypass
        if parse_success and effective_use_proxy != use_proxy:
            use_proxy = effective_use_proxy

        if not parse_success:
            logger.error(f"[{entry_index}] [Page {page_num}] Failed: {entry.get('video_code', '?')} ({detail_url})")
            failed += 1
            failed_movies.append({'video_code': entry.get('video_code', '?'), 'url': detail_url, 'phase': phase})
            pending_movie_sleep = True
            continue

        magnet_links = extract_magnets(magnets, entry_index)
        outcome = persist_parsed_detail_result(
            entry=entry,
            phase=phase,
            entry_index=entry_index,
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
            actor_info=actor_info or '',
            actor_gender=actor_gender or '',
            actor_link=actor_link or '',
            supporting_actors=supporting_actors or '',
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

        if outcome.status == 'skipped':
            pending_movie_sleep = True
            continue
        if outcome.status == 'no_row':
            continue

        if fallback_triggered:
            logger.debug(f"[{entry_index}] Applying fallback cooldown: {FALLBACK_COOLDOWN}s")
            time.sleep(FALLBACK_COOLDOWN)
            pending_movie_sleep = False
        else:
            pending_movie_sleep = True

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
        'use_proxy': use_proxy,
        'use_cf_bypass': use_cf_bypass,
    }
