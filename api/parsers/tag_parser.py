"""
Tag filter page parser.

Parses the JavDB ``/tags`` pages to extract:
- All filter categories (主題, 角色, 服裝, 體型, 行爲, 玩法, 類別, …)
- All tag options within each category with their display names
- The numeric/string ID for each tag (``c{N}=ID``) when extractable
- Which tags are currently selected
- The movie listing (delegated to the index parser)

The ID mapping is extracted from two sources:

1. **Non-selected tags with real URLs** – when a category has no active
   selection, its tag ``<a>`` elements carry full href URLs like
   ``/tags?c4=15&c7=28`` from which we extract ``c4=15`` → "熟女".

2. **Currently selected tags** – their IDs come from the page URL
   (e.g. ``/tags?c1=23&c5=24`` → c1=23 is "淫亂真實").

Tags whose ID cannot be determined (because their category already has an
active selection and the href is ``javascript:;``) will have an empty
``tag_id`` field.  Combining results from pages with different selections
lets callers build a complete mapping.
"""

from __future__ import annotations

import re
import logging
from typing import Dict, List, Tuple
from urllib.parse import urlparse, parse_qs, unquote

from bs4 import BeautifulSoup
from bs4.element import Tag

from api.models import (
    TagOption,
    TagCategory,
    TagPageResult,
)
from api.parsers.index_parser import parse_index_page

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_page_url(html_content: str) -> str:
    """Try to find the original page URL from the HTML.

    Checks ``<!-- saved from url=(...) -->`` comments and
    ``<link rel="canonical">`` tags.
    """
    # "saved from" comment – format: url=(NNNN)https://...
    m = re.search(r'saved from url=\(\d+\)(https?://[^\s]+)', html_content[:3000])
    if m:
        return m.group(1).rstrip()

    # Canonical link
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', html_content[:5000])
    if m:
        return m.group(1)

    return ''


def _parse_url_params(url: str) -> Dict[str, List[str]]:
    """Parse URL query parameters, returning ``{key: [values]}``."""
    parsed = urlparse(url)
    return parse_qs(parsed.query)


def _extract_tag_id_from_href(href: str, category_id: str) -> str:
    """Extract the tag ID for a specific category from a tag's href URL.

    For example, given ``href="/tags?c4=15&c7=28"`` and
    ``category_id="4"``, returns ``"15"``.

    For the *same-category multi-select* pattern like ``c7=28,212`` where
    the current page already has ``c7=28`` selected, the newly added value
    is the tag's ID.
    """
    if not href or 'javascript' in href:
        return ''

    params = _parse_url_params(href)
    key = f'c{category_id}'
    values = params.get(key, [])
    if not values:
        return ''

    # The value may be a single ID or comma-separated list
    return values[0]


def _extract_new_tag_id_from_href(
    href: str,
    category_id: str,
    current_selection: str,
) -> str:
    """For multi-select categories, extract the *newly added* tag ID.

    When the page already has ``c7=28`` selected and a sibling tag's href
    is ``/tags?c7=28,212``, the new ID is ``212``.

    Args:
        href: The tag's href URL.
        category_id: The category's ``data-cid``.
        current_selection: The comma-separated value currently in the URL
            for this category (e.g. ``"28"`` or ``"28,212"``).
    """
    raw = _extract_tag_id_from_href(href, category_id)
    if not raw:
        return ''

    # Split both into sets to find the diff
    raw_ids = set(raw.split(','))
    current_ids = set(current_selection.split(',')) if current_selection else set()
    new_ids = raw_ids - current_ids

    if len(new_ids) == 1:
        return new_ids.pop()

    # Fallback: if multiple new IDs or none, return the full value
    return raw if not current_ids else ''


