"""Daily index video-code-family blacklist (config-only, ADR-044).

The blacklist is sourced entirely from the static
``DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST`` config list. There is no D1 control
plane: video-code families are a closed enum defined by the parser.
"""

from __future__ import annotations

from collections.abc import Iterable


def normalize_family_blacklist(values: Iterable[str] | None) -> set[str]:
    """Trim and drop empties, returning the active family set."""
    return {
        str(value).strip()
        for value in (values or [])
        if value is not None and str(value).strip()
    }


def filter_blacklisted_families(movies, blacklist, counts=None):
    """Return movies whose ``video_code_family`` is not in ``blacklist``.

    Runs as a single pre-selection pass (ADR-044 D3/D5), so each excluded card
    is counted exactly once. ``counts`` accumulates per-family exclusion totals.
    """
    if not blacklist:
        return movies
    kept = []
    for movie in movies:
        family = getattr(movie, "video_code_family", "") or ""
        if family and family in blacklist:
            if counts is not None:
                counts[family] = counts.get(family, 0) + 1
            continue
        kept.append(movie)
    return kept
