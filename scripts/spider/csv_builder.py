"""Compatibility wrappers for CSV row construction.

The canonical implementations now live in ``scripts.ingestion.adapters``.
"""

from scripts.ingestion.adapters import (
    check_torrent_status,
    collect_new_magnet_links,
    create_csv_row_with_history_filter,
    create_redownload_row,
    should_include_torrent_in_csv,
)
from utils.rust_adapters.csv_adapter import RUST_CSV_AVAILABLE

__all__ = [
    'RUST_CSV_AVAILABLE',
    'check_torrent_status',
    'collect_new_magnet_links',
    'create_csv_row_with_history_filter',
    'create_redownload_row',
    'should_include_torrent_in_csv',
]
