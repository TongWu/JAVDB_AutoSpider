"""
Frozen BeautifulSoup parser fallbacks.

These modules are the canonical Python fallback implementation for
``javdb.rust_core`` parsers. New parser behavior should be implemented in the
Rust parser first and kept in parity with these fallbacks only when required.
"""

from javdb.parsing.fallback.detail_parser import parse_detail_page
from javdb.parsing.fallback.index_parser import (
    derive_letter_suffix_fallback_video_code,
    find_exact_video_code_match,
    parse_category_page,
    parse_index_page,
    parse_top_page,
)
from javdb.parsing.fallback.tag_parser import parse_tag_page

__all__ = [
    'parse_index_page',
    'parse_category_page',
    'parse_top_page',
    'find_exact_video_code_match',
    'derive_letter_suffix_fallback_video_code',
    'parse_detail_page',
    'parse_tag_page',
]
