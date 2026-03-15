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
    check_redownload_upgrade,
    should_skip_recent_today_release,
    should_skip_recent_yesterday_release,
)
from utils.csv_writer import write_csv

import scripts.spider.state as state
from scripts.spider.fallback import fetch_detail_page_with_fallback
from scripts.spider.sleep_manager import movie_sleep_mgr
from scripts.spider.csv_builder import (
    create_csv_row_with_history_filter, check_torrent_status, collect_new_magnet_links,
    create_redownload_row,
)
from scripts.spider.config_loader import BASE_URL, FALLBACK_COOLDOWN
from scripts.spider.dedup_checker import (
    should_skip_from_rclone,
    check_dedup_upgrade,
    append_dedup_record,
)

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
    skipped_history = 0
    failed = 0
    failed_movies: list = []
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
            skip_complete = True
            if enable_redownload and not is_adhoc_mode:
                is_today = entry.get('is_today_release', False)
                is_yesterday = entry.get('is_yesterday_release', False)
                if not (should_skip_recent_today_release(href, history_data, is_today)
                        or should_skip_recent_yesterday_release(href, history_data, is_yesterday)):
                    skip_complete = False
                    logger.debug(
                        f"[{i}/{total_entries}] [Page {page_num}] "
                        f"{entry['video_code']} has complete subtitles but re-download check enabled"
                    )
            if skip_complete:
                logger.info(
                    f"[{i}/{total_entries}] [Page {page_num}] "
                    f"Skipping {entry['video_code']} - already has subtitle and hacked_subtitle in history"
                )
                skipped_history += 1
                continue

        if rclone_filter and rclone_inventory and should_skip_from_rclone(entry.get('video_code', ''), rclone_inventory, enable_dedup):
            logger.info(
                f"[{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - already exists in rclone inventory with 中字"
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

        if not parse_success:
            logger.error(f"[{entry_index}] [Page {page_num}] Failed: {entry.get('video_code', '?')} ({detail_url})")
            failed += 1
            failed_movies.append({'video_code': entry.get('video_code', '?'), 'url': detail_url, 'phase': phase})
            pending_movie_sleep = True
            continue

        visited_hrefs.add(href)
        magnet_links = extract_magnets(magnets, entry_index)

        should_process, history_torrent_types = should_process_movie(href, history_data, phase, magnet_links)

        redownload_cats = []
        if not should_process:
            if enable_redownload and not is_adhoc_mode:
                is_today = entry.get('is_today_release', False)
                is_yesterday = entry.get('is_yesterday_release', False)
                if not (should_skip_recent_today_release(href, history_data, is_today)
                        or should_skip_recent_yesterday_release(href, history_data, is_yesterday)):
                    redownload_cats = check_redownload_upgrade(
                        href, history_data, magnet_links, redownload_threshold,
                    )
            if not redownload_cats:
                if history_torrent_types and 'hacked_subtitle' in history_torrent_types and 'subtitle' in history_torrent_types:
                    logger.debug(f"[{entry_index}] [Page {page_num}] Skipping based on history rules (history types: {history_torrent_types})")
                else:
                    logger.debug(f"[{entry_index}] [Page {page_num}] Should process, missing preferred types: {get_missing_torrent_types(history_torrent_types, [])}")
                skipped_history += 1
                pending_movie_sleep = True
                continue

        # Dedup upgrade detection against rclone inventory
        if enable_dedup and rclone_inventory and entry.get('video_code'):
            vc = entry['video_code'].upper()
            rclone_entries = rclone_inventory.get(vc, [])
            if rclone_entries:
                torrent_types = {
                    'subtitle': any(m.get('type') == 'subtitle' for m in magnet_links),
                    'hacked_subtitle': any(m.get('type') == 'hacked_subtitle' for m in magnet_links),
                    'hacked_no_subtitle': any(m.get('type') == 'hacked_no_subtitle' for m in magnet_links),
                    'no_subtitle': any(m.get('type') == 'no_subtitle' for m in magnet_links),
                }
                dedup_records = check_dedup_upgrade(vc, torrent_types, rclone_entries)
                for rec in dedup_records:
                    if not dry_run and dedup_csv_path:
                        append_dedup_record(dedup_csv_path, rec)
                    logger.info(f"[{entry_index}] DEDUP: {rec.video_code} – {rec.deletion_reason}")

        if redownload_cats:
            row = create_redownload_row(href, entry, page_num, actor_info, magnet_links, redownload_cats)
        else:
            row = create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links, history_data)
        row['video_code'] = entry['video_code']

        _has_any, has_new_torrents, should_include_in_report = check_torrent_status(row)

        if should_include_in_report:
            write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
            phase_rows.append(row)

            if use_history_for_saving and not dry_run and has_new_torrents:
                new_magnet_links, new_sizes, new_fc, new_res = collect_new_magnet_links(row, magnet_links)
                if new_magnet_links:
                    save_parsed_movie_to_history(
                        history_file, href, phase, entry['video_code'],
                        new_magnet_links, size_links=new_sizes,
                        file_count_links=new_fc, resolution_links=new_res,
                    )
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
        'failed_movies': failed_movies,
        'no_new_torrents': no_new_torrents,
        'use_proxy': use_proxy,
        'use_cf_bypass': use_cf_bypass,
    }
