# apps/api/routers/library_ownership_query_builders.py
"""Pure SQL builders for the Library ownership endpoints (ADR-034 FE-2).

Dual-backend parity unit (ADR-018): the TS Worker mirrors each string
byte-for-byte and the query-contract golden pins them. Summary uses TWO
separate queries (per-source breakdown + cross-source distinct) because a
single GROUP BY cannot simultaneously compute per-source and cross-source
distinct counts without a subquery. The route assembles both into one
OwnershipSummary response.
"""

from __future__ import annotations


def build_ownership_summary_by_source_query() -> tuple[str, list]:
    """Per-source breakdown: unique present titles, present rows, total bytes."""
    sql = (
        "SELECT source, "
        "COUNT(DISTINCT video_code) AS unique_titles, "
        "COALESCE(SUM(CASE WHEN present = 1 THEN 1 ELSE 0 END), 0) AS present_rows, "
        "COALESCE(SUM(size), 0) AS total_bytes "
        "FROM OwnershipLedger "
        "WHERE present = 1 "
        "GROUP BY source "
        "ORDER BY source"
    )
    return sql, []


def build_ownership_summary_distinct_query() -> tuple[str, list]:
    """Cross-source total: distinct video codes with at least one present row."""
    sql = (
        "SELECT COUNT(DISTINCT video_code) AS total_owned_titles "
        "FROM OwnershipLedger "
        "WHERE present = 1"
    )
    return sql, []


def build_ownership_recent_query(
    *, source: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[str, list]:
    """Newest-first page of ownership rows; optional source filter."""
    bindings: list[str | int] = []
    where = ""
    if source is not None:
        where = "WHERE source = ? "
        bindings.append(source)
    sql = (
        "SELECT video_code, source, category, path, size, present, observed_at "
        "FROM OwnershipLedger "
        f"{where}"
        "ORDER BY observed_at DESC, video_code, source, category "
        "LIMIT ? OFFSET ?"
    )
    bindings.extend([limit, offset])
    return sql, bindings


__all__ = [
    "build_ownership_recent_query",
    "build_ownership_summary_by_source_query",
    "build_ownership_summary_distinct_query",
]
