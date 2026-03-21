"""CSV helper adapter for optional Rust acceleration."""

from __future__ import annotations

from typing import Dict, Tuple

try:
    from javdb_rust_core import (
        create_csv_row as _rust_create_csv_row,
        check_torrent_status as _rust_check_torrent_status,
        collect_new_magnet_links as _rust_collect_new_magnet_links,
    )

    RUST_CSV_AVAILABLE = True
except ImportError:
    RUST_CSV_AVAILABLE = False
    _rust_create_csv_row = None
    _rust_check_torrent_status = None
    _rust_collect_new_magnet_links = None


def rust_create_csv_row(*args, **kwargs):
    if not RUST_CSV_AVAILABLE:
        return None
    try:
        return _rust_create_csv_row(*args, **kwargs)
    except Exception:
        return None


def rust_check_torrent_status(row: dict, include_downloaded: bool = False) -> Optional[Tuple[bool, bool, bool]]:
    if not RUST_CSV_AVAILABLE:
        return None
    try:
        result = _rust_check_torrent_status(row, include_downloaded)
        if isinstance(result, tuple) and len(result) == 3:
            return bool(result[0]), bool(result[1]), bool(result[2])
    except Exception:
        pass
    return None


def rust_collect_new_magnet_links(row: dict, magnet_links: dict) -> Tuple[Dict, Dict, Dict, Dict] | None:
    if not RUST_CSV_AVAILABLE:
        return None
    try:
        result = _rust_collect_new_magnet_links(row, magnet_links)
        if isinstance(result, tuple) and len(result) == 4:
            return result
    except Exception:
        pass
    return None

