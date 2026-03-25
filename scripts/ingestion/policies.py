"""Pure ingestion policies shared by spider and migration tools."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from utils.contracts import category_to_indicators, is_uncensored_category
from utils.logging_config import get_logger
from utils.magnet_extractor import _parse_size

logger = get_logger(__name__)

ALIGNMENT_CENSORED_FAMILY = 'censored'
ALIGNMENT_UNCENSORED_FAMILY = 'uncensored'
ALIGNMENT_PARSED_FAMILY_CANDIDATES = {
    ALIGNMENT_CENSORED_FAMILY: ('subtitle', 'no_subtitle'),
    ALIGNMENT_UNCENSORED_FAMILY: ('hacked_subtitle', 'hacked_no_subtitle'),
}


def determine_torrent_types(magnet_links: dict) -> List[str]:
    """Determine torrent types from magnet links dictionary."""
    torrent_types = []
    if magnet_links.get('hacked_subtitle', '').strip():
        torrent_types.append('hacked_subtitle')
    if magnet_links.get('hacked_no_subtitle', '').strip():
        torrent_types.append('hacked_no_subtitle')
    if magnet_links.get('subtitle', '').strip():
        torrent_types.append('subtitle')
    if magnet_links.get('no_subtitle', '').strip():
        torrent_types.append('no_subtitle')
    return torrent_types


def determine_torrent_type(magnet_links: dict) -> str:
    """Legacy single-type helper kept for compatibility."""
    types = determine_torrent_types(magnet_links)
    return types[0] if types else 'no_subtitle'


def get_missing_torrent_types(history_torrent_types: List[str], current_torrent_types: List[str]) -> List[str]:
    """Get missing torrent types that should be searched for."""
    missing_types = []

    has_hacked_subtitle_in_history = 'hacked_subtitle' in history_torrent_types
    has_hacked_no_subtitle_in_history = 'hacked_no_subtitle' in history_torrent_types
    has_subtitle_in_history = 'subtitle' in history_torrent_types
    has_no_subtitle_in_history = 'no_subtitle' in history_torrent_types

    has_hacked_subtitle_current = 'hacked_subtitle' in current_torrent_types
    has_hacked_no_subtitle_current = 'hacked_no_subtitle' in current_torrent_types
    has_subtitle_current = 'subtitle' in current_torrent_types
    has_no_subtitle_current = 'no_subtitle' in current_torrent_types

    if has_hacked_subtitle_current and not has_hacked_subtitle_in_history:
        missing_types.append('hacked_subtitle')
    elif has_hacked_no_subtitle_current and not has_hacked_no_subtitle_in_history and not has_hacked_subtitle_in_history:
        missing_types.append('hacked_no_subtitle')

    if has_subtitle_current and not has_subtitle_in_history:
        missing_types.append('subtitle')
    elif has_no_subtitle_current and not has_no_subtitle_in_history and not has_subtitle_in_history:
        missing_types.append('no_subtitle')

    return missing_types


def has_complete_subtitles(href: str, history_data: dict) -> bool:
    """Check if a movie already has both subtitle and hacked_subtitle in history."""
    if not history_data or href not in history_data:
        return False
    entry = history_data[href]
    if entry.get('PerfectMatchIndicator'):
        return True
    torrent_types = entry.get('torrent_types', [])
    return 'subtitle' in torrent_types and 'hacked_subtitle' in torrent_types


def _get_visited_datetime(entry: dict) -> str:
    """Get the last visited datetime from a history entry."""
    return (
        entry.get('DateTimeVisited', '')
        or entry.get('last_visited_datetime', '')
        or entry.get('DateTimeUpdated', '')
        or entry.get('update_datetime', '')
    )


def should_skip_recent_yesterday_release(href: str, history_data: dict, is_yesterday_release: bool) -> bool:
    """Skip a movie if it was visited recently and is tagged as yesterday's release."""
    if not is_yesterday_release:
        return False
    if not history_data or href not in history_data:
        return False
    visited_str = _get_visited_datetime(history_data[href])
    if not visited_str:
        return False
    cutoff = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    return visited_str[:10] >= cutoff


def should_skip_recent_today_release(href: str, history_data: dict, is_today_release: bool) -> bool:
    """Skip a movie if it was already visited today and is tagged as today's release."""
    if not is_today_release:
        return False
    if not history_data or href not in history_data:
        return False
    visited_str = _get_visited_datetime(history_data[href])
    if not visited_str:
        return False
    cutoff = datetime.now().strftime('%Y-%m-%d')
    return visited_str[:10] >= cutoff


