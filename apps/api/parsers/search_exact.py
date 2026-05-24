"""Compatibility exports for exact-match video-code search helpers.

The canonical helper lives in ``javdb.parsing.search_exact``.  This module
keeps the historical ``apps.api.parsers.search_exact`` import path working for
downstream callers.
"""

from javdb.parsing.search_exact import find_exact_entry_first_search_page

__all__ = ['find_exact_entry_first_search_page']
