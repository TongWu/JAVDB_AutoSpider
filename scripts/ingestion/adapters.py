"""Adapters that convert ingestion decisions into external row formats."""

from __future__ import annotations

from typing import Dict, List, Tuple

from scripts.ingestion.policies import (
    alignment_category_family,
    alignment_inventory_entry_family,
    alignment_inventory_entry_rank,
    determine_torrent_types,
    get_missing_torrent_types,
)
from scripts.spider.config_loader import INCLUDE_DOWNLOADED_IN_REPORT
from utils.contracts import DOWNLOADED_PLACEHOLDER, TORRENT_CATEGORIES
from utils.logging_config import get_logger
from utils.rust_adapters.csv_adapter import (
    RUST_CSV_AVAILABLE,
    rust_check_torrent_status,
    rust_collect_new_magnet_links,
    rust_create_csv_row,
)

logger = get_logger(__name__)


def check_torrent_status(row: dict) -> Tuple[bool, bool, bool]:
    """Determine download status for a CSV row's torrent fields."""
    rust_result = rust_check_torrent_status(row, INCLUDE_DOWNLOADED_IN_REPORT)
    if rust_result is not None:
        return rust_result

    has_any = any(row[f] for f in TORRENT_CATEGORIES)
    has_new = any(
        row[f] and row[f] != DOWNLOADED_PLACEHOLDER
        for f in TORRENT_CATEGORIES
    )
    should_include = has_new or (INCLUDE_DOWNLOADED_IN_REPORT and has_any)
    return has_any, has_new, should_include


def collect_new_magnet_links(row: dict, magnet_links: dict):
    """Extract magnet links, sizes, file counts, and resolutions for new torrents."""
    rust_result = rust_collect_new_magnet_links(row, magnet_links)
    if rust_result is not None:
        return rust_result

    new_magnets = {}
    new_sizes = {}
    new_file_counts = {}
    new_resolutions = {}
    for mtype in TORRENT_CATEGORIES:
        if row.get(mtype) and row[mtype] != DOWNLOADED_PLACEHOLDER:
            new_magnets[mtype] = magnet_links.get(mtype, '')
            new_sizes[mtype] = magnet_links.get(f'size_{mtype}', '')
            new_file_counts[mtype] = magnet_links.get(f'file_count_{mtype}', 0)
            new_resolutions[mtype] = magnet_links.get(f'resolution_{mtype}')
    return new_magnets, new_sizes, new_file_counts, new_resolutions


def should_include_torrent_in_csv(href: str, history_data: dict, magnet_links: dict) -> bool:
    """Check if torrent categories should be included in CSV based on history."""
    if not history_data or href not in history_data:
        return True
    history_torrent_types = history_data[href].get('torrent_types', [])
    current_torrent_types = determine_torrent_types(magnet_links)
    for torrent_type in current_torrent_types:
        if torrent_type not in history_torrent_types:
            return True
    return False


