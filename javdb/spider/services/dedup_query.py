"""Pure skip-time dedup decisions over an already-loaded inventory."""

from datetime import datetime
from typing import Dict, List, Optional, Set

from javdb.infra.logging import get_logger
from javdb.parsing.common import normalise_code
from javdb.spider.contracts import (
    get_uncensored_priority,
    is_uncensored_category,
)
from javdb.spider.magnet_extractor import _parse_size
from javdb.spider.services.dedup_types import DedupRecord, RcloneEntry

# Optional Rust-backed dedup helpers (inlined from the former bridges.rust_adapters.dedup_adapter shim)
try:
    from javdb.rust_core import should_skip_from_rclone as _rs_should_skip_from_rclone
    from javdb.rust_core import check_dedup_upgrade as _rs_check_dedup_upgrade

    RUST_DEDUP_AVAILABLE = True
except ImportError:
    RUST_DEDUP_AVAILABLE = False
    _rs_should_skip_from_rclone = None
    _rs_check_dedup_upgrade = None


def rust_should_skip_from_rclone(
    video_code: str, entries: List[dict], enable_dedup: bool,
) -> Optional[bool]:
    """Return True/False when Rust gives a definitive answer, None if unavailable."""
    if not RUST_DEDUP_AVAILABLE:
        return None
    try:
        return bool(_rs_should_skip_from_rclone(video_code, entries, enable_dedup))
    except Exception:
        logger.debug(
            "Rust dedup skip adapter failed for %s; falling back to Python",
            video_code,
            exc_info=True,
        )
        return None


def rust_check_dedup_upgrade(
    video_code: str, new_torrent_types: Dict[str, bool], entries: List[dict],
) -> List[dict]:
    if not RUST_DEDUP_AVAILABLE:
        return []
    try:
        result = _rs_check_dedup_upgrade(video_code, new_torrent_types, entries)
        if isinstance(result, list):
            return result
    except Exception:
        logger.debug(
            "Rust dedup upgrade adapter failed for %s; falling back to Python",
            video_code,
            exc_info=True,
        )
    return []


logger = get_logger(__name__)

# Priority aliases — delegated to javdb.spider.contracts
_is_wuma_category = is_uncensored_category
_get_wuma_priority = get_uncensored_priority


def is_in_rclone_inventory(video_code: str, inventory: Dict[str, List[RcloneEntry]]) -> bool:
    """Check whether a video_code exists in the rclone inventory."""
    return normalise_code(video_code) in inventory


def should_skip_from_rclone(
    video_code: str,
    inventory: Dict[str, List[RcloneEntry]],
    enable_dedup: bool = False,
) -> bool:
    """Determine if the spider should skip processing this video_code based
    on the rclone inventory.

    When dedup is enabled, we never skip purely based on rclone inventory
    because we still want to detect potential upgrades.  When dedup is
    disabled, we skip if any entry for this code already has 中字.
    """
    code = normalise_code(video_code)
    entries = inventory.get(code)
    if not entries:
        return False

    rust_result = rust_should_skip_from_rclone(
        code,
        [
            {
                'video_code': e.video_code,
                'subtitle_category': e.subtitle_category,
            }
            for e in entries
        ],
        enable_dedup,
    )
    if rust_result is not None:
        return rust_result

    if enable_dedup:
        return False

    return any(entry.subtitle_category == '中字' for entry in entries)


