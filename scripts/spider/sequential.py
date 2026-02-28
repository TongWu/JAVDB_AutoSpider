"""Sequential (single-threaded) detail-page processing."""

import time
from typing import List
from urllib.parse import urljoin

from utils.logging_config import get_logger
from utils.magnet_extractor import extract_magnets
from utils.history_manager import (
    has_complete_subtitles, should_process_movie,
    get_missing_torrent_types, save_parsed_movie_to_history,
    batch_update_last_visited,
)
from utils.csv_writer import write_csv

import scripts.spider.state as state
from scripts.spider.fallback import fetch_detail_page_with_fallback
from scripts.spider.sleep_manager import movie_sleep_mgr
from scripts.spider.csv_builder import (
    create_csv_row_with_history_filter, check_torrent_status, collect_new_magnet_links,
)
from scripts.spider.config_loader import BASE_URL, FALLBACK_COOLDOWN

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
) -> dict:
    """Process detail entries sequentially (single-threaded mode).

    Returns a dict with keys:
        rows, skipped_history, failed, no_new_torrents,
        use_proxy, use_cf_bypass
    """
    total_entries = len(entries)
    phase_rows: list = []
    visited_hrefs: set = set()
    skipped_history = 0
    failed = 0
    no_new_torrents = 0
    pending_movie_sleep = False

    for i, entry in enumerate(entries, 1):
        href = entry['href']
        page_num = entry['page']

        if href in state.parsed_links:
            logger.info(f"[{i}/{total_entries}] [Page {page_num}] Skipping duplicate entry in current run")
            continue

        state.parsed_links.add(href)

        if has_complete_subtitles(href, history_data):
            logger.info(
                f"[{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - already has subtitle and hacked_subtitle in history"
            )
            skipped_history += 1
            continue

        if pending_movie_sleep:
            movie_sleep_mgr.sleep()
            pending_movie_sleep = False

        detail_url = urljoin(BASE_URL, href)
        entry_index = f"{i}/{total_entries}"
        logger.info(f"[{entry_index}] [Page {page_num}] Processing {entry['video_code'] or href}")

        magnets, actor_info, parse_success, effective_use_proxy, effective_use_cf_bypass = fetch_detail_page_with_fallback(
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

        if not parse_success and not magnets:
            logger.error(f"[{entry_index}] [Page {page_num}] Failed to fetch/parse detail page after all fallback attempts")
            failed += 1
            pending_movie_sleep = True
            continue

        visited_hrefs.add(href)
        magnet_links = extract_magnets(magnets, entry_index)

        should_process, history_torrent_types = should_process_movie(href, history_data, phase, magnet_links)

        if not should_process:
            if history_torrent_types and 'hacked_subtitle' in history_torrent_types and 'subtitle' in history_torrent_types:
                logger.debug(f"[{entry_index}] [Page {page_num}] Skipping based on history rules (history types: {history_torrent_types})")
            else:
                logger.debug(f"[{entry_index}] [Page {page_num}] Should process, missing preferred types: {get_missing_torrent_types(history_torrent_types, [])}")
            skipped_history += 1
            pending_movie_sleep = True
            continue

        row = create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links, history_data)
        row['video_code'] = entry['video_code']

        _has_any, has_new_torrents, should_include_in_report = check_torrent_status(row)

        if should_include_in_report:
            write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
            phase_rows.append(row)

            if use_history_for_saving and not dry_run and has_new_torrents:
                new_magnet_links = collect_new_magnet_links(row, magnet_links)
                if new_magnet_links:
                    save_parsed_movie_to_history(history_file, href, phase, entry['video_code'], new_magnet_links)
        else:
            no_new_torrents += 1

        if fallback_triggered:
            logger.debug(f"[{entry_index}] Applying fallback cooldown: {FALLBACK_COOLDOWN}s")
            time.sleep(FALLBACK_COOLDOWN)
            pending_movie_sleep = False
        else:
            pending_movie_sleep = True

    if use_history_for_saving and not dry_run and visited_hrefs:
        batch_update_last_visited(history_file, visited_hrefs)

    logger.info(
        f"Phase {phase} completed: {total_entries} movies discovered, "
        f"{len(phase_rows)} processed, {skipped_history} skipped (history), "
        f"{no_new_torrents} no new torrents, {failed} failed"
    )
    return {
        'rows': phase_rows,
        'skipped_history': skipped_history,
        'failed': failed,
        'no_new_torrents': no_new_torrents,
        'use_proxy': use_proxy,
        'use_cf_bypass': use_cf_bypass,
    }
