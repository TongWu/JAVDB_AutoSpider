"""
HTML parsing utilities for the spider.

.. note::

    This module is a **backward-compatible wrapper** around the new
    ``api.parsers`` layer.  New code should prefer importing from
    ``api.parsers`` directly.

The public interface (``extract_video_code``, ``parse_index``,
``parse_detail``) is preserved so that ``spider.py`` and existing tests
continue to work without modification.
"""

import re
import logging
import time
from bs4 import BeautifulSoup
from bs4.element import Tag

# Import configuration
try:
    from config import DETAIL_PAGE_SLEEP, PHASE2_MIN_RATE, PHASE2_MIN_COMMENTS, LOG_LEVEL, IGNORE_RELEASE_DATE_FILTER
except ImportError:
    # Fallback values if config.py doesn't exist
    DETAIL_PAGE_SLEEP = 5
    PHASE2_MIN_RATE = 4.0
    PHASE2_MIN_COMMENTS = 100
    LOG_LEVEL = 'INFO'
    IGNORE_RELEASE_DATE_FILTER = False

from utils.logging_config import get_logger, setup_logging

# New API layer imports
from api.parsers.index_parser import parse_index_page as _api_parse_index
from api.parsers.detail_parser import parse_detail_page as _api_parse_detail

setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Subtitle / magnet / release-date tag helpers
# ---------------------------------------------------------------------------

_SUBTITLE_TAGS = frozenset(['含中字磁鏈', '含中字磁链', 'CnSub DL'])
_MAGNET_TAGS = frozenset(['含磁鏈', '含磁链', 'DL'])
_RELEASE_DATE_TAGS = frozenset([
    '今日新種', '昨日新種',
    '今日新种', '昨日新种',
    'Today', 'Yesterday',
])


def _has_subtitle(tags: list) -> bool:
    return bool(_SUBTITLE_TAGS.intersection(tags))


def _has_magnet(tags: list) -> bool:
    return bool(_MAGNET_TAGS.intersection(tags)) or _has_subtitle(tags)


def _has_release_date(tags: list) -> bool:
    return bool(_RELEASE_DATE_TAGS.intersection(tags))


# ---------------------------------------------------------------------------
# extract_video_code  (delegates to api.parsers.common)
# ---------------------------------------------------------------------------

def extract_video_code(a):
    """Extract video code from movie item with improved robustness.

    .. deprecated:: Use ``api.parsers.common.extract_video_code`` instead.

    Returns:
        video_code: The extracted video code, or empty string if not found
                    or invalid.  Video codes without ``-`` are considered
                    invalid and will return empty string.
    """
    from api.parsers.common import extract_video_code as _api_extract
    return _api_extract(a)


# ---------------------------------------------------------------------------
# parse_index  (delegates to api.parsers + applies business filters)
# ---------------------------------------------------------------------------