def should_process_movie(href: str, history_data: dict, phase: int, magnet_links: dict):
    """Determine if a movie should be processed based on history and phase rules."""
    if href not in history_data:
        logger.debug(f"New movie {href}: should process")
        return True, None

    current_torrent_types = determine_torrent_types(magnet_links)
    history_torrent_types = history_data[href].get('torrent_types', ['no_subtitle'])

    logger.debug(f"Movie {href}: current={current_torrent_types}, history={history_torrent_types}, phase={phase}")

    missing_types = get_missing_torrent_types(history_torrent_types, current_torrent_types)

    if phase == 1:
        if missing_types:
            logger.debug(f"Phase 1: missing types {missing_types} -> should process")
            return True, history_torrent_types
        logger.debug("Phase 1: no missing types -> should not process")
        return False, history_torrent_types

    if phase == 2:
        if 'no_subtitle' in history_torrent_types and 'hacked_no_subtitle' in current_torrent_types:
            logger.debug("Phase 2: upgrading no_subtitle to hacked_no_subtitle -> should process")
            return True, history_torrent_types
        if missing_types:
            logger.debug(f"Phase 2: missing types {missing_types} -> should process")
            return True, history_torrent_types
        logger.debug("Phase 2: no upgrade possible -> should not process")
        return False, history_torrent_types

    return False, history_torrent_types


def check_redownload_upgrade(href: str, history_data: dict, magnet_links: dict, threshold: float = 0.30) -> List[str]:
    """Check if any same-category torrent qualifies for re-download."""
    if not history_data or href not in history_data:
        return []

    entry = history_data[href]
    torrents = entry.get('torrents', {})
    upgrade_categories = []

    for cat in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
        new_magnet = magnet_links.get(cat, '')
        if not new_magnet:
            continue

        new_size_str = magnet_links.get(f'size_{cat}', '')
        if not new_size_str:
            continue

        key = category_to_indicators(cat)
        old_torrent = torrents.get(key, {})
        old_size_str = old_torrent.get('Size', '') or entry.get(f'size_{cat}', '')
        if not old_size_str:
            continue

        old_bytes = _parse_size(old_size_str)
        new_bytes = _parse_size(new_size_str)
        if old_bytes <= 0:
            continue
        if new_bytes > old_bytes * (1 + threshold):
            logger.info(
                f"Re-download upgrade for {href} [{cat}]: "
                f"{old_size_str} -> {new_size_str} "
                f"(+{((new_bytes / old_bytes) - 1) * 100:.0f}%, threshold {threshold * 100:.0f}%)"
            )
            upgrade_categories.append(cat)

    return upgrade_categories


def alignment_parsed_category_rank(category: str) -> int:
    """Legacy alignment rank mapping used for upgrade-plan generation."""
    rank_map = {
        'hacked_subtitle': 40,
        'hacked_no_subtitle': 30,
        'subtitle': 20,
        'no_subtitle': 10,
    }
    return rank_map.get(category, 0)


def alignment_inventory_entry_rank(entry: dict) -> int:
    """Legacy inventory rank mapping used by alignment."""
    sensor = (entry.get('SensorCategory') or entry.get('sensor_category') or '').strip()
    subtitle = (entry.get('SubtitleCategory') or entry.get('subtitle_category') or '').strip()
    if sensor == '有码':
        return 20 if subtitle == '中字' else 10
    if sensor == '无码破解':
        return 40 if subtitle == '中字' else 30
    if sensor == '无码':
        return 55 if subtitle == '中字' else 50
    if sensor == '无码流出':
        return 65 if subtitle == '中字' else 60
    return 0


def alignment_best_inventory_rank(entries: List[dict]) -> int:
    """Return the best legacy-rank entry from the current inventory list."""
    if not entries:
        return 0
    return max(alignment_inventory_entry_rank(entry) for entry in entries)


def alignment_best_parsed_category(magnet_links: Dict[str, str]) -> str:
    """Return the best parsed category according to the legacy rank model."""
    candidates = ['hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
    best = ''
    best_rank = 0
    for category in candidates:
        if not magnet_links.get(category):
            continue
        rank = alignment_parsed_category_rank(category)
        if rank > best_rank:
            best = category
            best_rank = rank
    return best


def alignment_category_family(category: str) -> str:
    """Map a parsed torrent category to its planning family."""
    for family, categories in ALIGNMENT_PARSED_FAMILY_CANDIDATES.items():
        if category in categories:
            return family
    return ''


def alignment_inventory_entry_family(entry: dict) -> str:
    """Map an inventory entry to its planning family."""
    sensor = (entry.get('SensorCategory') or entry.get('sensor_category') or '').strip()
    if sensor == '有码':
        return ALIGNMENT_CENSORED_FAMILY
    if is_uncensored_category(sensor):
        return ALIGNMENT_UNCENSORED_FAMILY
    return ''


def alignment_best_inventory_rank_for_family(entries: List[dict], family: str) -> int:
    """Return the best inventory rank inside a single planning family."""
    family_ranks = [
        alignment_inventory_entry_rank(entry)
        for entry in entries
        if alignment_inventory_entry_family(entry) == family
    ]
    return max(family_ranks) if family_ranks else 0


def alignment_best_parsed_category_for_family(magnet_links: Dict[str, str], family: str) -> str:
    """Return the best parsed category inside a single planning family."""
    best = ''
    best_rank = 0
    for category in ALIGNMENT_PARSED_FAMILY_CANDIDATES.get(family, ()):
        if not magnet_links.get(category):
            continue
        rank = alignment_parsed_category_rank(category)
        if rank > best_rank:
            best = category
            best_rank = rank
    return best

