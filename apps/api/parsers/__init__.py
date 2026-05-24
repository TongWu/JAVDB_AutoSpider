"""
Compatibility exports for JAVDB HTML parser dispatch.

The canonical parser API lives in ``javdb.parsing``. This module keeps the
historical ``apps.api.parsers`` import path working during ADR-011 migration.
"""

import javdb.parsing as _parsing

parse_index_page = _parsing.parse_index_page
parse_category_page = _parsing.parse_category_page
parse_top_page = _parsing.parse_top_page
parse_detail_page = _parsing.parse_detail_page
parse_tag_page = _parsing.parse_tag_page
detect_page_type = _parsing.detect_page_type
RUST_PARSERS_AVAILABLE = _parsing.RUST_PARSERS_AVAILABLE

__all__ = list(_parsing.__all__)
