"""Selection logic for parsed JavDB index entries."""

from __future__ import annotations

import logging
from typing import Iterable

from javdb.infra.config import cfg

PHASE2_MIN_RATE = cfg('PHASE2_MIN_RATE', 4.0)
PHASE2_MIN_COMMENTS = cfg('PHASE2_MIN_COMMENTS', 100)
IGNORE_RELEASE_DATE_FILTER = cfg('IGNORE_RELEASE_DATE_FILTER', False)

logger = logging.getLogger(__name__)

_SUBTITLE_TAGS = frozenset(['含中字磁鏈', '含中字磁链', 'CnSub DL'])
_MAGNET_TAGS = frozenset(['含磁鏈', '含磁链', 'DL'])
_TODAY_TAGS = frozenset(['今日新種', '今日新种', 'Today'])
_YESTERDAY_TAGS = frozenset(['昨日新種', '昨日新种', 'Yesterday'])
_RELEASE_DATE_TAGS = frozenset([
    '今日新種', '昨日新種',
    '今日新种', '昨日新种',
    'Today', 'Yesterday',
])


def _has_subtitle(tags: Iterable[str]) -> bool:
    return bool(_SUBTITLE_TAGS.intersection(tags))


def _has_magnet(tags: Iterable[str]) -> bool:
    return bool(_MAGNET_TAGS.intersection(tags)) or _has_subtitle(tags)


def _has_release_date(tags: Iterable[str]) -> bool:
    return bool(_RELEASE_DATE_TAGS.intersection(tags))


def _is_today_release(tags: Iterable[str]) -> bool:
    return bool(_TODAY_TAGS.intersection(tags))


def _is_yesterday_release(tags: Iterable[str]) -> bool:
    return bool(_YESTERDAY_TAGS.intersection(tags))


def _entry_to_legacy_dict(entry, page_num: int) -> dict:
    legacy = entry.to_legacy_dict()
    legacy['page'] = page_num
    legacy['is_today_release'] = _is_today_release(entry.tags)
    legacy['is_yesterday_release'] = _is_yesterday_release(entry.tags)
    return legacy


def select_index_entries(page_result, page_num: int, phase: int = 1, disable_new_releases_filter: bool = False, is_adhoc_mode: bool = False) -> list[dict]:
    """Apply spider selection rules to parsed index entries."""
    if not page_result.has_movie_list:
        logger.warning(f'[Page {page_num}] No movie list found!')
        return []

    logger.debug(f"[Page {page_num}] Found movie list container")
    logger.debug(f"[Page {page_num}] Parsing index page for phase {phase}...")

    results = []

    for entry in page_result.movies:
        tags = entry.tags
        video_code = entry.video_code

        if not video_code:
            continue

        logger.debug(f"[Page {page_num}] Found tags: {tags}")

        if is_adhoc_mode:
            has_sub = _has_subtitle(tags)
            has_mag = _has_magnet(tags)

            if not has_mag:
                logger.debug(f"[Page {page_num}] Skipping entry without magnet link (no magnet tag in HTML)")
                continue

            if phase == 1 and has_sub:
                logger.debug(f"[Page {page_num}] Found entry (adhoc P1): {video_code} ({entry.href})")
                results.append(_entry_to_legacy_dict(entry, page_num))
            elif phase == 2 and not has_sub:
                logger.debug(f"[Page {page_num}] Found entry (adhoc P2): {video_code} ({entry.href})")
                results.append(_entry_to_legacy_dict(entry, page_num))
            continue

        if phase == 1:
            if disable_new_releases_filter:
                if _has_subtitle(tags):
                    logger.debug(f"[Page {page_num}] Found entry (filter disabled): {video_code} ({entry.href})")
                    results.append(_entry_to_legacy_dict(entry, page_num))
            else:
                has_sub = _has_subtitle(tags)
                has_rd = _has_release_date(tags)
                if has_sub and (IGNORE_RELEASE_DATE_FILTER or has_rd):
                    logger.debug(f"[Page {page_num}] Found entry: {video_code} ({entry.href})")
                    results.append(_entry_to_legacy_dict(entry, page_num))

        elif phase == 2:
            if _has_subtitle(tags):
                continue

            should_process = False
            if disable_new_releases_filter:
                should_process = True
            else:
                has_rd = _has_release_date(tags)
                if IGNORE_RELEASE_DATE_FILTER or has_rd:
                    should_process = True

            if should_process:
                try:
                    comment_num = int(entry.comment_count) if entry.comment_count else 0
                    rate_num = float(entry.rate) if entry.rate else 0

                    if comment_num >= PHASE2_MIN_COMMENTS and rate_num >= PHASE2_MIN_RATE:
                        logger.debug(
                            f"[Page {page_num}] Found entry: {video_code} ({entry.href}) "
                            f"- Rate: {entry.rate}, Comments: {entry.comment_count}")
                        results.append(_entry_to_legacy_dict(entry, page_num))
                    else:
                        logger.debug(
                            f"[Page {page_num}] Skipped entry (filtered): {video_code} "
                            f"- Rate: {entry.rate}, Comments: {entry.comment_count}")
                except (ValueError, TypeError):
                    logger.debug(
                        f"[Page {page_num}] Skipped entry (invalid data): {video_code} "
                        f"- Rate: {entry.rate}, Comments: {entry.comment_count}")

    logger.debug(f"[Page {page_num}] Found {len(results)} entries for phase {phase}")
    return results
