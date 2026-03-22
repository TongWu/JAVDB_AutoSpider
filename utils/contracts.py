"""Cross-module data contract helpers.

Single source of truth for shared constants and mapping tables used
across both Python modules and (mirrored in) the Rust core.

Rust mirrors:
  - TORRENT_CATEGORIES / DOWNLOADED_PLACEHOLDER → rust_core/src/csv_writer.rs
  - UNCENSORED_SENSOR_PRIORITY                  → rust_core/src/dedup_ops.rs
  - CATEGORY_TO_INDICATORS                      → rust_core/src/csv_writer.rs (implicit)
"""

from __future__ import annotations

from typing import Tuple

# ── Torrent category constants ───────────────────────────────────────────

TORRENT_CATEGORIES = (
    'hacked_subtitle',
    'hacked_no_subtitle',
    'subtitle',
    'no_subtitle',
)

DOWNLOADED_PLACEHOLDER = '[DOWNLOADED PREVIOUSLY]'

# ── Category ↔ Indicator mapping ────────────────────────────────────────

CATEGORY_TO_INDICATORS = {
    'hacked_subtitle':    (1, 0),
    'hacked_no_subtitle': (0, 0),
    'subtitle':           (1, 1),
    'no_subtitle':        (0, 1),
}
INDICATORS_TO_CATEGORY = {v: k for k, v in CATEGORY_TO_INDICATORS.items()}


def category_to_indicators(category: str) -> Tuple[int, int]:
    """Convert a legacy category name to (SubtitleIndicator, CensorIndicator)."""
    return CATEGORY_TO_INDICATORS.get(category, (0, 1))


def indicators_to_category(subtitle_ind: int, censor_ind: int) -> str:
    """Convert indicator pair back to legacy category name."""
    return INDICATORS_TO_CATEGORY.get((subtitle_ind, censor_ind), 'no_subtitle')


# ── Uncensored sensor priority (无码系列优先级) ─────────────────────────

UNCENSORED_SENSOR_PRIORITY = {
    '无码流出': 3,
    '无码': 2,
    '无码破解': 1,
}


def is_uncensored_category(cat: str) -> bool:
    return cat in UNCENSORED_SENSOR_PRIORITY


def get_uncensored_priority(cat: str) -> int:
    return UNCENSORED_SENSOR_PRIORITY.get(cat, 0)


# ── Dict key normalizers ────────────────────────────────────────────────

def get_video_code(data: dict, default: str = "") -> str:
    """Return normalized video code from snake/camel compatible keys."""
    return data.get("VideoCode", data.get("video_code", default))


def get_sensor_category(data: dict, default=None):
    return data.get("SensorCategory", data.get("sensor_category", default))


def get_subtitle_category(data: dict, default=None):
    return data.get("SubtitleCategory", data.get("subtitle_category", default))


def get_folder_path(data: dict, default=None):
    return data.get("FolderPath", data.get("folder_path", default))
