"""
Enhanced index-page parser for all listing pages (normal index, category,
top/ranking pages).

This parser extracts **all** movie entries without applying any business
filters (no phase logic, no subtitle/release-date filtering).  Filtering is
the responsibility of the caller (e.g. ``spider.py``).
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from api.models import (
    MovieIndexEntry,
    IndexPageResult,
    CategoryPageResult,
    TopPageResult,
)
from api.parsers.common import (
    extract_video_code,
    extract_rate_and_comments,
    extract_category_name,
    detect_page_type,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_movie_item(item: Tag, page_num: int) -> Optional[MovieIndexEntry]:
    """Parse a single ``<div class="item">`` into a *MovieIndexEntry*.

    Returns *None* when the item cannot be parsed (missing link, etc.).
    """
    a = item.find('a', class_='box')
    # Some pages (e.g. the recommendation section) use plain <a> without
    # class="box".  We still try to parse them.
    if not a:
        a = item.find('a')
    if not a or not isinstance(a, Tag):
        return None

    href = a.get('href', '')
    if not href:
        return None

    # --- video code ---
    video_code = extract_video_code(a)

    # --- title ---
    title = ''
    video_title_div = a.find('div', class_='video-title')
    if video_title_div:
        # Full text contains "[CODE] Title …"
        full_text = video_title_div.get_text(strip=True)
        # Remove leading video code to get the title portion
        if video_code and full_text.startswith(video_code):
            title = full_text[len(video_code):].strip()
        else:
            title = full_text
    # Fallback: use the <a> tag's title attribute
    if not title:
        title = a.get('title', '')

    # --- rating & comment count ---
    rate = ''
    comment_count = ''
    score_div = a.find('div', class_='score')
    if score_div:
        value_span = score_div.find('span', class_='value')
        # Some pages use <div class="value"> instead of <span>
        if not value_span:
            value_span = score_div.find('div', class_='value')
        if value_span:
            score_text = value_span.get_text(strip=True)
            rate, comment_count = extract_rate_and_comments(score_text)

    # --- release date ---
    release_date = ''
    meta_div = a.find('div', class_='meta')
    if meta_div:
        release_date = meta_div.get_text(strip=True)

    # --- tags (今日新種, 含中字磁鏈, etc.) ---
    tags = []
    tags_div = a.find('div', class_='tags has-addons')
    if tags_div and isinstance(tags_div, Tag):
        for span in tags_div.find_all('span', class_='tag'):
            if isinstance(span, Tag):
                tag_text = span.get_text(strip=True)
                if tag_text:
                    tags.append(tag_text)

    # --- cover image URL ---
    cover_url = ''
    cover_div = a.find('div', class_=lambda c: c and 'cover' in c)
    if cover_div:
        img = cover_div.find('img')
        if img:
            cover_url = img.get('src', '') or img.get('data-src', '')

    # --- ranking (only present on Top pages) ---
    ranking = None
    if cover_div:
        ranking_span = cover_div.find('span', class_='ranking')
        if ranking_span:
            try:
                ranking = int(ranking_span.get_text(strip=True))
            except (ValueError, TypeError):
                pass

    return MovieIndexEntry(
        href=href,
        video_code=video_code,
        title=title,
        rate=rate,
        comment_count=comment_count,
        release_date=release_date,
        tags=tags,
        cover_url=cover_url,
        page=page_num,
        ranking=ranking,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_index_page(html_content: str, page_num: int = 1) -> IndexPageResult:
    """Parse any movie-list index page and return **all** entries.

    This function does NOT apply any business filtering (phase, subtitle,
    release-date, rate thresholds, etc.).  The caller is responsible for
    post-processing the result.

    Works for:
    - Normal index / home page
    - Category pages (maker, publisher, series, director, video_codes, tags)
    - Top / ranking pages
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # Page title
    title_tag = soup.find('title')
    page_title = title_tag.get_text(strip=True) if title_tag else ''

    # Find ALL movie-list containers on the page.
    # Some pages (e.g. the home page) have multiple: a recommendation
    # section and the main listing.  We parse items from all of them.
    movie_lists = soup.find_all('div', class_=lambda x: x and 'movie-list' in x)
    if not movie_lists:
        logger.warning('[Page %d] No movie list found', page_num)
        return IndexPageResult(has_movie_list=False, page_title=page_title)

    movies = []
    for movie_list in movie_lists:
        for item in movie_list.find_all('div', class_='item'):
            entry = _parse_movie_item(item, page_num)
            if entry is not None:
                movies.append(entry)

    logger.debug('[Page %d] Parsed %d movie entries', page_num, len(movies))
    return IndexPageResult(
        has_movie_list=True,
        movies=movies,
        page_title=page_title,
    )


def parse_category_page(html_content: str, page_num: int = 1) -> CategoryPageResult:
    """Parse a category page (actors, makers, publishers, series, directors,
    video_codes, tags) and return entries plus category metadata."""
    soup = BeautifulSoup(html_content, 'html.parser')
    base = parse_index_page(html_content, page_num)

    cat_type, cat_name = extract_category_name(soup)

    # Try to infer category type from URL embedded in the HTML
    if not cat_type:
        page_type = detect_page_type(html_content)
        if page_type not in ('index', 'detail', 'unknown'):
            cat_type = page_type

    return CategoryPageResult(
        has_movie_list=base.has_movie_list,
        movies=base.movies,
        page_title=base.page_title,
        category_type=cat_type,
        category_name=cat_name,
    )


def parse_top_page(html_content: str, page_num: int = 1) -> TopPageResult:
    """Parse a top/ranking page and return entries with ranking info."""
    base = parse_index_page(html_content, page_num)

    # Determine the top type and period from the page
    top_type = ''
    period = None
    page_type = detect_page_type(html_content)

    if page_type == 'top250':
        top_type = 'top250'
        # Extract year from URL (e.g. ?t=y2025)
        year_match = re.search(r'[?&]t=y(\d{4})', html_content[:5000])
        if year_match:
            period = year_match.group(1)
    elif page_type in ('top_movies', 'top_playback'):
        top_type = page_type
        # Extract period (daily, weekly, monthly)
        period_match = re.search(r'[?&]p=(daily|weekly|monthly)', html_content[:5000])
        if period_match:
            period = period_match.group(1)

    return TopPageResult(
        has_movie_list=base.has_movie_list,
        movies=base.movies,
        page_title=base.page_title,
        top_type=top_type,
        period=period,
    )
