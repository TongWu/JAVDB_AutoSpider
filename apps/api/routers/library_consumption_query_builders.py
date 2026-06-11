# apps/api/routers/library_consumption_query_builders.py
"""Pure SQL builders for the Library consumption endpoints (ADR-034 FE-3).

Dual-backend parity unit (ADR-018): the TS Worker mirrors each string
byte-for-byte. Summary uses TWO separate SQL functions (signals aggregate +
unresolved count) because they query different tables; the route assembles
both into one ConsumptionSummary response.

`watched_at` is a T-separated ISO timestamp — use substr(watched_at, 1, 10)
for daily grouping, NOT DATE() (cannot parse the format).
"""

from __future__ import annotations


def build_consumption_summary_query() -> tuple[str, list]:
    """Aggregate counts from ConsumptionSignal. avg_rating is NOT coalesced — NULL
    propagates to the response to distinguish 'no ratings' from 'rated 0'."""
    sql = (
        "SELECT "
        "COUNT(*) AS total_signals, "
        "COALESCE(SUM(CASE WHEN watched = 1 THEN 1 ELSE 0 END), 0) AS watched_count, "
        "COALESCE(SUM(CASE WHEN watched = 0 THEN 1 ELSE 0 END), 0) AS unwatched_count, "
        "AVG(rating) AS avg_rating, "
        "COUNT(DISTINCT video_code) AS unique_titles, "
        "COUNT(DISTINCT instance) AS instance_count "
        "FROM ConsumptionSignal"
    )
    return sql, []


def build_consumption_summary_unresolved_count_query() -> tuple[str, list]:
    """Count of rows in UnresolvedMediaItem (items the reconciler could not resolve)."""
    sql = (
        "SELECT COUNT(*) AS unresolved_count "
        "FROM UnresolvedMediaItem"
    )
    return sql, []


def build_consumption_recent_query(
    *,
    instance: str | None = None,
    watched: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[str, list]:
    """Newest-first page; optional instance and watched filters.

    `watched` is a Python bool (True/False/None), NOT a string.
    True  → WHERE watched = 1
    False → WHERE watched = 0
    None  → no filter
    """
    bindings: list[str | int] = []
    clauses: list[str] = []
    if instance is not None:
        clauses.append("instance = ?")
        bindings.append(instance)
    if watched is True:
        clauses.append("watched = 1")
    elif watched is False:
        clauses.append("watched = 0")
    where = ("WHERE " + " AND ".join(clauses) + " ") if clauses else ""
    sql = (
        "SELECT video_code, source_type, instance, library_id, library_name, "
        "watched, progress_pct, play_count, rating, watched_at, resolved_confidence, observed_at "
        "FROM ConsumptionSignal "
        f"{where}"
        "ORDER BY observed_at DESC, video_code, instance, library_id "
        "LIMIT ? OFFSET ?"
    )
    bindings.extend([limit, offset])
    return sql, bindings


def build_consumption_trend_query(*, cutoff: str) -> tuple[str, list]:
    """Daily watched count and total signal count since cutoff (YYYY-MM-DD).

    Grouped over watched_at (the watch-event timestamp). Only rows where
    watched_at IS NOT NULL are included (rows with watched_at=NULL have no
    confirmed watch event and must not appear on the trend axis).
    """
    sql = (
        "SELECT substr(watched_at, 1, 10) AS d, "
        "COALESCE(SUM(CASE WHEN watched = 1 THEN 1 ELSE 0 END), 0) AS watched, "
        "COUNT(*) AS total_signals "
        "FROM ConsumptionSignal "
        "WHERE watched_at IS NOT NULL AND watched_at >= ? "
        "GROUP BY d ORDER BY d"
    )
    return sql, [cutoff]


def build_consumption_unresolved_query(
    *,
    instance: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[str, list]:
    """Newest-first page of unresolved items; optional instance filter."""
    bindings: list[str | int] = []
    where = ""
    if instance is not None:
        where = "WHERE instance = ? "
        bindings.append(instance)
    sql = (
        "SELECT instance, source_type, library_id, library_name, item_id, raw_title, file_path, observed_at "
        "FROM UnresolvedMediaItem "
        f"{where}"
        "ORDER BY observed_at DESC, instance, library_id, item_id "
        "LIMIT ? OFFSET ?"
    )
    bindings.extend([limit, offset])
    return sql, bindings


__all__ = [
    "build_consumption_recent_query",
    "build_consumption_summary_query",
    "build_consumption_summary_unresolved_count_query",
    "build_consumption_trend_query",
    "build_consumption_unresolved_query",
]
