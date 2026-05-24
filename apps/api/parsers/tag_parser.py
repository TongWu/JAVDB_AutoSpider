"""
Compatibility exports for the frozen tag parser fallback.

The canonical Python fallback lives in ``javdb.parsing.fallback.tag_parser``.
This module keeps the historical ``apps.api.parsers.tag_parser`` import path
working for downstream callers.
"""

from javdb.parsing.fallback.tag_parser import (
    _extract_new_tag_id_from_href,
    _extract_page_url,
    _extract_tag_id_from_href,
    _parse_selected_tag,
    _parse_url_params,
    parse_tag_page,
)

__all__ = [
    'parse_tag_page',
]