def create_csv_row_with_history_filter(href, entry, page_num, actor_info, magnet_links, history_data):
    """Create CSV row with torrent categories, marking downloaded ones."""
    if RUST_CSV_AVAILABLE:
        if not history_data or href not in history_data:
            hist_types = []
            miss_types = []
        else:
            hist_types = history_data[href].get('torrent_types', [])
            current_types = determine_torrent_types(magnet_links)
            miss_types = get_missing_torrent_types(hist_types, current_types)
        rust_row = rust_create_csv_row(
            href,
            entry["video_code"],
            int(page_num) if page_num else 0,
            actor_info or "",
            entry.get("rate", ""),
            entry.get("comment_number", ""),
            magnet_links,
            hist_types,
            miss_types,
        )
        if rust_row is not None:
            return rust_row
        logger.debug("Rust create_csv_row failed, falling back to Python")

    if not history_data or href not in history_data:
        row = {
            'href': href,
            'video_code': entry['video_code'],
            'page': page_num,
            'actor': actor_info,
            'rate': entry['rate'],
            'comment_number': entry['comment_number'],
            'hacked_subtitle': magnet_links['hacked_subtitle'],
            'hacked_no_subtitle': '',
            'subtitle': magnet_links['subtitle'],
            'no_subtitle': '',
            'size_hacked_subtitle': magnet_links['size_hacked_subtitle'],
            'size_hacked_no_subtitle': '',
            'size_subtitle': magnet_links['size_subtitle'],
            'size_no_subtitle': '',
        }
        if magnet_links['subtitle']:
            row['no_subtitle'] = ''
            row['size_no_subtitle'] = ''
        else:
            row['no_subtitle'] = magnet_links['no_subtitle']
            row['size_no_subtitle'] = magnet_links['size_no_subtitle']
        if magnet_links['hacked_subtitle']:
            row['hacked_no_subtitle'] = ''
            row['size_hacked_no_subtitle'] = ''
        else:
            row['hacked_no_subtitle'] = magnet_links['hacked_no_subtitle']
            row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
        return row

    history_torrent_types = history_data[href].get('torrent_types', [])
    current_torrent_types = determine_torrent_types(magnet_links)
    missing_types = get_missing_torrent_types(history_torrent_types, current_torrent_types)

    row = {
        'href': href,
        'video_code': entry['video_code'],
        'page': page_num,
        'actor': actor_info,
        'rate': entry['rate'],
        'comment_number': entry['comment_number'],
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
        'size_hacked_subtitle': '',
        'size_hacked_no_subtitle': '',
        'size_subtitle': '',
        'size_no_subtitle': '',
    }

    if 'hacked_subtitle' in missing_types and magnet_links['hacked_subtitle']:
        row['hacked_subtitle'] = magnet_links['hacked_subtitle']
        row['size_hacked_subtitle'] = magnet_links['size_hacked_subtitle']
    if 'hacked_no_subtitle' in missing_types and magnet_links['hacked_no_subtitle']:
        row['hacked_no_subtitle'] = magnet_links['hacked_no_subtitle']
        row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
    if 'subtitle' in missing_types and magnet_links['subtitle']:
        row['subtitle'] = magnet_links['subtitle']
        row['size_subtitle'] = magnet_links['size_subtitle']
    if 'no_subtitle' in missing_types and magnet_links['no_subtitle']:
        row['no_subtitle'] = magnet_links['no_subtitle']
        row['size_no_subtitle'] = magnet_links['size_no_subtitle']

    if 'hacked_subtitle' in history_torrent_types and magnet_links['hacked_subtitle']:
        row['hacked_subtitle'] = DOWNLOADED_PLACEHOLDER
        row['size_hacked_subtitle'] = magnet_links['size_hacked_subtitle']
    if 'hacked_no_subtitle' in history_torrent_types and magnet_links['hacked_no_subtitle']:
        row['hacked_no_subtitle'] = DOWNLOADED_PLACEHOLDER
        row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
    if 'subtitle' in history_torrent_types and magnet_links['subtitle']:
        row['subtitle'] = DOWNLOADED_PLACEHOLDER
        row['size_subtitle'] = magnet_links['size_subtitle']
    if 'no_subtitle' in history_torrent_types and magnet_links['no_subtitle']:
        row['no_subtitle'] = DOWNLOADED_PLACEHOLDER
        row['size_no_subtitle'] = magnet_links['size_no_subtitle']

    return row


def create_redownload_row(href, entry, page_num, actor_info, magnet_links, redownload_categories):
    """Create a CSV row containing only the re-download upgrade categories."""
    row = {
        'href': href,
        'video_code': entry['video_code'],
        'page': page_num,
        'actor': actor_info,
        'rate': entry['rate'],
        'comment_number': entry['comment_number'],
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
        'size_hacked_subtitle': '',
        'size_hacked_no_subtitle': '',
        'size_subtitle': '',
        'size_no_subtitle': '',
    }
    for cat in redownload_categories:
        row[cat] = magnet_links.get(cat, '')
        row[f'size_{cat}'] = magnet_links.get(f'size_{cat}', '')
    return row


def build_alignment_qb_row(href: str, video_code: str, chosen_category, magnet_links: Dict[str, str]) -> dict:
    """Build a qB import row for one or more chosen alignment upgrade categories."""
    if isinstance(chosen_category, str):
        categories = [chosen_category] if chosen_category else []
    else:
        categories = [category for category in chosen_category if category]

    row = {
        'href': href,
        'video_code': video_code,
        'page': 1,
        'hacked_subtitle': '',
        'hacked_no_subtitle': '',
        'subtitle': '',
        'no_subtitle': '',
    }
    for category in categories:
        row[category] = magnet_links.get(category, '')
    return row


def build_alignment_purge_plan_rows(
    video_code: str,
    inventory_entries: List[dict],
    parsed_best_rank: int,
    new_torrent_category: str,
) -> List[dict]:
    """Rows for rclone purge where alignment found a better legacy category."""
    rows = []
    new_family = alignment_category_family(new_torrent_category)
    for entry in inventory_entries:
        src = (entry.get('FolderPath') or entry.get('folder_path') or '').strip()
        if not src:
            continue
        if new_family and alignment_inventory_entry_family(entry) != new_family:
            continue
        if alignment_inventory_entry_rank(entry) >= parsed_best_rank:
            continue
        rows.append({
            'video_code': video_code,
            'source_path': src,
            'existing_sensor': entry.get('SensorCategory') or entry.get('sensor_category') or '',
            'existing_subtitle': entry.get('SubtitleCategory') or entry.get('subtitle_category') or '',
            'new_torrent_category': new_torrent_category,
            'reason': 'parsed_better_version',
        })
    return rows

