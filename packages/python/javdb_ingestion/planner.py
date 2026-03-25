"""High-level ingestion planners built on shared policies and adapters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from packages.python.javdb_ingestion.adapters import (
    build_alignment_purge_plan_rows,
    build_alignment_qb_row,
    check_torrent_status,
    collect_new_magnet_links,
    create_csv_row_with_history_filter,
    create_redownload_row,
)
from packages.python.javdb_ingestion.models import AlignmentUpgradePlan, ParsedMovie, SpiderIngestionPlan
from packages.python.javdb_ingestion.policies import (
    ALIGNMENT_CENSORED_FAMILY,
    ALIGNMENT_UNCENSORED_FAMILY,
    alignment_best_inventory_rank,
    alignment_best_inventory_rank_for_family,
    alignment_best_parsed_category_for_family,
    alignment_parsed_category_rank,
    check_redownload_upgrade,
    should_process_movie,
)
from packages.python.javdb_spider.services.dedup import (
    DedupRecord,
    check_dedup_upgrade,
    check_redownload_dedup_upgrade,
)


def _combined_magnet_payload(parsed_movie: ParsedMovie) -> Dict[str, Any]:
    """Merge category, size, file-count, and resolution payloads for legacy helpers."""
    payload: Dict[str, Any] = {
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
        'size_hacked_subtitle': '',
        'size_hacked_no_subtitle': '',
        'size_subtitle': '',
        'size_no_subtitle': '',
        'file_count_hacked_subtitle': 0,
        'file_count_hacked_no_subtitle': 0,
        'file_count_subtitle': 0,
        'file_count_no_subtitle': 0,
        'resolution_hacked_subtitle': None,
        'resolution_hacked_no_subtitle': None,
        'resolution_subtitle': None,
        'resolution_no_subtitle': None,
    }
    payload.update(parsed_movie.magnet_links)
    for category, size in parsed_movie.size_links.items():
        payload[f'size_{category}'] = size
    for category, file_count in parsed_movie.file_count_links.items():
        payload[f'file_count_{category}'] = file_count
    for category, resolution in parsed_movie.resolution_links.items():
        payload[f'resolution_{category}'] = resolution
    return payload


def build_spider_ingestion_plan(
    parsed_movie: ParsedMovie,
    *,
    history_data: dict,
    phase: int,
    rclone_entries: Optional[List[Any]] = None,
    enable_dedup: bool = False,
    enable_redownload: bool = False,
    redownload_threshold: float = 0.30,
) -> SpiderIngestionPlan:
    """Build a spider ingestion plan from parsed detail-page data."""
    magnet_payload = _combined_magnet_payload(parsed_movie)
    should_process, history_torrent_types = should_process_movie(
        parsed_movie.href,
        history_data,
        phase,
        magnet_payload,
    )
    history_torrent_types = history_torrent_types or []

    redownload_categories: List[str] = []
    if not should_process and enable_redownload:
        redownload_categories = check_redownload_upgrade(
            parsed_movie.href,
            history_data,
            magnet_payload,
            redownload_threshold,
        )

    if not should_process and not redownload_categories:
        return SpiderIngestionPlan(
            should_skip=True,
            skip_reason='history_no_missing_types',
            history_torrent_types=history_torrent_types,
        )

    dedup_records: List[DedupRecord] = []
    if enable_dedup and rclone_entries and parsed_movie.video_code:
        torrent_types = {
            'subtitle': bool(parsed_movie.magnet_links.get('subtitle')),
            'hacked_subtitle': bool(parsed_movie.magnet_links.get('hacked_subtitle')),
            'hacked_no_subtitle': bool(parsed_movie.magnet_links.get('hacked_no_subtitle')),
            'no_subtitle': bool(parsed_movie.magnet_links.get('no_subtitle')),
        }
        dedup_records = check_dedup_upgrade(
            parsed_movie.video_code.upper(),
            torrent_types,
            rclone_entries,
        )
        if redownload_categories:
            dedup_records.extend(
                check_redownload_dedup_upgrade(
                    parsed_movie.video_code.upper(),
                    redownload_categories,
                    magnet_payload,
                    rclone_entries,
                )
            )

    if redownload_categories:
        report_row = create_redownload_row(
            parsed_movie.href,
            parsed_movie.entry,
            parsed_movie.page_num,
            parsed_movie.actor_name,
            magnet_payload,
            redownload_categories,
        )
    else:
        report_row = create_csv_row_with_history_filter(
            parsed_movie.href,
            parsed_movie.entry,
            parsed_movie.page_num,
            parsed_movie.actor_name,
            magnet_payload,
            history_data,
        )

    report_row['video_code'] = parsed_movie.video_code
    has_any, has_new, should_include = check_torrent_status(report_row)

    new_magnets = {}
    new_sizes = {}
    new_file_counts = {}
    new_resolutions = {}
    if should_include and has_new:
        new_magnets, new_sizes, new_file_counts, new_resolutions = collect_new_magnet_links(
            report_row,
            magnet_payload,
        )

    return SpiderIngestionPlan(
        should_skip=False,
        history_torrent_types=history_torrent_types,
        redownload_categories=redownload_categories,
        dedup_records=dedup_records,
        report_row=report_row,
        has_any_torrents=has_any,
        has_new_torrents=has_new,
        should_include_in_report=should_include,
        new_magnet_links=new_magnets,
        new_sizes=new_sizes,
        new_file_counts=new_file_counts,
        new_resolutions=new_resolutions,
    )


def build_alignment_upgrade_plan(
    *,
    detail_href: str,
    video_code: str,
    magnet_links: Dict[str, str],
    inventory_entries: List[dict],
) -> AlignmentUpgradePlan:
    """Build a family-aware alignment upgrade plan from parsed magnets and inventory."""
    chosen_categories = []
    purge_plan_rows = []
    parsed_best_rank = 0
    inventory_best_rank = alignment_best_inventory_rank(inventory_entries)

    for family in (ALIGNMENT_CENSORED_FAMILY, ALIGNMENT_UNCENSORED_FAMILY):
        parsed_category = alignment_best_parsed_category_for_family(magnet_links, family)
        family_parsed_rank = alignment_parsed_category_rank(parsed_category)
        family_inventory_rank = alignment_best_inventory_rank_for_family(inventory_entries, family)

        parsed_best_rank = max(parsed_best_rank, family_parsed_rank)

        if not parsed_category or family_parsed_rank <= family_inventory_rank:
            continue

        chosen_categories.append(parsed_category)
        purge_plan_rows.extend(
            build_alignment_purge_plan_rows(
                video_code,
                inventory_entries,
                family_parsed_rank,
                parsed_category,
            )
        )

    if not chosen_categories:
        return AlignmentUpgradePlan(
            chosen_upgrade_category='',
            chosen_upgrade_categories=[],
            parsed_best_rank=parsed_best_rank,
            inventory_best_rank=inventory_best_rank,
            qb_rows=[],
            purge_plan_rows=[],
        )

    return AlignmentUpgradePlan(
        chosen_upgrade_category=','.join(chosen_categories),
        chosen_upgrade_categories=chosen_categories,
        parsed_best_rank=parsed_best_rank,
        inventory_best_rank=inventory_best_rank,
        qb_rows=[
            build_alignment_qb_row(
                detail_href,
                video_code,
                chosen_categories,
                magnet_links,
            )
        ],
        purge_plan_rows=purge_plan_rows,
    )
