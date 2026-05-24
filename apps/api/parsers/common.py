"""
Compatibility exports for shared parser helpers.

The canonical parser helpers live in ``javdb.parsing.common``. This module
keeps the historical ``apps.api.parsers.common`` import path working for API
parser adapters and downstream callers.
"""

from javdb.parsing.common import (
    MovieLink,
    _HYPHENATED_CODE_RE,
    _PAGE_TYPE_PATTERNS,
    _is_plausible_video_code,
    absolutize_supporting_actors_json,
    detect_page_type,
    extract_all_movie_links,
    extract_category_name,
    extract_movie_link,
    extract_rate_and_comments,
    extract_video_code,
    javdb_absolute_url,
    movie_href_lookup_values,
    normalize_javdb_href_path,
)

__all__ = [
    'MovieLink',
    'extract_rate_and_comments',
    'normalize_javdb_href_path',
    'javdb_absolute_url',
    'movie_href_lookup_values',
    'absolutize_supporting_actors_json',
    'extract_movie_link',
    'extract_all_movie_links',
    'extract_video_code',
    'detect_page_type',
    'extract_category_name',
]
