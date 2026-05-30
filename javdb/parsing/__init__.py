"""
JAVDB HTML parsers - canonical public API.

Usage::

    from javdb.parsing import parse_index_page, parse_detail_page
    from javdb.parsing import parse_category_page, parse_top_page

Prefers the Rust implementation (``javdb.rust_core``) when available,
falling back to the pure-Python parsers otherwise.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from javdb.rust_core import (
        parse_index_page,
        parse_detail_page,
        parse_category_page,
        parse_top_page,
        parse_tag_page,
        detect_page_type,
    )
    RUST_PARSERS_AVAILABLE = True
    logger.debug("Rust parsers loaded successfully - using high-performance Rust implementation")
except ImportError as e:
    from javdb.parsing.fallback.index_parser import (
        parse_index_page,
        parse_category_page,
        parse_top_page,
    )
    from javdb.parsing.fallback.detail_parser import parse_detail_page
    from javdb.parsing.common import detect_page_type
    from javdb.parsing.fallback.tag_parser import parse_tag_page
    RUST_PARSERS_AVAILABLE = False
    logger.warning(
        "Rust core unavailable — pure-Python parsers fallback is best-effort "
        "and may diverge from production (ImportError: %s)",
        e,
    )

__all__ = [
    'parse_index_page',
    'parse_category_page',
    'parse_top_page',
    'parse_detail_page',
    'parse_tag_page',
    'detect_page_type',
    'RUST_PARSERS_AVAILABLE',
]
