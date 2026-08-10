"""Daily index studio/label code-keyword blacklist (hardcoded, config-only).

The blacklist is sourced entirely from the static ``BLACKLIST_CODE_KEYWORDS``
config set (no D1 control plane, no env var — same shape as
:mod:`javdb.pipeline.index_family_blacklist`, but matching on the video code's
studio-label prefix instead of its structural family).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, MutableMapping
from typing import TypeVar

from javdb.infra.logging import log_summary_block

T = TypeVar("T")

_LEADING_ALPHA_RE = re.compile(r"^[A-Za-z]+")


def extract_code_keyword(video_code: str) -> str:
    """Return the uppercased leading alphabetic run of a video code, or ''.

    E.g. ``"IDBD-123"`` -> ``"IDBD"``. Codes with no leading letters (numeric
    date-style uncensored codes) yield ``""`` and never match the blacklist.
    """
    match = _LEADING_ALPHA_RE.match(str(video_code or "").strip())
    return match.group(0).upper() if match else ""


def load_daily_code_keyword_blacklist(custom_url: str | None) -> frozenset[str]:
    """Load the configured code-keyword blacklist for daily runs only."""
    if custom_url is not None:
        return frozenset()

    from javdb.spider.runtime.config import BLACKLIST_CODE_KEYWORDS

    return frozenset(BLACKLIST_CODE_KEYWORDS or ())


def filter_blacklisted_code_keywords(
    movies: list[T],
    blacklist: Iterable[str] | None,
    counts: MutableMapping[str, int] | None = None,
) -> list[T]:
    """Return movies whose ``video_code`` studio-label prefix is not blacklisted.

    Runs as a single pre-selection pass, mirroring
    :func:`javdb.pipeline.index_family_blacklist.filter_blacklisted_families`,
    so each excluded card is counted exactly once. ``counts`` accumulates
    per-keyword exclusion totals.
    """
    active_blacklist = set(blacklist or ())
    if not active_blacklist:
        return list(movies)
    kept = []
    for movie in movies:
        keyword = extract_code_keyword(getattr(movie, "video_code", "") or "")
        if keyword and keyword in active_blacklist:
            if counts is not None:
                counts[keyword] = counts.get(keyword, 0) + 1
            continue
        kept.append(movie)
    return kept


def log_code_keyword_blacklist_summary(logger, counts: MutableMapping[str, int]) -> None:
    """Emit aggregate daily code-keyword blacklist counts, if any."""
    if not counts:
        return

    pairs = [("total", sum(counts.values()))]
    pairs.extend(sorted(counts.items()))
    log_summary_block(logger, "INDEX CODE KEYWORD BLACKLIST SUMMARY", pairs)
