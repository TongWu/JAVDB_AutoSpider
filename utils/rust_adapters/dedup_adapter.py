"""Dedup-related optional Rust adapter."""

from __future__ import annotations

from typing import Dict, List

try:
    from javdb_rust_core import should_skip_from_rclone as _rs_should_skip_from_rclone
    from javdb_rust_core import check_dedup_upgrade as _rs_check_dedup_upgrade

    RUST_DEDUP_AVAILABLE = True
except ImportError:
    RUST_DEDUP_AVAILABLE = False
    _rs_should_skip_from_rclone = None
    _rs_check_dedup_upgrade = None


def rust_should_skip_from_rclone(video_code: str, entries: List[dict], enable_dedup: bool) -> bool:
    if not RUST_DEDUP_AVAILABLE:
        return False
    try:
        return bool(_rs_should_skip_from_rclone(video_code, entries, enable_dedup))
    except Exception:
        return False


def rust_check_dedup_upgrade(video_code: str, new_torrent_types: Dict[str, bool], entries: List[dict]) -> List[dict]:
    if not RUST_DEDUP_AVAILABLE:
        return []
    try:
        result = _rs_check_dedup_upgrade(video_code, new_torrent_types, entries)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    return []

