"""
Compatibility exports for frozen index parser fallbacks.

The canonical Python fallback lives in ``javdb.parsing.fallback.index_parser``.
This module keeps the historical ``apps.api.parsers.index_parser`` import path
working for downstream callers.
"""

from javdb.parsing.fallback.index_parser import (
    derive_letter_suffix_fallback_video_code,
    find_exact_video_code_match,
    parse_category_page,
    parse_index_page,
    parse_top_page,
)

__all__ = [
    'parse_index_page',
    'find_exact_video_code_match',
    'derive_letter_suffix_fallback_video_code',
    'parse_category_page',
    'parse_top_page',
]
