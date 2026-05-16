"""Filename generation utilities for JavDB spider output CSV files.

Provides functions to generate descriptive CSV filenames from custom URLs,
with optional HTML-based name resolution for actors, makers, etc.
Pure string/URL operations delegate to ``utils.domain.url_helper`` which uses Rust
when available.
"""

import logging
from datetime import datetime

from bs4 import BeautifulSoup
from apps.api.parsers.common import extract_category_name as _api_extract_category_name
from packages.python.javdb_core.url_helper import (
    detect_url_type,
    extract_url_identifier,
    sanitize_filename_part,
    extract_url_part_after_javdb,
)

logger = logging.getLogger(__name__)


def parse_actor_name_from_html(html_content):
    """Extract actor name from JavDB actor page HTML."""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        _cat_type, cat_name = _api_extract_category_name(soup)
        if cat_name:
            return cat_name
    except Exception as e:
        logger.warning(f"Error parsing actor name from HTML: {e}")
    return None


def parse_section_name_from_html(html_content):
    """Extract section name from any JavDB category page HTML."""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        _cat_type, cat_name = _api_extract_category_name(soup)
        if cat_name:
            return cat_name
    except Exception as e:
        logger.warning(f"Error parsing section name from HTML: {e}")
    return None


def generate_output_csv_name_from_html(custom_url, index_html):
    """Generate CSV filename using display name extracted from fetched HTML.

    Called after the first index page is fetched so no extra request is needed.
    """
    today_date = datetime.now().strftime("%Y%m%d")
    url_type = detect_url_type(custom_url)
    display_name = None
    raw_name = None

    if url_type == 'actors':
        raw_name = parse_actor_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted actor name from index page: {raw_name}")
    elif url_type in ('makers', 'publishers', 'series', 'directors'):
        raw_name = parse_section_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted {url_type} name from index page: {raw_name}")
    elif url_type == 'video_codes':
        raw_name = parse_section_name_from_html(index_html)
        if raw_name:
            display_name = sanitize_filename_part(raw_name)
            logger.info(f"[AdHoc] Successfully extracted video code from index page: {raw_name}")
        else:
            url_id = extract_url_identifier(custom_url)
            if url_id:
                display_name = sanitize_filename_part(url_id)
                logger.info(f"[AdHoc] Extracted video code from URL: {url_id}")

    if display_name:
        csv_filename = f'Javdb_AdHoc_{url_type}_{display_name}_{today_date}.csv'
        logger.info(f"[AdHoc] URL type: {url_type}, Display name: {display_name}")
        logger.info(f"[AdHoc] Generated CSV filename: {csv_filename}")
        return csv_filename

    url_part = extract_url_part_after_javdb(custom_url)
    csv_filename = f'Javdb_AdHoc_{url_part}_{today_date}.csv'
    logger.warning(f"[AdHoc] Could not extract display name for URL type: {url_type}")
    logger.info(f"[AdHoc] Fallback CSV filename: {csv_filename}")
    return csv_filename


def generate_output_csv_name(custom_url=None, use_proxy=False):
    """Generate CSV filename from custom URL (without fetching HTML).

    For adhoc mode with a custom URL this produces a temporary filename based
    on URL structure.  The real display name is resolved later via
    ``generate_output_csv_name_from_html`` once the first index page is fetched.
    """
    if custom_url:
        today_date = datetime.now().strftime("%Y%m%d")
        url_type = detect_url_type(custom_url)

        if url_type == 'video_codes':
            url_id = extract_url_identifier(custom_url)
            if url_id:
                display_name = sanitize_filename_part(url_id)
                csv_filename = f'Javdb_AdHoc_{url_type}_{display_name}_{today_date}.csv'
                logger.info(f"[AdHoc] URL type: {url_type}, Display name: {display_name}")
                logger.info(f"[AdHoc] Generated CSV filename: {csv_filename}")
                return csv_filename

        url_part = extract_url_part_after_javdb(custom_url)
        csv_filename = f'Javdb_AdHoc_{url_part}_{today_date}.csv'
        logger.info(f"[AdHoc] Temporary CSV filename (will resolve display name after fetching index page): {csv_filename}")
        return csv_filename

    return f'Javdb_TodayTitle_{datetime.now().strftime("%Y%m%d")}.csv'
