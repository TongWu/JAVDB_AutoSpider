"""Search JavDB by video code and return annotated movie listings."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from apps.api.parsers import parse_index_page
from apps.api.parsers.index_parser import (
    derive_letter_suffix_fallback_video_code,
    find_exact_video_code_match,
)
from apps.api.parsers.search_exact import find_exact_entry_first_search_page
from apps.api.services import config_service, context
from apps.api.services.explore_service import _fetch_javdb_html
from packages.python.javdb_core.url_helper import build_search_url
from packages.python.javdb_platform.bridges.rust_adapters.parser_adapter import (
    is_login_page,
)


async def search_by_video_code(
    video_code: str,
    *,
    use_proxy: bool = True,
    use_cookie: bool = True,
    f: str = "all",
) -> Dict[str, Any]:
    """Fetch the first JavDB search page for *video_code*, parse it, and mark the exact match.

    Returns a dict with ``movies`` (each annotated with ``exact_match``),
    ``exact_match_entry``, and diagnostic metadata.
    """
    cfg = config_service.load_runtime_config()
    base_url = str(cfg.get("BASE_URL", "https://javdb.com")).rstrip("/")

    search_url = build_search_url(video_code, f=f, base_url=base_url)
    html = _fetch_javdb_html(search_url, use_proxy=use_proxy, use_cookie=use_cookie)

    if is_login_page(html):
        raise HTTPException(status_code=403, detail="JavDB login required")

    parsed = parse_index_page(html, page_num=1)
    movies = parsed.movies if parsed.has_movie_list else []

    exact_entry = find_exact_entry_first_search_page(movies, video_code)
    letter_suffix_fallback_searched = False

    if exact_entry is None:
        alt_code = derive_letter_suffix_fallback_video_code(video_code)
        if alt_code is not None:
            alt_url = build_search_url(alt_code, f=f, base_url=base_url)
            try:
                alt_html = _fetch_javdb_html(alt_url, use_proxy=use_proxy, use_cookie=use_cookie)
            except HTTPException:
                alt_html = None

            letter_suffix_fallback_searched = True
            if alt_html and not is_login_page(alt_html):
                parsed_alt = parse_index_page(alt_html, page_num=1)
                m_alt = parsed_alt.movies if parsed_alt.has_movie_list else []
                exact_entry = find_exact_video_code_match(m_alt, alt_code)

    annotated = _annotate_movies(movies, exact_entry)
    exact_match_entry: Optional[Dict[str, str]] = None
    if exact_entry is not None:
        exact_match_entry = {
            "href": exact_entry.href,
            "video_code": exact_entry.video_code,
        }

    return {
        "video_code": video_code,
        "search_url": search_url,
        "movies": annotated,
        "exact_match_entry": exact_match_entry,
        "letter_suffix_fallback_searched": letter_suffix_fallback_searched,
    }


def _annotate_movies(
    movies: list,
    exact_entry: Any,
) -> List[Dict[str, Any]]:
    """Convert movie entries to dicts with an ``exact_match`` flag."""
    exact_href = (exact_entry.href if exact_entry is not None else None)
    result: List[Dict[str, Any]] = []
    for entry in movies:
        d = entry.to_dict()
        d["exact_match"] = (exact_entry is not None and entry.href == exact_href)
        result.append(d)
    return result
