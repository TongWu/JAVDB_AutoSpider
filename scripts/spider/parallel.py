"""Parallel detail-page processing backed by FetchEngine."""

from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin

from utils.logging_config import get_logger
from utils.config_helper import use_sqlite
from utils.parser import parse_detail
from utils.magnet_extractor import extract_magnets
from utils.db import db_batch_update_movie_actors
from utils.history_manager import (
    has_complete_subtitles, should_skip_recent_yesterday_release,
    should_skip_recent_today_release, should_process_movie,
    save_parsed_movie_to_history, batch_update_last_visited,
    check_redownload_upgrade,
)
from utils.csv_writer import write_csv

import scripts.spider.state as state
from scripts.spider.engine import FetchEngine, EngineTask
from scripts.spider.sleep_manager import (
    penalty_tracker as _shared_penalty_tracker,
    dual_window_throttle as _shared_throttle,
)
from scripts.spider.csv_builder import (
    create_csv_row_with_history_filter, check_torrent_status, collect_new_magnet_links,
    create_redownload_row,
)
from scripts.spider.config_loader import BASE_URL
from scripts.spider.dedup_checker import (
    should_skip_from_rclone,
    check_dedup_upgrade,
    append_dedup_record,
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

    tasks_submitted = 0
    local_parsed_links: set = set()

    for i, entry in enumerate(entries, 1):
        href = entry['href']
        if href in state.parsed_links or href in local_parsed_links:
            continue
        local_parsed_links.add(href)

        if has_complete_subtitles(href, history_data):
            skip_complete = True
            if enable_redownload and not is_adhoc_mode:
                is_today = entry.get('is_today_release', False)
                is_yesterday = entry.get('is_yesterday_release', False)
                if not (should_skip_recent_today_release(href, history_data, is_today)
                        or should_skip_recent_yesterday_release(href, history_data, is_yesterday)):
                    skip_complete = False
                    logger.debug(
                        f"[{i}/{total_entries}] [Page {entry['page']}] "
                        f"{entry['video_code']} has complete subtitles but re-download check enabled"
                    )
            if skip_complete:
                logger.info(
                    f"[{i}/{total_entries}] [Page {entry['page']}] "
                    f"Skipping {entry['video_code']} — already has subtitle and hacked_subtitle in history"
                )
                continue

        if rclone_filter and rclone_inventory and should_skip_from_rclone(entry.get('video_code', ''), rclone_inventory, enable_dedup):
            logger.info(
                f"[{i}/{total_entries}] [Page {entry['page']}] "
                f"Skipping {entry['video_code']} — already exists in rclone inventory with 中字"
            )
            continue

        if not is_adhoc_mode and should_skip_recent_yesterday_release(
            href, history_data, entry.get('is_yesterday_release', False)
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {entry['page']}] "
                f"Skipping {entry['video_code']} — yesterday release, recently updated in history"
            )
            continue

        if not is_adhoc_mode and should_skip_recent_today_release(
            href, history_data, entry.get('is_today_release', False)
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {entry['page']}] "
                f"Skipping {entry['video_code']} — today release, already visited today"
            )
            continue

        detail_url = urljoin(BASE_URL, href)
        entry_index = f"{i}/{total_entries}"
        logger.debug(f"[{entry_index}] [Page {entry['page']}] Queued {entry['video_code'] or href}")
        engine.submit(
            detail_url,
            entry_index=entry_index,
            meta={'entry': entry, 'phase': phase, 'video_code': entry.get('video_code', '')},
        )
        tasks_submitted += 1

    state.parsed_links.update(local_parsed_links)

    skipped_history = len(local_parsed_links) - tasks_submitted

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

    rows: list = []
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
        visited_hrefs.add(href)
        actor_updates.append((
            href, data['actor_info'], data['actor_gender'],
            data['actor_link'], data['supporting'],
        ))
        magnet_links = extract_magnets(data['magnets'], idx_str)

        should_process, history_torrent_types = should_process_movie(
            href, history_data, phase, magnet_links,
        )

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
                skipped_history += 1
                continue

        if enable_dedup and rclone_inventory and entry.get('video_code'):
            vc = entry['video_code'].upper()
            rclone_entries = rclone_inventory.get(vc, [])
            if rclone_entries:
                torrent_types = {
                    'subtitle': bool(magnet_links.get('subtitle')),
                    'hacked_subtitle': bool(magnet_links.get('hacked_subtitle')),
                    'hacked_no_subtitle': bool(magnet_links.get('hacked_no_subtitle')),
                    'no_subtitle': bool(magnet_links.get('no_subtitle')),
                }
                dedup_records = check_dedup_upgrade(vc, torrent_types, rclone_entries)
                for rec in dedup_records:
                    if not dry_run and dedup_csv_path:
                        append_dedup_record(dedup_csv_path, rec)
                    logger.info(f"[{idx_str}] DEDUP: {rec.video_code} – {rec.deletion_reason}")

        if redownload_cats:
            row = create_redownload_row(
                href, entry, page_num, data['actor_info'], magnet_links, redownload_cats)
        else:
            row = create_csv_row_with_history_filter(
                href, entry, page_num, data['actor_info'], magnet_links, history_data)
        row['video_code'] = entry['video_code']

        _has_any, has_new_torrents, should_include_in_report = check_torrent_status(row)

        if should_include_in_report:
            write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
            rows.append(row)
            phase_rows.append(row)

            if use_history_for_saving and not dry_run and has_new_torrents:
                new_magnet_links, new_sizes, new_fc, new_res = collect_new_magnet_links(row, magnet_links)
                if new_magnet_links:
                    save_parsed_movie_to_history(
                        history_file, href, phase, entry['video_code'],
                        new_magnet_links, size_links=new_sizes,
                        file_count_links=new_fc, resolution_links=new_res,
                        actor_name=data['actor_info'],
                        actor_gender=data['actor_gender'],
                        actor_link=data['actor_link'],
                        supporting_actors=data['supporting'],
                    )
        else:
            no_new_torrents += 1

    engine.shutdown()

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
    }
