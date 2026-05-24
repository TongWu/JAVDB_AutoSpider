"""
Compatibility exports for canonical tag parser dispatch and fallback helpers.

The canonical parser dispatch lives in ``javdb.parsing``.
This module keeps the historical ``apps.api.parsers.tag_parser`` import path
working for downstream callers.
"""

from javdb.parsing import parse_tag_page
from javdb.parsing.fallback.tag_parser import (
    _extract_new_tag_id_from_href,
    _extract_page_url,
    _extract_tag_id_from_href,
    _parse_selected_tag,
    _parse_url_params,
)

__all__ = [
    'parse_tag_page',
]
