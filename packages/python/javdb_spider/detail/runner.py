"""Shared runner helpers for spider detail-page orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.config_helper import use_sqlite
from packages.python.javdb_platform.db import db_batch_update_movie_actors
from packages.python.javdb_platform.history_manager import (
    save_parsed_movie_to_history,
    batch_update_last_visited,
)
from packages.python.javdb_platform.csv_writer import write_csv
from packages.python.javdb_core.magnet_extractor import extract_magnets

import packages.python.javdb_spider.runtime.state as state
from packages.python.javdb_ingestion.models import ParsedMovie
from packages.python.javdb_ingestion.planner import build_spider_ingestion_plan
from packages.python.javdb_ingestion.policies import (
    has_complete_subtitles,
    should_skip_recent_today_release,
    should_skip_recent_yesterday_release,
)
from packages.python.javdb_spider.services.dedup import (
    should_skip_from_rclone,
    append_dedup_record,
)
from packages.python.javdb_spider.fetch.backend import FetchBackend
from packages.python.javdb_spider.fetch.fetch_engine import EngineTask
from packages.python.javdb_spider.runtime.config import BASE_URL

logger = get_logger(__name__)


@dataclass(frozen=True)
class DetailEntryCandidate:
    """A detail-page entry that passed pre-fetch filtering."""

    entry: dict
    href: str
    page_num: int
    entry_index: str


@dataclass
class DetailPersistOutcome:
    """Result of persisting one parsed detail page."""

    status: str
    skipped_history: int = 0
    no_new_torrents: int = 0
    row: Optional[dict] = None
    visited_href: Optional[str] = None
    actor_update: Optional[Tuple[str, str, str, str, str]] = None


def process_detail_entries(
    *,
    backend: FetchBackend,
    entries: List[dict],
    phase: int,
    history_data: dict,
    history_file: str,
    csv_path: str,
    fieldnames: list,
    dry_run: bool,
    use_history_for_saving: bool,
    is_adhoc_mode: bool,
    rclone_inventory: Optional[dict] = None,
    rclone_filter: bool = True,
    enable_dedup: bool = False,
    dedup_csv_path: str = '',
    enable_redownload: bool = False,
    redownload_threshold: float = 0.30,
    include_recent_release_filters: bool = False,
    log_duplicate_skips: bool = False,
) -> dict:
    """Run the shared detail pipeline against a concrete fetch backend."""

    total_entries = len(entries)

    prepared_entries, skipped_history = prepare_detail_entries(
        entries,
        history_data=history_data,
        is_adhoc_mode=is_adhoc_mode,
        rclone_inventory=rclone_inventory,
        rclone_filter=rclone_filter,
        enable_dedup=enable_dedup,
        enable_redownload=enable_redownload,
        include_recent_release_filters=include_recent_release_filters,
        log_duplicate_skips=log_duplicate_skips,
    )

    for candidate in prepared_entries:
        detail_url = urljoin(BASE_URL, candidate.href)
        logger.debug(
            f"[{candidate.entry_index}] [Page {candidate.page_num}] "
            f"Queued {candidate.entry.get('video_code') or candidate.href}"
        )
        backend.submit_task(
            EngineTask(
                url=detail_url,
                entry_index=candidate.entry_index,
                meta={
                    'entry': candidate.entry,
                    'phase': phase,
                    'video_code': candidate.entry.get('video_code', ''),
                },
            )
        )

    runtime_state = backend.runtime_state()
    if not prepared_entries:
        logger.info(f"Phase {phase}: No detail tasks to process (all filtered)")
        return {
            'rows': [],
            'skipped_history': skipped_history,
            'failed': 0,
            'failed_movies': [],
            'no_new_torrents': 0,
            'use_proxy': runtime_state.use_proxy,
            'use_cf_bypass': runtime_state.use_cf_bypass,
        }

    backend.start()
    backend.mark_done()

    logger.info(
        f"Phase {phase}: Started {backend.worker_count} workers for "
        f"{len(prepared_entries)} detail tasks ({skipped_history} skipped by history)"
    )

    phase_rows: list = []
    visited_hrefs: set = set()
    actor_updates: List[tuple] = []
    failed = 0
    failed_movies: list = []
    no_new_torrents = 0
    previous_runtime_state = runtime_state

    try:
        for result in backend.results():
            entry = result.task.meta['entry']
            href = entry['href']
            page_num = entry['page']
            idx_str = result.task.entry_index

            if not result.success:
                detail_url = urljoin(BASE_URL, href)
                logger.error(
                    f"[{idx_str}] [Page {page_num}] Failed: "
                    f"{entry.get('video_code', '?')} ({detail_url})"
                )
                failed += 1
                failed_movies.append(
                    {
                        'video_code': entry.get('video_code', '?'),
                        'url': detail_url,
                        'phase': phase,
                    }
                )
                current_runtime_state = backend.runtime_state()
                result.acknowledge(
                    'failed',
                    runtime_state_changed=(
                        current_runtime_state != previous_runtime_state
                    ),
                )
                previous_runtime_state = current_runtime_state
                continue

            cf_tag = " +CF" if result.used_cf else ""
            logger.info(f"[{idx_str}] Parsed {entry.get('video_code', '')}{cf_tag}")

            data = result.data or {}
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

            current_runtime_state = backend.runtime_state()
            result.acknowledge(
                outcome.status,
                runtime_state_changed=(
                    current_runtime_state != previous_runtime_state
                ),
            )
            previous_runtime_state = current_runtime_state
    finally:
        backend.shutdown()

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
    runtime_state = backend.runtime_state()
    return {
        'rows': phase_rows,
        'skipped_history': skipped_history,
        'failed': failed,
        'failed_movies': failed_movies,
        'no_new_torrents': no_new_torrents,
        'use_proxy': runtime_state.use_proxy,
        'use_cf_bypass': runtime_state.use_cf_bypass,
    }


def prepare_detail_entries(
    entries: List[dict],
    *,
    history_data: dict,
    is_adhoc_mode: bool,
    rclone_inventory: Optional[dict] = None,
    rclone_filter: bool = True,
    enable_dedup: bool = False,
    enable_redownload: bool = False,
    include_recent_release_filters: bool = False,
    log_duplicate_skips: bool = False,
) -> tuple[List[DetailEntryCandidate], int]:
    """Filter raw entries into detail-page candidates for fetching."""

    total_entries = len(entries)
    prepared: List[DetailEntryCandidate] = []
    local_parsed_links: set[str] = set()
    skipped_history = 0

    for i, entry in enumerate(entries, 1):
        href = entry['href']
        page_num = entry['page']

        if href in state.parsed_links or href in local_parsed_links:
            if log_duplicate_skips:
                logger.info(
                    f"[{i}/{total_entries}] [Page {page_num}] "
                    "Skipping duplicate entry in current run"
                )
            continue

        local_parsed_links.add(href)

        if has_complete_subtitles(href, history_data):
            skip_complete = True
            if enable_redownload and not is_adhoc_mode:
                is_today = entry.get('is_today_release', False)
                is_yesterday = entry.get('is_yesterday_release', False)
                if not (
                    should_skip_recent_today_release(href, history_data, is_today)
                    or should_skip_recent_yesterday_release(
                        href,
                        history_data,
                        is_yesterday,
                    )
                ):
                    skip_complete = False
                    logger.debug(
                        f"[{i}/{total_entries}] [Page {page_num}] "
                        f"{entry['video_code']} has complete subtitles but "
                        "re-download check enabled"
                    )
            if skip_complete:
                logger.info(
                    f"[{i}/{total_entries}] [Page {page_num}] "
                    f"Skipping {entry['video_code']} - already has subtitle "
                    "and hacked_subtitle in history"
                )
                skipped_history += 1
                continue

        if (
            rclone_filter
            and rclone_inventory
            and should_skip_from_rclone(
                entry.get('video_code', ''),
                rclone_inventory,
                enable_dedup,
            )
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - already exists in "
                "rclone inventory with 中字"
            )
            skipped_history += 1
            continue

        if (
            include_recent_release_filters
            and not is_adhoc_mode
            and should_skip_recent_yesterday_release(
                href,
                history_data,
                entry.get('is_yesterday_release', False),
            )
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - yesterday release, "
                "recently updated in history"
            )
            skipped_history += 1
            continue

        if (
            include_recent_release_filters
            and not is_adhoc_mode
            and should_skip_recent_today_release(
                href,
                history_data,
                entry.get('is_today_release', False),
            )
        ):
            logger.info(
                f"[{i}/{total_entries}] [Page {page_num}] "
                f"Skipping {entry['video_code']} - today release, "
                "already visited today"
            )
            skipped_history += 1
            continue

        prepared.append(
            DetailEntryCandidate(
                entry=entry,
                href=href,
                page_num=page_num,
                entry_index=f"{i}/{total_entries}",
            )
        )

    state.parsed_links.update(local_parsed_links)
    return prepared, skipped_history


def persist_parsed_detail_result(
    *,
    entry: dict,
    phase: int,
    entry_index: str = '',
    history_data: dict,
    history_file: str,
    csv_path: str,
    fieldnames: list,
    dry_run: bool,
    use_history_for_saving: bool,
    is_adhoc_mode: bool,
    rclone_inventory: Optional[dict] = None,
    enable_dedup: bool = False,
    dedup_csv_path: str = '',
    enable_redownload: bool = False,
    redownload_threshold: float = 0.30,
    actor_info: str = '',
    actor_gender: str = '',
    actor_link: str = '',
    supporting_actors: str = '',
    magnet_links: Optional[dict] = None,
) -> DetailPersistOutcome:
    """Build ingestion plan, write outputs, and return outcome metadata."""

    href = entry['href']
    video_code = entry['video_code']
    page_num = entry['page']
    actor_info = actor_info or ''
    actor_gender = actor_gender or ''
    actor_link = actor_link or ''
    supporting_actors = supporting_actors or ''
    magnet_links = magnet_links or {}

    outcome = DetailPersistOutcome(
        status='reported',
        visited_href=href,
        actor_update=(
            href,
            actor_info,
            actor_gender,
            actor_link,
            supporting_actors,
        ),
    )

    parsed_movie = ParsedMovie(
        href=href,
        video_code=video_code,
        page_num=page_num,
        actor_name=actor_info,
        actor_gender=actor_gender,
        actor_link=actor_link,
        supporting_actors=supporting_actors,
        magnet_links=magnet_links,
        entry=entry,
    )

    rclone_entries = []
    if rclone_inventory and video_code:
        rclone_entries = rclone_inventory.get(video_code.upper(), [])

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
        if entry_index:
            logger.debug(
                f"[{entry_index}] [Page {page_num}] "
                f"Skipping based on ingestion plan: {plan.skip_reason}"
            )
        outcome.status = 'skipped'
        outcome.skipped_history = 1
        return outcome

    for rec in plan.dedup_records:
        if not dry_run and dedup_csv_path:
            append_dedup_record(dedup_csv_path, rec)
        if entry_index:
            logger.info(
                f"[{entry_index}] DEDUP: {rec.video_code} - "
                f"{rec.deletion_reason}"
            )

    row = plan.report_row
    if row is None:
        outcome.status = 'no_row'
        outcome.no_new_torrents = 1
        return outcome

    if plan.should_include_in_report:
        write_csv([row], csv_path, fieldnames, dry_run, append_mode=True)
        outcome.row = row
        if (
            use_history_for_saving
            and not dry_run
            and plan.has_new_torrents
            and plan.new_magnet_links
        ):
            save_parsed_movie_to_history(
                history_file,
                href,
                phase,
                video_code,
                plan.new_magnet_links,
                size_links=plan.new_sizes,
                file_count_links=plan.new_file_counts,
                resolution_links=plan.new_resolutions,
                actor_name=actor_info,
                actor_gender=actor_gender,
                actor_link=actor_link,
                supporting_actors=supporting_actors,
            )
        return outcome

    outcome.status = 'not_included'
    outcome.no_new_torrents = 1
    return outcome


def finalize_detail_phase(
    *,
    use_history_for_saving: bool,
    dry_run: bool,
    history_file: str,
    visited_hrefs: set,
    actor_updates: list,
) -> None:
    """Flush shared per-phase side effects after detail processing completes."""

    if use_history_for_saving and not dry_run and visited_hrefs:
        if use_sqlite() and actor_updates:
            db_batch_update_movie_actors(actor_updates)
        batch_update_last_visited(history_file, visited_hrefs)
