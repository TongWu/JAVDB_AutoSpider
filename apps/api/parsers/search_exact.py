"""Shared exact-match helpers for video-code search.

Used by both the Search API and the inventory alignment tool.
"""

from __future__ import annotations

from typing import Optional

from apps.api.parsers.index_parser import (
    derive_letter_suffix_fallback_video_code,
    find_exact_video_code_match,
)


def find_exact_entry_first_search_page(movies: list, video_code: str):
    """Match *video_code* on a results page; if miss, try the letter-suffix fallback code.

    Returns the matched entry or ``None``.  Does **not** perform any network
    requests — the caller is responsible for a second search when this returns
    ``None`` and a fallback code exists.
    """
    if not movies:
        return None
    hit = find_exact_video_code_match(movies, video_code)
    if hit is not None:
        return hit
    alt = derive_letter_suffix_fallback_video_code(video_code)
    if alt is None:
        return None
    return find_exact_video_code_match(movies, alt)
