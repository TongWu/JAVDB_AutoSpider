"""
Temporary compatibility adapter for spider parsing.

This module exists only to preserve the legacy ``javdb.spider.parser``
imports while callers are migrated to ``javdb.parsing`` and
``javdb.pipeline.index_selection``. It is expected to be deleted by
IMP-ADR011-03.
"""

from typing import Any, Tuple
from bs4 import BeautifulSoup

from javdb.infra.logging import get_logger
from javdb.spider import _parser_support as _support

from javdb.parsing import (
    parse_index_page as _api_parse_index,
    parse_detail_page as _api_parse_detail,
)
from javdb.parsing.common import extract_video_code as _api_extract_video_code
from javdb.pipeline.index_selection import select_index_entries

try:
    from javdb.rust_core import is_login_page as _rust_is_login_page
    from javdb.rust_core import validate_index_html as _rust_validate_index_html

    RUST_PARSER_EXTRAS_AVAILABLE = True
except ImportError:
    RUST_PARSER_EXTRAS_AVAILABLE = False
    _rust_is_login_page = None
    _rust_validate_index_html = None


def is_maintenance_page(html: str) -> bool:
    return _support.is_maintenance_page(html)


def result_to_dict(result: Any) -> dict:
    return _support.result_to_dict(result)


def is_login_page(html: str) -> bool:
    return _support.is_login_page(
        html,
        rust_parser_extras_available=RUST_PARSER_EXTRAS_AVAILABLE,
        rust_is_login_page=_rust_is_login_page,
    )


def validate_index_html(html: str) -> Tuple[bool, bool]:
    return _support.validate_index_html(
        html,
        rust_parser_extras_available=RUST_PARSER_EXTRAS_AVAILABLE,
        rust_validate_index_html=_rust_validate_index_html,
    )


logger = get_logger(__name__)


def extract_video_code(a):
    return _api_extract_video_code(a)


def parse_index(html_content, page_num, phase=1, disable_new_releases_filter=False, is_adhoc_mode=False):
    """Parse the index page to extract entries with required tags.

    This function delegates HTML parsing to ``javdb.parsing.parse_index_page``
    and then applies the spider's business filtering logic in
    ``javdb.pipeline.index_selection``.

    Args:
        html_content: HTML content to parse
        page_num: Current page number
        phase: 1 for subtitle entries, 2 for non-subtitle entries
        disable_new_releases_filter: If True, disable release date filter
            but keep other filters
        is_adhoc_mode: If True, bypass only the new-release date filter for
            custom URL mode. Entry selection still delegates to
            ``select_index_entries()``, so phase subtitle/non-subtitle
            filtering and magnet-tag skips still apply.
    """
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

    return select_index_entries(
        page_result,
        page_num=page_num,
        phase=phase,
        disable_new_releases_filter=disable_new_releases_filter,
        is_adhoc_mode=is_adhoc_mode,
    )


def parse_detail(html_content, index=None, skip_sleep=False):
    """Parse the detail page to extract magnet links and actor information.

    This function delegates HTML parsing to ``javdb.parsing.parse_detail_page``
    and converts the result to the legacy tuple format.

    Note: video_code is extracted from the index/catalog page, not from
    the detail page.

    Args:
        html_content: HTML content of the detail page
        index: Index number for logging prefix
        skip_sleep: Deprecated, kept for backward compatibility. Rate-limit
            sleep is now handled exclusively by the caller (spider.py via
            ``MOVIE_SLEEP_MIN`` / ``MOVIE_SLEEP_MAX``).

    Returns:
        tuple: (magnets, actor_info, actor_gender, actor_link, supporting_actors, parse_success)
            - magnets: List of magnet link dictionaries
            - actor_info: Lead (first) actor name
            - actor_gender: ``female`` / ``male`` / ``''``
            - actor_link: Lead actor href as site path (e.g. ``/actors/...``)
            - supporting_actors: JSON array string for supporting cast (DB column);
              ``[]`` when there is no supporting cast, including a single lead actor only
            - parse_success: True if magnets_content was found
    """
    prefix = f"[{index}]" if index is not None else ""

    # Delegate to the new API parser
    detail = _api_parse_detail(html_content)

    # Convert actor info (lead + supporting JSON)
    actor_info = detail.get_first_actor_name()
    actor_gender = detail.get_first_actor_gender()
    actor_link = detail.get_first_actor_href()
    supporting_actors = detail.get_supporting_actors_json()
    if actor_info:
        logger.debug(f"{prefix} Found actor: {actor_info}")

    # Convert magnets to legacy dict format
    magnets = detail.get_magnets_as_legacy()

    parse_success = detail.parse_success
    if not parse_success:
        logger.debug(f"{prefix} No magnets content found in detail page")

    logger.debug(f"{prefix} Found {len(magnets)} magnet links")
    return magnets, actor_info, actor_gender, actor_link, supporting_actors, parse_success
