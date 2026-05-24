"""
Compatibility exports for the frozen detail parser fallback.

The canonical Python fallback lives in ``javdb.parsing.fallback.detail_parser``.
This module keeps the historical ``apps.api.parsers.detail_parser`` import path
working for downstream callers.
"""

from javdb.parsing.fallback.detail_parser import parse_detail_page

__all__ = [
    'parse_detail_page',
]