def _parse_selected_tag(div_tag: Tag) -> str:
    """Extract the display name from a selected tag ``<div class="tag is-info">``."""
    # The text content includes the button text; strip that.
    button = div_tag.find('button')
    if button:
        button.decompose()
    return div_tag.get_text(strip=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_tag_page(html_content: str, page_num: int = 1) -> TagPageResult:
    """Parse a JavDB tag filter page.

    Returns a ``TagPageResult`` containing:
    - The movie listing (same as ``parse_index_page``)
    - All filter categories with tag options and ID mappings
    - The current selection state

    Args:
        html_content: Full HTML of the ``/tags`` page.
        page_num: Page number for pagination context.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # --- Parse movie listing (reuse index parser) ---
    index_result = parse_index_page(html_content, page_num)

    # --- Extract current page URL and its selections ---
    page_url = _extract_page_url(html_content)
    url_params = _parse_url_params(page_url)

    # Build current_selections: {category_id: "value_string"}
    # e.g. {"1": "23", "5": "24", "7": "28,212", "11": "2026"}
    current_selections: Dict[str, str] = {}
    for key, values in url_params.items():
        m = re.match(r'^c(\d+)$', key)
        if m:
            current_selections[m.group(1)] = values[0] if values else ''

    # --- Parse the tag filter panel ---
    categories: List[TagCategory] = []
    tags_div = soup.find('div', id='tags')
    if not tags_div:
        logger.warning('No tag filter panel found (<div id="tags">)')
        return TagPageResult(
            has_movie_list=index_result.has_movie_list,
            movies=index_result.movies,
            page_title=index_result.page_title,
            categories=[],
            current_selections=current_selections,
        )

    for dt in tags_div.find_all('dt', class_=lambda c: c and 'tag-category' in c):
        cid = dt.get('data-cid', '')
        if not cid:
            # Try to extract from id="tag-category-N"
            dt_id = dt.get('id', '')
            m = re.search(r'tag-category-(\d+)', dt_id)
            if m:
                cid = m.group(1)
        if not cid:
            continue

        # Category name
        strong = dt.find('strong')
        cat_name = strong.get_text(strip=True) if strong else ''

        # Current selection for this category
        cat_current = current_selections.get(cid, '')

        # Parse tag options
        options: List[TagOption] = []
        labels_span = dt.find('span', class_='tag_labels')
        if not labels_span:
            categories.append(TagCategory(
                category_id=cid,
                name=cat_name,
                options=options,
            ))
            continue

        for child in labels_span.children:
            if not isinstance(child, Tag):
                continue

            # --- Selected tag: <div class="tag is-info">NAME<button>...</button></div>
            if child.name == 'div' and 'is-info' in child.get('class', []):
                tag_name = _parse_selected_tag(child)
                if not tag_name:
                    continue

                # Determine the ID from current_selections
                # For multi-select (e.g. c7=28,212), we need to figure out
                # which ID in the comma list belongs to this tag.
                # Strategy: match by position among selected tags in this category.
                tag_id = ''
                if cat_current:
                    # Collect all selected names in order to match with IDs
                    # We'll do a second pass after collecting all options
                    tag_id = '__selected__'

                options.append(TagOption(
                    name=tag_name,
                    tag_id=tag_id,
                    selected=True,
                ))

            # --- Non-selected tag: <a class="tag ..." href="...">NAME</a>
            elif child.name == 'a' and 'tag' in child.get('class', []):
                tag_name = child.get_text(strip=True)
                if not tag_name:
                    continue

                href = child.get('href', '')
                tag_id = ''

                if href and 'javascript' not in href:
                    if cat_current:
                        # This category already has a selection; the href
                        # adds a new tag to the existing selection.
                        tag_id = _extract_new_tag_id_from_href(
                            href, cid, cat_current
                        )
                    else:
                        # No current selection — the href contains the
                        # single tag ID directly.
                        tag_id = _extract_tag_id_from_href(href, cid)

                options.append(TagOption(
                    name=tag_name,
                    tag_id=tag_id,
                    selected=False,
                ))

        # --- Second pass: resolve IDs for selected tags ---
        selected_opts = [o for o in options if o.selected]
        if selected_opts and cat_current:
            current_ids = cat_current.split(',')

            # Collect IDs used by non-selected tags in this category's href
            non_selected_ids = {o.tag_id for o in options if not o.selected and o.tag_id}

            # The selected tag IDs are those in current_ids not claimed by
            # non-selected siblings.
            remaining_ids = [
                tid for tid in current_ids if tid not in non_selected_ids
            ]

            if len(remaining_ids) == len(selected_opts):
                # Perfect 1:1 match — assign in order
                for opt, tid in zip(selected_opts, remaining_ids):
                    opt.tag_id = tid
            elif len(remaining_ids) >= 1 and len(selected_opts) == 1:
                # Single selected tag, take the first remaining ID
                selected_opts[0].tag_id = remaining_ids[0]
            else:
                # Ambiguous — assign what we can
                for opt, tid in zip(selected_opts, remaining_ids):
                    opt.tag_id = tid
                # Mark any unresolved as empty
                for opt in selected_opts[len(remaining_ids):]:
                    opt.tag_id = ''

        categories.append(TagCategory(
            category_id=cid,
            name=cat_name,
            options=options,
        ))

    logger.debug(
        'Parsed tag page: %d categories, %d total options, %d movies',
        len(categories),
        sum(len(c.options) for c in categories),
        len(index_result.movies),
    )

    return TagPageResult(
        has_movie_list=index_result.has_movie_list,
        movies=index_result.movies,
        page_title=index_result.page_title,
        categories=categories,
        current_selections=current_selections,
    )
