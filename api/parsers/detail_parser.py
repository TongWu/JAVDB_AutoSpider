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

from api.models import ActorCredit, MovieDetail, MovieLink, MagnetInfo, NO_ACTOR_LISTING_ACTOR_NAME
from api.parsers.common import (
    extract_rate_and_comments,
    extract_movie_link,
    extract_all_movie_links,
)

logger = logging.getLogger(__name__)

# Plain-text values in 演員 span.value when there are no /actors/ links (normalised to NO_ACTOR_LISTING_ACTOR_NAME in DB).
_NO_ACTOR_LISTING_PLACEHOLDERS = frozenset({
    'N/A',
    'n/a',
    '无',
    '無',
    '—',
    '–',
    '-',
    '－',
    '暂无',
    '暂无演员',
})

# Label aliases for locale-independent panel-block matching.
# JavDB supports Traditional Chinese (zh) and English (en).  CF bypass may
# return the English locale when the headless browser lacks a locale cookie.
# Matching is exact (``strong.get_text(strip=True) == label``).
_L_CODE = ('番號:', 'ID:')
_L_DATE = ('日期:', 'Released Date:')
_L_DURATION = ('時長:', 'Duration:')
_L_DIRECTOR = ('導演:', 'Director:')
_L_MAKER = ('片商:', 'Maker:')
_L_PUBLISHER = ('發行商:', 'Publisher:')
_L_SERIES = ('系列:', 'Series:')
_L_RATING = ('評分:', 'Rating:')
_L_TAGS = ('類別:', 'Tags:')
_L_ACTOR = ('演員:', 'Actor(s):')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_panel_block(panel_blocks, label) -> Optional[Tag]:
    """Find the ``<div class="panel-block">`` whose ``<strong>`` text
    exactly matches *label*.

    *label* can be a single string (``"片商:"``) or a tuple of alternatives
    (``("片商:", "Maker:")``).  The first matching block is returned.
    """
    labels = (label,) if isinstance(label, str) else label
    for block in panel_blocks:
        strong = block.find('strong')
        if strong:
            text = strong.get_text(strip=True)
            if text in labels:
                return block
    return None


def _extract_link_from_panel(panel_blocks, label) -> Optional[MovieLink]:
    """Extract a single ``MovieLink`` from a panel-block identified by *label*."""
    block = _find_panel_block(panel_blocks, label)
    if not block:
        return None
    value_span = block.find('span', class_='value')
    if not value_span:
        return None
    a_tag = value_span.find('a')
    return extract_movie_link(a_tag)


def _extract_links_from_panel(panel_blocks, label) -> list:
    """Extract all ``MovieLink`` objects from a panel-block."""
    block = _find_panel_block(panel_blocks, label)
    if not block:
        return []
    value_span = block.find('span', class_='value')
    if not value_span:
        return []
    return extract_all_movie_links(value_span)


def _actor_gender_from_following_marker(a_tag: Tag) -> str:
    """Read ``female`` / ``male`` from the ``<strong class=\"symbol …\">`` after *a_tag*."""
    sib = a_tag.next_sibling
    while sib is not None and not isinstance(sib, Tag):
        sib = sib.next_sibling
    if sib is None or sib.name != 'strong':
        return ''
    classes = sib.get('class') or []
    if isinstance(classes, str):
        classes = classes.split()
    if 'female' in classes:
        return 'female'
    if 'male' in classes:
        return 'male'
    return ''


def _find_actor_panel_by_links(panel_blocks) -> Optional[Tag]:
    """Structural fallback: find a panel-block containing ``/actors/`` links.

    Used when locale-based label matching fails (e.g. CF bypass returning a
    page in simplified Chinese or English).
    """
    for block in panel_blocks:
        value_span = block.find('span', class_='value')
        if not value_span:
            continue
        for a_tag in value_span.find_all('a'):
            href = (a_tag.get('href') or '').strip()
            if '/actors/' in href:
                return block
    return None


def _extract_actors_with_gender(panel_blocks) -> list:
    """Actors from the 演員 panel, in order, with gender from ♀/♂ markers."""
    block = _find_panel_block(panel_blocks, _L_ACTOR)
    if not block:
        block = _find_actor_panel_by_links(panel_blocks)
    if not block:
        return []
    value_span = block.find('span', class_='value')
    if not value_span:
        return []
    actors: list = []
    for a_tag in value_span.find_all('a'):
        if not isinstance(a_tag, Tag):
            continue
        href = (a_tag.get('href') or '').strip()
        if '/actors/' not in href:
            continue
        ml = extract_movie_link(a_tag)
        if not ml:
            continue
        gender = _actor_gender_from_following_marker(a_tag)
        actors.append(ActorCredit(name=ml.name, href=ml.href, gender=gender))
    return actors