def check_dedup_upgrade(
    video_code: str,
    new_torrent_types: Dict[str, bool],
    rclone_entries: List[RcloneEntry],
) -> List[DedupRecord]:
    """Compare a newly found torrent against existing GDrive entries and
    return a list of DedupRecords for entries that should be replaced.

    ``new_torrent_types`` is a dict like:
        {'subtitle': True, 'hacked_subtitle': False, ...}

    Upgrade rules:
      - Subtitle upgrade: GDrive has 无字, spider found 中字 torrent
      - Sensor upgrade: GDrive has 无码破解, spider found 无码 or 无码流出
    """
    rust_records = rust_check_dedup_upgrade(
        video_code,
        new_torrent_types,
        [
            {
                'video_code': e.video_code,
                'sensor_category': e.sensor_category,
                'subtitle_category': e.subtitle_category,
                'folder_path': e.folder_path,
                'folder_size': e.folder_size,
            }
            for e in rclone_entries
        ],
    )
    if rust_records:
        return [
            DedupRecord(
                video_code=r.get('video_code', video_code.upper()),
                existing_sensor=r.get('existing_sensor', ''),
                existing_subtitle=r.get('existing_subtitle', ''),
                existing_gdrive_path=r.get('existing_gdrive_path', ''),
                existing_folder_size=int(r.get('existing_folder_size', 0) or 0),
                new_torrent_category=r.get('new_torrent_category', ''),
                deletion_reason=r.get('deletion_reason', ''),
                detect_datetime=r.get('detect_datetime', ''),
                is_deleted=r.get('is_deleted', 'False'),
                delete_datetime=r.get('delete_datetime', ''),
            )
            for r in rust_records
        ]

    records: List[DedupRecord] = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    has_subtitle = new_torrent_types.get('subtitle', False) or new_torrent_types.get('hacked_subtitle', False)

    for entry in rclone_entries:
        reason: Optional[str] = None

        # Subtitle upgrade
        if has_subtitle and entry.subtitle_category == '无字':
            reason = "Subtitle upgrade (中字 found, replacing 无字)"

        # Sensor upgrade: only relevant within 无码 family
        if _is_wuma_category(entry.sensor_category):
            existing_prio = _get_wuma_priority(entry.sensor_category)
            # Check if the new torrent indicates a higher-priority sensor category
            # Spider torrent types don't directly encode the sensor category,
            # but we can infer: non-hacked subtitle/no_subtitle = 无码 or higher
            if not new_torrent_types.get('hacked_subtitle', False) and not new_torrent_types.get('hacked_no_subtitle', False):
                inferred_prio = _get_wuma_priority('无码')
                if inferred_prio > existing_prio:
                    sensor_reason = f"Sensor upgrade (无码 > {entry.sensor_category})"
                    reason = f"{reason}; {sensor_reason}" if reason else sensor_reason

        if reason:
            new_cat_parts = []
            if new_torrent_types.get('subtitle') or new_torrent_types.get('hacked_subtitle'):
                new_cat_parts.append('中字')
            else:
                new_cat_parts.append('无字')
            if new_torrent_types.get('hacked_subtitle') or new_torrent_types.get('hacked_no_subtitle'):
                new_cat_parts.append('破解')

            records.append(DedupRecord(
                video_code=video_code.upper(),
                existing_sensor=entry.sensor_category,
                existing_subtitle=entry.subtitle_category,
                existing_gdrive_path=entry.folder_path,
                existing_folder_size=entry.folder_size,
                new_torrent_category='-'.join(new_cat_parts),
                deletion_reason=reason,
                detect_datetime=now_str,
                is_deleted='False',
                delete_datetime='',
            ))

    return records


def _redownload_category_matches_entry(category: str, entry: RcloneEntry) -> bool:
    """Return True when an rclone entry matches the broad torrent category."""
    if category not in {'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'}:
        return False

    wants_subtitle = category in {'hacked_subtitle', 'subtitle'}
    wants_uncensored = category in {'hacked_subtitle', 'hacked_no_subtitle'}

    if wants_uncensored:
        if not _is_wuma_category(entry.sensor_category):
            return False
    elif entry.sensor_category != '有码':
        return False

    return entry.subtitle_category == ('中字' if wants_subtitle else '无字')


def check_redownload_dedup_upgrade(
    video_code: str,
    redownload_categories: List[str],
    new_size_links: Dict[str, str],
    rclone_entries: List[RcloneEntry],
) -> List[DedupRecord]:
    """Queue matching old folders for dedup when a size-based re-download wins.

    The history layer only tracks the legacy broad categories, so we match
    inventory entries by family (有码 vs 无码*) plus subtitle state.  We keep
    a safety guard: if the existing folder is already at least as large as the
    newly found torrent, we do not queue it for deletion.
    """
    if not redownload_categories or not rclone_entries:
        return []

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    seen_paths: Set[str] = set()
    records: List[DedupRecord] = []

    for category in redownload_categories:
        new_size_str = new_size_links.get(f'size_{category}', '')
        new_size_bytes = _parse_size(new_size_str) if new_size_str else 0
        if new_size_bytes <= 0:
            continue

        for entry in rclone_entries:
            if not _redownload_category_matches_entry(category, entry):
                continue
            if entry.folder_path in seen_paths:
                continue
            if entry.folder_size > 0 and entry.folder_size >= new_size_bytes:
                logger.debug(
                    "Skipping size-based dedup for %s [%s]: existing folder %s is not smaller than %s",
                    video_code.upper(),
                    category,
                    entry.folder_size,
                    new_size_str,
                )
                continue

            records.append(DedupRecord(
                video_code=video_code.upper(),
                existing_sensor=entry.sensor_category,
                existing_subtitle=entry.subtitle_category,
                existing_gdrive_path=entry.folder_path,
                existing_folder_size=entry.folder_size,
                new_torrent_category=category,
                deletion_reason=(
                    f"Re-download upgrade ({category}: larger same-category torrent {new_size_str} found)"
                ),
                detect_datetime=now_str,
                is_deleted='False',
                delete_datetime='',
            ))
            seen_paths.add(entry.folder_path)

    return records