def parse_index(html_content, page_num, phase=1, disable_new_releases_filter=False, is_adhoc_mode=False):
    """Parse the index page to extract entries with required tags.

    This function delegates HTML parsing to ``api.parsers.parse_index_page``
    and then applies the spider's business filtering logic (phase selection,
    subtitle/release-date tags, rate/comment thresholds).

    Args:
        html_content: HTML content to parse
        page_num: Current page number
        phase: 1 for subtitle entries, 2 for non-subtitle entries
        disable_new_releases_filter: If True, disable release date filter
            but keep other filters
        is_adhoc_mode: If True, disable ALL filters and process ALL entries
            (for custom URL mode)
    """
    # Use the new API parser to get ALL entries (no filtering)
    page_result = _api_parse_index(html_content, page_num)

    if not page_result.has_movie_list:
        logger.warning(f'[Page {page_num}] No movie list found!')
        # Preserve the original debug output for diagnostics
        soup = BeautifulSoup(html_content, 'html.parser')
        logger.debug(f'[Page {page_num}] HTML content length: {len(html_content)}')
        title_tag = soup.find('title')
        page_title = title_tag.get_text() if title_tag else "No title"
        logger.debug(f'[Page {page_num}] Page title: {page_title}')
        return []

    logger.debug(f"[Page {page_num}] Found movie list container")
    logger.debug(f"[Page {page_num}] Parsing index page for phase {phase}...")

    results = []

    for entry in page_result.movies:
        tags = entry.tags
        video_code = entry.video_code

        # Skip entries without a valid video code
        if not video_code:
            continue

        logger.debug(f"[Page {page_num}] Found tags: {tags}")

        # Build the legacy result dict from the API entry
        def _to_result(e):
            return {
                'href': e.href,
                'video_code': e.video_code,
                'page': page_num,
                'actor': '',  # Will be filled from detail page
                'rate': e.rate,
                'comment_number': e.comment_count,
            }

        # ---- AD HOC MODE ----
        if is_adhoc_mode:
            has_sub = _has_subtitle(tags)
            has_mag = _has_magnet(tags)

            if not has_mag:
                logger.debug(f"[Page {page_num}] Skipping entry without magnet link (no magnet tag in HTML)")
                continue

            if phase == 1 and has_sub:
                logger.debug(f"[Page {page_num}] Found entry (adhoc P1): {video_code} ({entry.href})")
                results.append(_to_result(entry))
            elif phase == 2 and not has_sub:
                logger.debug(f"[Page {page_num}] Found entry (adhoc P2): {video_code} ({entry.href})")
                results.append(_to_result(entry))
            continue

        # ---- PHASE 1 ----
        if phase == 1:
            if disable_new_releases_filter:
                if _has_subtitle(tags):
                    logger.debug(f"[Page {page_num}] Found entry (filter disabled): {video_code} ({entry.href})")
                    results.append(_to_result(entry))
            else:
                has_sub = _has_subtitle(tags)
                has_rd = _has_release_date(tags)
                if has_sub and (IGNORE_RELEASE_DATE_FILTER or has_rd):
                    logger.debug(f"[Page {page_num}] Found entry: {video_code} ({entry.href})")
                    results.append(_to_result(entry))

        # ---- PHASE 2 ----
        elif phase == 2:
            if _has_subtitle(tags):
                # Already handled in phase 1
                continue

            should_process = False
            if disable_new_releases_filter:
                should_process = True
            else:
                has_rd = _has_release_date(tags)
                if IGNORE_RELEASE_DATE_FILTER or has_rd:
                    should_process = True

            if should_process:
                # Apply rate/comment thresholds for phase 2
                try:
                    comment_num = int(entry.comment_count) if entry.comment_count else 0
                    rate_num = float(entry.rate) if entry.rate else 0

                    if comment_num >= PHASE2_MIN_COMMENTS and rate_num >= PHASE2_MIN_RATE:
                        logger.debug(
                            f"[Page {page_num}] Found entry: {video_code} ({entry.href}) "
                            f"- Rate: {entry.rate}, Comments: {entry.comment_count}")
                        results.append(_to_result(entry))
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


# ---------------------------------------------------------------------------
# parse_detail  (delegates to api.parsers + returns legacy tuple)
# ---------------------------------------------------------------------------

def parse_detail(html_content, index=None, skip_sleep=False):
    """Parse the detail page to extract magnet links and actor information.

    This function delegates HTML parsing to ``api.parsers.parse_detail_page``
    and converts the result to the legacy tuple format.

    Note: video_code is extracted from the index/catalog page, not from
    the detail page.

    Args:
        html_content: HTML content of the detail page
        index: Index number for logging prefix
        skip_sleep: If True, skip the sleep delay (used during fallback
            retries)

    Returns:
        tuple: (magnets, actor_info, parse_success)
            - magnets: List of magnet link dictionaries
            - actor_info: Actor name string
            - parse_success: True if magnets_content was found
    """
    # Respect the rate-limit sleep (the API layer does NOT sleep)
    if not skip_sleep:
        time.sleep(DETAIL_PAGE_SLEEP)

    prefix = f"[{index}]" if index is not None else ""

    # Delegate to the new API parser
    detail = _api_parse_detail(html_content)

    # Convert actor info
    actor_info = detail.get_first_actor_name()
    if actor_info:
        logger.debug(f"{prefix} Found actor: {actor_info}")

    # Convert magnets to legacy dict format
    magnets = detail.get_magnets_as_legacy()

    parse_success = detail.parse_success
    if not parse_success:
        logger.debug(f"{prefix} No magnets content found in detail page")

    logger.debug(f"{prefix} Found {len(magnets)} magnet links")
    return magnets, actor_info, parse_success
