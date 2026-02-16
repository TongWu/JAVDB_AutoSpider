"""
Shared parsing utilities used by both index and detail parsers.
"""

from __future__ import annotations

import re
import logging
from typing import Tuple, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from api.models import MovieLink

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate / comment extraction
# ---------------------------------------------------------------------------

def extract_rate_and_comments(score_text: str) -> Tuple[str, str]:
    """Extract numeric rating and comment count from a score string.

    Expected formats (Traditional / Simplified / English variations):
        ``4.47分, 由595人評價``
        ``3.95分, 由191人評價``

    Returns:
        (rate, comment_count) – both as strings, empty when not found.
    """
    rate = ''
    comment_count = ''

    rate_match = re.search(r'(\d+\.?\d*)分', score_text)
    if rate_match:
        rate = rate_match.group(1)

    comment_match = re.search(r'由(\d+)人評價', score_text)
    if comment_match:
        comment_count = comment_match.group(1)

    return rate, comment_count


# ---------------------------------------------------------------------------
# MovieLink helpers
# ---------------------------------------------------------------------------

def extract_movie_link(a_tag: Tag) -> Optional[MovieLink]:
    """Build a ``MovieLink`` from a ``<a>`` tag.  Returns *None* if the tag
    is missing or has no text."""
    if not a_tag or not isinstance(a_tag, Tag):
        return None
    name = a_tag.get_text(strip=True)
    href = a_tag.get('href', '')
    if not name:
        return None
    return MovieLink(name=name, href=href)


def extract_all_movie_links(parent_tag: Tag) -> list:
    """Return a list of ``MovieLink`` for every ``<a>`` inside *parent_tag*."""
    links = []
    if not parent_tag or not isinstance(parent_tag, Tag):
        return links
    for a in parent_tag.find_all('a'):
        ml = extract_movie_link(a)
        if ml:
            links.append(ml)
    return links


# ---------------------------------------------------------------------------
# Video-code extraction (ported from utils/parser.py)
# ---------------------------------------------------------------------------

def extract_video_code(a_tag: Tag) -> str:
    """Extract the video code from a movie-card ``<a class="box">`` tag.

    Video codes that do not contain ``-`` are considered invalid and an
    empty string is returned.
    """
    video_title_div = a_tag.find('div', class_='video-title')
    if video_title_div:
        strong_tag = video_title_div.find('strong')
        if strong_tag:
            video_code = strong_tag.get_text(strip=True)
        else:
            video_code = video_title_div.get_text(strip=True)

        if '-' not in video_code:
            logger.debug("Skipping invalid video code (no '-'): %s", video_code)
            return ''
        return video_code

    logger.debug("No video-title div found")
    return ''


# ---------------------------------------------------------------------------
# Page-type detection
# ---------------------------------------------------------------------------

_PAGE_TYPE_PATTERNS = {
    'top250': re.compile(r'/rankings/top'),
    'top_movies': re.compile(r'/rankings/movies'),
    'top_playback': re.compile(r'/rankings/playback'),
    'actors': re.compile(r'/actors/'),
    'makers': re.compile(r'/makers/'),
    'publishers': re.compile(r'/publishers/'),
    'series': re.compile(r'/series/'),
    'directors': re.compile(r'/directors/'),
    'video_codes': re.compile(r'/video_codes/'),
    'tags': re.compile(r'/tags'),
}


def detect_page_type(html_content: str) -> str:
    """Attempt to determine what kind of JavDB page the HTML represents.

    Returns one of:
        ``'index'``, ``'detail'``, ``'actors'``, ``'makers'``,
        ``'publishers'``, ``'series'``, ``'directors'``, ``'video_codes'``,
        ``'tags'``, ``'top250'``, ``'top_movies'``, ``'top_playback'``,
        or ``'unknown'``.
    """
    # Check the canonical URL or the "saved from" comment
    # Handles formats like: href="https://...", url=(0038)https://..., etc.
    url_match = re.search(r'(?:href|url)=["\']?(?:\(\d+\))?(https?://[^"\'>\s)]+)', html_content[:3000])
    if url_match:
        url = url_match.group(1)
        for page_type, pattern in _PAGE_TYPE_PATTERNS.items():
            if pattern.search(url):
                return page_type

    # Check for detail-page markers
    if 'magnets-content' in html_content[:50000] or 'video-meta-panel' in html_content[:50000]:
        return 'detail'

    # Fallback: if there is a movie-list, it is some kind of index page
    if 'movie-list' in html_content[:50000]:
        return 'index'

    return 'unknown'


# ---------------------------------------------------------------------------
# Category / section name extraction
# ---------------------------------------------------------------------------

def extract_category_name(soup: BeautifulSoup) -> Tuple[str, str]:
    """Extract the category type and display name from the page.

    Returns:
        (category_type, category_name)
    """
    # Actor pages use a dedicated class
    actor_span = soup.find('span', class_='actor-section-name')
    if actor_span:
        return 'actors', actor_span.get_text(strip=True)

    # Other category pages (makers, publishers, series, directors, video_codes)
    section_span = soup.find('span', class_='section-name')
    if section_span:
        return '', section_span.get_text(strip=True)

    # Fallback: try the page title
    title_tag = soup.find('title')
    if title_tag:
        title_text = title_tag.get_text(strip=True)
        # Remove the site suffix
        title_text = re.sub(r'\s*\|\s*JavDB.*$', '', title_text).strip()
        return '', title_text

    return '', ''
