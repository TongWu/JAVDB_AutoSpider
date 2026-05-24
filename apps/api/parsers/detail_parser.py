"""
Compatibility exports for the canonical detail parser dispatch.

The canonical parser dispatch lives in ``javdb.parsing``.
This module keeps the historical ``apps.api.parsers.detail_parser`` import path
working for downstream callers.
"""

from javdb.parsing import parse_detail_page

__all__ = [
    'parse_detail_page',
]
