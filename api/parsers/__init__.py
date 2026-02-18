"""
JAVDB HTML parsers – public API.

Usage::

    from api.parsers import parse_index_page, parse_detail_page
    from api.parsers import parse_category_page, parse_top_page

Prefers the Rust implementation (``javdb_rust_core``) when available,
falling back to the pure-Python parsers otherwise.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from javdb_rust_core import (
        parse_index_page,
        parse_detail_page,
        parse_category_page,
        parse_top_page,
        parse_tag_page,
        detect_page_type,
    )
    RUST_PARSERS_AVAILABLE = True
    logger.info("✅ Rust parsers loaded successfully - using high-performance Rust implementation")
except ImportError as e:
    from api.parsers.index_parser import (
        parse_index_page,
        parse_category_page,
        parse_top_page,
    )
    from api.parsers.detail_parser import parse_detail_page
    from api.parsers.common import detect_page_type
    from api.parsers.tag_parser import parse_tag_page
    RUST_PARSERS_AVAILABLE = False
    logger.warning(f"⚠️  Rust parsers not available (ImportError: {e}) - falling back to pure-Python implementation")

__all__ = [
    'parse_index_page',
    'parse_category_page',
    'parse_top_page',
    'parse_detail_page',
    'parse_tag_page',
    'detect_page_type',
    'RUST_PARSERS_AVAILABLE',
]