def _value_span_has_actor_link(value_span) -> bool:
    if not value_span:
        return False
    for a_tag in value_span.find_all('a'):
        if not isinstance(a_tag, Tag):
            continue
        href = (a_tag.get('href') or '').strip()
        if '/actors/' in href:
            return True
    return False


def _is_no_actor_placeholder_text(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if t.casefold() == 'n/a':
        return True
    return t in _NO_ACTOR_LISTING_PLACEHOLDERS


def _detect_no_actor_listing(panel_blocks) -> bool:
    """True when 演員 panel exists, has no actor links, and value text is a known placeholder."""
    block = _find_panel_block(panel_blocks, _L_ACTOR)
    if not block:
        return False
    value_span = block.find('span', class_='value')
    if not value_span:
        return False
    if _value_span_has_actor_link(value_span):
        return False
    raw = value_span.get_text(strip=True)
    return _is_no_actor_placeholder_text(raw)


def _extract_text_from_panel(panel_blocks, label) -> str:
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

        # Size and file count
        size = ''
        file_count = 0
        meta_span = magnet_a.find('span', class_='meta')
        if meta_span:
            meta_text = meta_span.get_text(strip=True)
            size_match = re.search(r'([\d.]+\s*(?:GB|MB|KB|TB))', meta_text)
            if size_match:
                size = size_match.group(1).replace(' ', '')
            fc_match = re.search(r'(\d+)\s*(?:個文件|files?)', meta_text)
            if fc_match:
                file_count = int(fc_match.group(1))

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
            file_count=file_count,
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
    first_block = _find_panel_block(panel_blocks, _L_CODE)
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
    detail.release_date = _extract_text_from_panel(panel_blocks, _L_DATE)

    # --- Duration ---
    detail.duration = _extract_text_from_panel(panel_blocks, _L_DURATION)

    # --- Directors ---
    detail.directors = _extract_links_from_panel(panel_blocks, _L_DIRECTOR)

    # --- Maker (片商) ---
    detail.maker = _extract_link_from_panel(panel_blocks, _L_MAKER)

    # --- Publisher (發行商) – may not be present on all pages ---
    detail.publisher = _extract_link_from_panel(panel_blocks, _L_PUBLISHER)

    # --- Series ---
    detail.series = _extract_link_from_panel(panel_blocks, _L_SERIES)

    # --- Rating & comment count ---
    rating_block = _find_panel_block(panel_blocks, _L_RATING)
    if rating_block:
        value_span = rating_block.find('span', class_='value')
        if value_span:
            score_text = value_span.get_text(strip=True)
            detail.rate, detail.comment_count = extract_rate_and_comments(score_text)

    # --- Tags (類別) ---
    detail.tags = _extract_links_from_panel(panel_blocks, _L_TAGS)

    # --- Actors (演員) — include gender from adjacent symbol markers ---
    detail.actors = _extract_actors_with_gender(panel_blocks)
    if not detail.actors:
        detail.no_actor_listing = _detect_no_actor_listing(panel_blocks)
        if detail.no_actor_listing:
            logger.debug(
                'Detail: no actor links; placeholder 演員 text → %r',
                NO_ACTOR_LISTING_ACTOR_NAME,
            )

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

    # --- Review count (from tabs "短評(N)" / "Reviews(N)") ---
    review_tab = soup.find('a', class_='review-tab')
    if review_tab:
        tab_text = review_tab.get_text(strip=True)
        review_match = re.search(r'(?:短評|Reviews)\((\d+)\)', tab_text)
        if review_match:
            detail.review_count = int(review_match.group(1))

    # --- Want to watch / watched counts ---
    for block in panel_blocks:
        span = block.find('span', class_='is-size-7')
        if span:
            text = span.get_text(strip=True)
            want_match = re.search(r'(\d+)\s*(?:人想看|want to watch)', text)
            if want_match:
                detail.want_count = int(want_match.group(1))
            watched_match = re.search(r'(\d+)\s*(?:人看過|have seen)', text)
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
