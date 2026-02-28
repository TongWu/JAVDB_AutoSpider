"""CSV row construction with history-aware torrent filtering."""

from typing import Tuple

from utils.logging_config import get_logger
from utils.history_manager import determine_torrent_types, get_missing_torrent_types
from scripts.spider.config_loader import INCLUDE_DOWNLOADED_IN_REPORT

logger = get_logger(__name__)

try:
    from javdb_rust_core import create_csv_row as _rust_create_csv_row
    _RUST_CSV_ROW = True
except ImportError:
    _RUST_CSV_ROW = False


def check_torrent_status(row: dict) -> Tuple[bool, bool, bool]:
    """Determine download status for a CSV row's torrent fields.

    Returns:
        (has_any_torrents, has_new_torrents, should_include_in_report)
    """
    _TORRENT_FIELDS = ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle')
    has_any = any(row[f] for f in _TORRENT_FIELDS)
    has_new = any(
        row[f] and row[f] != '[DOWNLOADED PREVIOUSLY]'
        for f in _TORRENT_FIELDS
    )
    should_include = has_new or (INCLUDE_DOWNLOADED_IN_REPORT and has_any)
    return has_any, has_new, should_include


def collect_new_magnet_links(row: dict, magnet_links: dict) -> dict:
    """Extract magnet links that haven't been downloaded previously."""
    new_magnets = {}
    for mtype in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
        if row[mtype] and row[mtype] != '[DOWNLOADED PREVIOUSLY]':
            new_magnets[mtype] = magnet_links.get(mtype, '')
    return new_magnets


def should_include_torrent_in_csv(href, history_data, magnet_links):
    """Check if torrent categories should be included in CSV based on history."""
    if not history_data or href not in history_data:
        return True
    history_torrent_types = history_data[href].get('torrent_types', [])
    current_torrent_types = determine_torrent_types(magnet_links)
    for torrent_type in current_torrent_types:
        if torrent_type not in history_torrent_types:
            return True
    return False


def create_csv_row_with_history_filter(href, entry, page_num, actor_info,
                                       magnet_links, history_data):
    """Create CSV row with torrent categories, marking downloaded ones."""
    if _RUST_CSV_ROW:
        if not history_data or href not in history_data:
            hist_types: list = []
            miss_types: list = []
        else:
            hist_types = history_data[href].get('torrent_types', [])
            current_types = determine_torrent_types(magnet_links)
            miss_types = get_missing_torrent_types(hist_types, current_types)
        try:
            return _rust_create_csv_row(
                href,
                entry['video_code'],
                int(page_num) if page_num else 0,
                actor_info or '',
                entry.get('rate', ''),
                entry.get('comment_number', ''),
                magnet_links,
                hist_types,
                miss_types,
            )
        except Exception as e:
            logger.debug(f"Rust create_csv_row failed ({e}), falling back to Python")

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
        logger.debug(f"Adding missing hacked_subtitle torrent for {entry['video_code']}")

    if 'hacked_no_subtitle' in missing_types and magnet_links['hacked_no_subtitle']:
        row['hacked_no_subtitle'] = magnet_links['hacked_no_subtitle']
        row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
        logger.debug(f"Adding missing hacked_no_subtitle torrent for {entry['video_code']}")

    if 'subtitle' in missing_types and magnet_links['subtitle']:
        row['subtitle'] = magnet_links['subtitle']
        row['size_subtitle'] = magnet_links['size_subtitle']
        logger.debug(f"Adding missing subtitle torrent for {entry['video_code']}")

    if 'no_subtitle' in missing_types and magnet_links['no_subtitle']:
        row['no_subtitle'] = magnet_links['no_subtitle']
        row['size_no_subtitle'] = magnet_links['size_no_subtitle']
        logger.debug(f"Adding missing no_subtitle torrent for {entry['video_code']}")

    if 'hacked_subtitle' in history_torrent_types and magnet_links['hacked_subtitle']:
        row['hacked_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_hacked_subtitle'] = magnet_links['size_hacked_subtitle']
        logger.debug(f"Marking hacked_subtitle as downloaded for {entry['video_code']}")

    if 'hacked_no_subtitle' in history_torrent_types and magnet_links['hacked_no_subtitle']:
        row['hacked_no_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_hacked_no_subtitle'] = magnet_links['size_hacked_no_subtitle']
        logger.debug(f"Marking hacked_no_subtitle as downloaded for {entry['video_code']}")

    if 'subtitle' in history_torrent_types and magnet_links['subtitle']:
        row['subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_subtitle'] = magnet_links['size_subtitle']
        logger.debug(f"Marking subtitle as downloaded for {entry['video_code']}")

    if 'no_subtitle' in history_torrent_types and magnet_links['no_subtitle']:
        row['no_subtitle'] = '[DOWNLOADED PREVIOUSLY]'
        row['size_no_subtitle'] = magnet_links['size_no_subtitle']
        logger.debug(f"Marking no_subtitle as downloaded for {entry['video_code']}")

    return row
