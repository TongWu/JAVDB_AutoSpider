"""
Shared parsing utilities used by both index and detail parsers.
"""

from __future__ import annotations

import json
import re
import logging
from typing import Tuple, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from apps.api.models import MovieLink

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate / comment extraction
# ---------------------------------------------------------------------------

def extract_rate_and_comments(score_text: str) -> Tuple[str, str]:
    """Extract numeric rating and comment count from a score string.

    Expected formats:
        Traditional Chinese: ``4.47分, 由595人評價``
        English:             ``4.2, by 101 users``

    Returns:
        (rate, comment_count) – both as strings, empty when not found.
    """
    rate = ''
    comment_count = ''

    rate_match = re.search(r'(\d+\.?\d*)分', score_text)
    if not rate_match:
        rate_match = re.search(r'(\d+\.?\d*),\s*by\b', score_text)
    if rate_match:
        rate = rate_match.group(1)

    comment_match = re.search(r'由(\d+)人評價', score_text)
    if not comment_match:
        comment_match = re.search(r'by\s+(\d+)\s+users?', score_text)
    if comment_match:
        comment_count = comment_match.group(1)

    return rate, comment_count


# ---------------------------------------------------------------------------
# MovieLink helpers
# ---------------------------------------------------------------------------

def normalize_javdb_href_path(href: str) -> str:
    """Turn actor/director links into a site path like ``/actors/xyz``.

    Absolute ``https://javdb.com/...`` URLs become their ``path``; relative
    paths get a leading ``/`` when missing.
    """
    if not href:
        return ''
    h = href.strip()
    if h.startswith('http://') or h.startswith('https://'):
        path = urlparse(h).path or ''
        if not path:
            return ''
        return path if path.startswith('/') else f'/{path}'
    return h if h.startswith('/') else (f'/{h}' if h else '')


def javdb_absolute_url(href_or_path: str, base_url: str) -> str:
    """Return an absolute JavDB URL for a site path or JavDB absolute URL.

    Non-site links like ``magnet:`` are returned unchanged.
    """
    if not href_or_path:
        return ''
    h = href_or_path.strip()
    if not h:
        return ''
    if h.startswith('magnet:'):
        return h
    path = normalize_javdb_href_path(h)
    if not path:
        return ''
    return f"{base_url.rstrip('/')}{path}"


def movie_href_lookup_values(href: str, base_url: str) -> Tuple[str, str]:
    """Return both path-form and absolute-form values for Href lookup."""
    path = normalize_javdb_href_path(href)
    if not path:
        return '', ''
    return path, javdb_absolute_url(path, base_url)


def absolutize_supporting_actors_json(json_str: str, base_url: str) -> str:
    """Absolutize supporting-actor URL keys in JSON array payload.

    Handles both ``link`` (current writer) and ``href`` (legacy/imported).
    """
    if not json_str:
        return json_str
    raw = json_str.strip()
    if not raw:
        return json_str
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Invalid SupportingActors JSON, keep original payload")
        return json_str
    if not isinstance(payload, list):
        logger.warning("SupportingActors is not a JSON array, keep original payload")
        return json_str

    for item in payload:
        if not isinstance(item, dict):
            continue
        for key in ('link', 'href'):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                item[key] = javdb_absolute_url(value, base_url)
    return json.dumps(payload, ensure_ascii=False)


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

def _is_plausible_video_code(raw: str) -> bool:
    """Heuristic: accept classic ``ABC-123`` codes and hyphen-less studio codes (e.g. ``n0656``).

    Rejects empty strings and bare letter-only blobs (e.g. a mistaken title fragment).
    """
    s = (raw or '').strip()
    if len(s) < 2:
        return False
    if '-' in s:
        return True
    # Hyphen-less codes must mix letters and digits so we do not treat e.g. "NODASH" as a code.
    has_letter = any(c.isalpha() for c in s)
    has_digit = any(c.isdigit() for c in s)
    return has_letter and has_digit


def extract_video_code(a_tag: Tag) -> str:
    """Extract the video code from a movie-card ``<a class="box">`` tag.

    Accepts standard hyphenated codes and hyphen-less codes when they contain
    both letters and digits (e.g. ``n0656``). Other values return an empty string.
    """
    video_title_div = a_tag.find('div', class_='video-title')
    if video_title_div:
        strong_tag = video_title_div.find('strong')
        if strong_tag:
            video_code = strong_tag.get_text(strip=True)
        else:
            video_code = video_title_div.get_text(strip=True)

        if not _is_plausible_video_code(video_code):
            logger.debug("Skipping invalid or implausible video code: %s", video_code)
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
    'search': re.compile(r'/search(?:\?|$)'),
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
