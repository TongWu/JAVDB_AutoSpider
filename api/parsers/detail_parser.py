"""
Enhanced detail-page parser.

Extracts the full set of metadata from a single movie's detail page
including title, code, duration, maker/publisher, series, directors, tags,
rating, poster, fanart, trailer, actors, magnets, review stats, etc.

No ``time.sleep`` calls are made – the caller controls request pacing.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from bs4 import BeautifulSoup
from bs4.element import Tag

from api.models import MovieDetail, MovieLink, MagnetInfo
from api.parsers.common import (
    extract_rate_and_comments,
    extract_movie_link,
    extract_all_movie_links,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_panel_block(panel_blocks, label: str) -> Optional[Tag]:
    """Find the ``<div class="panel-block">`` whose ``<strong>`` contains
    *label* (e.g. ``"片商:"``).
    """
    for block in panel_blocks:
        strong = block.find('strong')
        if strong and label in strong.get_text():
            return block
    return None


def _extract_link_from_panel(panel_blocks, label: str) -> Optional[MovieLink]:
    """Extract a single ``MovieLink`` from a panel-block identified by *label*."""
    block = _find_panel_block(panel_blocks, label)
    if not block:
        return None
    value_span = block.find('span', class_='value')
    if not value_span:
        return None
    a_tag = value_span.find('a')
    return extract_movie_link(a_tag)


def _extract_links_from_panel(panel_blocks, label: str) -> list:
    """Extract all ``MovieLink`` objects from a panel-block."""
    block = _find_panel_block(panel_blocks, label)
    if not block:
        return []
    value_span = block.find('span', class_='value')
    if not value_span:
        return []
    return extract_all_movie_links(value_span)


def _extract_text_from_panel(panel_blocks, label: str) -> str:
    """Extract plain text from a panel-block."""
    block = _find_panel_block(panel_blocks, label)
    if not block:
        return ''
    value_span = block.find('span', class_='value')
    if not value_span:
        return ''
    return value_span.get_text(strip=True)


def _parse_magnets(soup: BeautifulSoup) -> tuple:
    """Parse magnet links from ``#magnets-content``.

    Returns:
        (magnets_list, parse_success)
    """
    magnets_content = soup.find('div', id='magnets-content')
    if not magnets_content:
        return [], False

    magnets = []
    for item in magnets_content.find_all('div', class_=re.compile(r'item columns is-desktop')):
        magnet_name_div = item.find('div', class_='magnet-name')
        if not magnet_name_div:
            continue

        magnet_a = magnet_name_div.find('a')
        if not magnet_a:
            continue

        magnet_href = magnet_a.get('href', '')
        name_span = magnet_a.find('span', class_='name')
        name = name_span.get_text(strip=True) if name_span else ''

        # Size
        size = ''
        meta_span = magnet_a.find('span', class_='meta')
        if meta_span:
            meta_text = meta_span.get_text(strip=True)
            size_match = re.search(r'([\d.]+(?:GB|MB|KB|TB))', meta_text)
            if size_match:
                size = size_match.group(1)

        # Timestamp
        timestamp = ''
        time_span = item.find('span', class_='time')
        if time_span:
            timestamp = time_span.get_text(strip=True)

        # Tags
        tags = []
        tags_div = magnet_a.find('div', class_='tags')
        if tags_div and isinstance(tags_div, Tag):
            for span in tags_div.find_all('span', class_='tag'):
                if isinstance(span, Tag):
                    tags.append(span.get_text(strip=True))

        magnets.append(MagnetInfo(
            href=magnet_href,
            name=name,
            tags=tags,
            size=size,
            timestamp=timestamp,
        ))

    return magnets, True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_detail_page(html_content: str) -> MovieDetail:
    """Parse a movie detail page and return a fully-populated *MovieDetail*.

    Fields that are not present on the page are left at their default values
    (empty string / empty list / None / 0).
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    detail = MovieDetail()

    # --- Title (from <h2> containing <strong class="current-title">) ---
    title_strong = soup.find('strong', class_='current-title')
    if title_strong:
        detail.title = title_strong.get_text(strip=True)

    # --- Metadata panel ---
    video_meta_panel = soup.find('div', class_='video-meta-panel')
    panel_blocks = []
    if video_meta_panel:
        panel_blocks = video_meta_panel.find_all('div', class_='panel-block')

    # --- Video code + prefix link ---
    first_block = _find_panel_block(panel_blocks, '番號:')
    if first_block:
        value_span = first_block.find('span', class_='value')
        if value_span:
            # The full video code text (e.g. "VDD-201")
            detail.video_code = value_span.get_text(strip=True)
            # The prefix link (e.g. /video_codes/VDD)
            prefix_a = value_span.find('a')
            if prefix_a:
                detail.code_prefix_link = prefix_a.get('href', '')

    # --- Release date ---
    detail.release_date = _extract_text_from_panel(panel_blocks, '日期:')

    # --- Duration ---
    detail.duration = _extract_text_from_panel(panel_blocks, '時長:')

    # --- Directors ---
    detail.directors = _extract_links_from_panel(panel_blocks, '導演:')

    # --- Maker (片商) ---
    detail.maker = _extract_link_from_panel(panel_blocks, '片商:')

    # --- Publisher (發行商) – may not be present on all pages ---
    detail.publisher = _extract_link_from_panel(panel_blocks, '發行商:')

    # --- Series ---
    detail.series = _extract_link_from_panel(panel_blocks, '系列:')

    # --- Rating & comment count ---
    rating_block = _find_panel_block(panel_blocks, '評分:')
    if rating_block:
        value_span = rating_block.find('span', class_='value')
        if value_span:
            score_text = value_span.get_text(strip=True)
            detail.rate, detail.comment_count = extract_rate_and_comments(score_text)

    # --- Tags (類別) ---
    detail.tags = _extract_links_from_panel(panel_blocks, '類別:')

    # --- Actors (演員) ---
    detail.actors = _extract_links_from_panel(panel_blocks, '演員:')

    # --- Poster URL (cover image) ---
    if video_meta_panel:
        cover_col = video_meta_panel.find('div', class_='column-video-cover')
        if cover_col:
            cover_img = cover_col.find('img', class_='video-cover')
            if cover_img:
                detail.poster_url = cover_img.get('src', '')

    # --- Fanart URLs (sample images) ---
    tile_images = soup.find('div', class_='tile-images preview-images')
    if tile_images:
        for tile in tile_images.find_all('a', class_='tile-item'):
            href = tile.get('href', '')
            if href:
                detail.fanart_urls.append(href)

    # --- Trailer URL ---
    # The preview video container links to the video section
    preview_container = soup.find('a', class_='preview-video-container')
    if preview_container:
        # The actual video element
        video_el = soup.find('video', id='preview-video')
        if video_el:
            src = video_el.get('src', '')
            if src and not src.startswith('blob:'):
                detail.trailer_url = src
            else:
                # Try data-src or source element
                source = video_el.find('source')
                if source:
                    detail.trailer_url = source.get('src', '') or None
        # If no direct src, note the preview container href
        if not detail.trailer_url:
            container_href = preview_container.get('href', '')
            if container_href and '#preview-video' in container_href:
                # The trailer is loaded dynamically; store the base URL
                detail.trailer_url = container_href

    # --- Review count (from tabs "短評(N)") ---
    review_tab = soup.find('a', class_='review-tab')
    if review_tab:
        tab_text = review_tab.get_text(strip=True)
        review_match = re.search(r'短評\((\d+)\)', tab_text)
        if review_match:
            detail.review_count = int(review_match.group(1))

    # --- Want to watch / watched counts ---
    for block in panel_blocks:
        span = block.find('span', class_='is-size-7')
        if span:
            text = span.get_text(strip=True)
            want_match = re.search(r'(\d+)人想看', text)
            if want_match:
                detail.want_count = int(want_match.group(1))
            watched_match = re.search(r'(\d+)人看過', text)
            if watched_match:
                detail.watched_count = int(watched_match.group(1))

    # --- Magnets ---
    magnets, parse_success = _parse_magnets(soup)
    detail.magnets = magnets
    detail.parse_success = parse_success

    logger.debug(
        'Parsed detail: code=%s, title=%s, actors=%d, magnets=%d',
        detail.video_code,
        detail.title[:40] if detail.title else '',
        len(detail.actors),
        len(detail.magnets),
    )

    return detail
