"""Sequential (single-threaded) detail-page processing."""

import time
from typing import List
from urllib.parse import urljoin

from utils.logging_config import get_logger
from utils.config_helper import use_sqlite
from utils.magnet_extractor import extract_magnets
from utils.db import db_batch_update_movie_actors
from scripts.ingestion.models import ParsedMovie
from scripts.ingestion.planner import build_spider_ingestion_plan
from scripts.ingestion.policies import (
    has_complete_subtitles,
    should_skip_recent_today_release,
    should_skip_recent_yesterday_release,
)
from utils.history_manager import (
    save_parsed_movie_to_history,
    batch_update_last_visited,
)
from utils.csv_writer import write_csv

import scripts.spider.state as state
from scripts.spider.fallback import fetch_detail_page_with_fallback
from scripts.spider.sleep_manager import movie_sleep_mgr
from scripts.spider.config_loader import BASE_URL, FALLBACK_COOLDOWN
from scripts.spider.dedup_checker import (
    should_skip_from_rclone,
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
    actor_updates: list = []
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

        visited_hrefs.add(href)
        actor_updates.append((
            href, actor_info or '', actor_gender or '', actor_link or '',
            supporting_actors or '',
        ))
        magnet_links = extract_magnets(magnets, entry_index)
        parsed_movie = ParsedMovie(
            href=href,
            video_code=entry['video_code'],
            page_num=page_num,
            actor_name=actor_info or '',
            actor_gender=actor_gender or '',
            actor_link=actor_link or '',
            supporting_actors=supporting_actors or '',
            magnet_links=magnet_links,
            entry=entry,
        )
        rclone_entries = []
        if rclone_inventory and entry.get('video_code'):
            rclone_entries = rclone_inventory.get(entry['video_code'].upper(), [])

        plan = build_spider_ingestion_plan(
            parsed_movie,
            history_data=history_data,
            phase=phase,
            rclone_entries=rclone_entries,
            enable_dedup=enable_dedup,
            enable_redownload=enable_redownload and not is_adhoc_mode,
            redownload_threshold=redownload_threshold,
        )

        if plan.should_skip:
            logger.debug(
                f"[{entry_index}] [Page {page_num}] Skipping based on ingestion plan: {plan.skip_reason}"
            )
            skipped_history += 1
            pending_movie_sleep = True
            continue

        for rec in plan.dedup_records:
            if not dry_run and dedup_csv_path:
                append_dedup_record(dedup_csv_path, rec)
            logger.info(f"[{entry_index}] DEDUP: {rec.video_code} – {rec.deletion_reason}")

        row = plan.report_row
        if row is None:
            no_new_torrents += 1
            continue

        if plan.should_include_in_report:
            write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
            phase_rows.append(row)

            if use_history_for_saving and not dry_run and plan.has_new_torrents and plan.new_magnet_links:
                save_parsed_movie_to_history(
                    history_file, href, phase, entry['video_code'],
                    plan.new_magnet_links, size_links=plan.new_sizes,
                    file_count_links=plan.new_file_counts, resolution_links=plan.new_resolutions,
                    actor_name=actor_info or '',
                    actor_gender=actor_gender or '',
                    actor_link=actor_link or '',
                    supporting_actors=supporting_actors or '',
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
        if use_sqlite() and actor_updates:
            db_batch_update_movie_actors(actor_updates)
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
