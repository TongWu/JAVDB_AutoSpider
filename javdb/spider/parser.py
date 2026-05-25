"""Compatibility shim for the legacy parser adapter.

DELETED in IMP-ADR011-03 Task 4. New code must import from
``javdb.spider.html_validators`` and ``javdb.spider.parse_legacy_adapters``.

This shim keeps ``parse_index``/``parse_detail`` defined inline (rather
than re-exported) so legacy tests that monkeypatch
``select_index_entries`` on this module's namespace continue to work
until Task 4 deletes both the shim and the tests targeting it.
"""

from bs4 import BeautifulSoup

from javdb.infra.logging import get_logger
from javdb.parsing import (
    parse_detail_page as _api_parse_detail,
    parse_index_page as _api_parse_index,
)
from javdb.parsing.common import extract_video_code as _api_extract_video_code
from javdb.pipeline.index_selection import select_index_entries
from javdb.spider.html_validators import (
    RUST_PARSER_EXTRAS_AVAILABLE,
    is_login_page,
    is_maintenance_page,
    result_to_dict,
    validate_index_html,
)

logger = get_logger(__name__)


def extract_video_code(a):
    return _api_extract_video_code(a)


def parse_index(html_content, page_num, phase=1, disable_new_releases_filter=False, is_adhoc_mode=False):
    """Legacy shim — see ``javdb.spider.parse_legacy_adapters.parse_index``."""
    page_result = _api_parse_index(html_content, page_num)
    if not page_result.has_movie_list:
        logger.warning(f'[Page {page_num}] No movie list found!')
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
    """Legacy shim — see ``javdb.spider.parse_legacy_adapters.parse_detail``."""
    prefix = f"[{index}]" if index is not None else ""

    detail = _api_parse_detail(html_content)

    actor_info = detail.get_first_actor_name()
    actor_gender = detail.get_first_actor_gender()
    actor_link = detail.get_first_actor_href()
    supporting_actors = detail.get_supporting_actors_json()
    if actor_info:
        logger.debug(f"{prefix} Found actor: {actor_info}")

    magnets = detail.get_magnets_as_legacy()

    parse_success = detail.parse_success
    if not parse_success:
        logger.debug(f"{prefix} No magnets content found in detail page")

    logger.debug(f"{prefix} Found {len(magnets)} magnet links")
    return magnets, actor_info, actor_gender, actor_link, supporting_actors, parse_success


__all__ = [
    "RUST_PARSER_EXTRAS_AVAILABLE",
    "is_login_page",
    "is_maintenance_page",
    "result_to_dict",
    "validate_index_html",
    "extract_video_code",
    "parse_detail",
    "parse_index",
    "select_index_entries",
]
