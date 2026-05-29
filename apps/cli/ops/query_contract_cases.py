"""Shared fixtures for the dual-backend query Contract Golden (ADR-018)."""
from __future__ import annotations

import re

# The golden's `version` is a content hash computed by the generator (ADR-018 D6),
# not a hand-bumped constant — avoids the forgotten-bump footgun.


def normalize_sql(sql: str) -> str:
    """Canonical form so formatting differences don't cause false drift.

    Collapse all runs of whitespace to a single space and strip ends.
    Keep this trivial and identical to the TS-side normalizer (Phase 2).
    """
    return re.sub(r"\s+", " ", sql).strip()


# Each case: (builder_id, case_name, kwargs)
#
# Coverage rule (ADR-018): one case per `if` branch in each builder, or the
# guard guards nothing for the uncovered branch. Boolean branches whose bound
# value differs (1 vs 0) need BOTH truth values; the `uncensored` branch emits
# DIFFERENT SQL for True vs False, so it needs two cases too.
MOVIE_FILTER_CASES = [
    ("movie_filters", "empty", {}),
    ("movie_filters", "cursor_only", {"cursor_id": 42}),
    ("movie_filters", "q_only", {"q": "ABC-123"}),
    ("movie_filters", "q_and_perfect_match", {"q": "ABC", "perfect_match": True}),
    ("movie_filters", "perfect_match_false", {"perfect_match": False}),
    ("movie_filters", "actor_only", {"actor": "Jane"}),
    ("movie_filters", "hi_res_true", {"hi_res": True}),
    ("movie_filters", "hi_res_false", {"hi_res": False}),
    ("movie_filters", "session_only", {"session_id": "S1"}),
    ("movie_filters", "actor_hires_session", {"actor": "Jane", "hi_res": True, "session_id": "S1"}),
    ("movie_filters", "date_range_cursor", {"date_from": "2026-01-01", "date_to": "2026-02-01", "cursor_id": 42}),
]
TORRENT_FILTER_CASES = [
    ("torrent_filters", "empty", {}),
    ("torrent_filters", "cursor_only", {"cursor_id": 7}),
    ("torrent_filters", "q_only", {"q": "ABC-123"}),
    ("torrent_filters", "resolution_only", {"resolution_type": 1}),
    ("torrent_filters", "has_subtitle_true", {"has_subtitle": True}),
    ("torrent_filters", "has_subtitle_false", {"has_subtitle": False}),
    # `uncensored` emits different SQL for True vs False — both must be pinned.
    ("torrent_filters", "uncensored_true", {"uncensored": True}),
    ("torrent_filters", "uncensored_false", {"uncensored": False}),
    ("torrent_filters", "session_only", {"session_id": "S1"}),
    (
        "torrent_filters",
        "resolution_subtitle_uncensored",
        {"resolution_type": 1, "has_subtitle": True, "uncensored": False},
    ),
    ("torrent_filters", "date_range_cursor", {"date_from": "2026-01-01", "date_to": "2026-02-01", "cursor_id": 7}),
]
SESSION_QUERY_CASES = [
    ("session_query", "default", {"state": None, "cursor": None, "limit": 50}),
    ("session_query", "state_only", {"state": "committed", "cursor": None, "limit": 50}),
    ("session_query", "state_and_cursor", {"state": "failed", "cursor": "<ENCODED>", "limit": 20}),
]
