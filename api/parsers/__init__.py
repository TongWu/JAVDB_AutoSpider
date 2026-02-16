"""
JAVDB HTML parsers – public API.

Usage::

    from api.parsers import parse_index_page, parse_detail_page
    from api.parsers import parse_category_page, parse_top_page
"""

from api.parsers.index_parser import (
    parse_index_page,
    parse_category_page,
    parse_top_page,
)
from api.parsers.detail_parser import parse_detail_page
from api.parsers.common import detect_page_type
from api.parsers.tag_parser import parse_tag_page

__all__ = [
    'parse_index_page',
    'parse_category_page',
    'parse_top_page',
    'parse_detail_page',
    'parse_tag_page',
    'detect_page_type',
]
