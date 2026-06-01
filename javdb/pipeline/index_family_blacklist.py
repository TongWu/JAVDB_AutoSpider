"""Daily index video-code-family blacklist (config-only, ADR-044).

The blacklist is sourced entirely from the static
``DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST`` config list. There is no D1 control
plane: video-code families are a closed enum defined by the parser.
"""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from typing import TypeVar

from javdb.infra.logging import log_summary_block

T = TypeVar("T")


def normalize_family_blacklist(values: Iterable[str] | None) -> set[str]:
    """Trim and drop empties, returning the active family set."""
    return {
        str(value).strip()
        for value in (values or [])
        if value is not None and str(value).strip()
    }


def load_daily_family_blacklist(custom_url: str | None) -> set[str]:
    """Load the configured family blacklist for daily runs only."""
    if custom_url is not None:
        return set()

    from javdb.spider.runtime.config import DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST

    return normalize_family_blacklist(DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST)


def filter_blacklisted_families(
    movies: list[T],
    blacklist: Iterable[str] | None,
    counts: MutableMapping[str, int] | None = None,
) -> list[T]:
    """Return movies whose ``video_code_family`` is not in ``blacklist``.

    Runs as a single pre-selection pass (ADR-044 D3/D5), so each excluded card
    is counted exactly once. ``counts`` accumulates per-family exclusion totals.
    """
    active_blacklist = set(blacklist or ())
    if not active_blacklist:
        return movies
    kept = []
    for movie in movies:
        family = getattr(movie, "video_code_family", "") or ""
        if family and family in active_blacklist:
            if counts is not None:
                counts[family] = counts.get(family, 0) + 1
            continue
        kept.append(movie)
    return kept


def log_family_blacklist_summary(logger, counts: MutableMapping[str, int]) -> None:
    """Emit aggregate daily-family blacklist counts, if any."""
    if not counts:
        return

    pairs = [("total", sum(counts.values()))]
    pairs.extend(sorted(counts.items()))
    log_summary_block(logger, "INDEX FAMILY BLACKLIST SUMMARY", pairs)
