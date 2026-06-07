"""Pure SQL builders for the Library acquisition endpoints (ADR-034 FE-1).

These are the dual-backend parity unit (ADR-018): the TS Worker mirrors each
string byte-for-byte and the query-contract golden pins them. Keep the SQL on
single-line string fragments so whitespace normalization stays trivial.
"""

from __future__ import annotations


def build_acquisition_summary_query() -> tuple[str, list]:
    """Funnel/KPI counts across all rows. COALESCE so an empty table yields 0."""
    sql = (
        "SELECT "
        "COALESCE(SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END), 0) AS queued, "
        "COALESCE(SUM(CASE WHEN state='downloading' THEN 1 ELSE 0 END), 0) AS downloading, "
        "COALESCE(SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END), 0) AS completed, "
        "COALESCE(SUM(CASE WHEN state='stalled' THEN 1 ELSE 0 END), 0) AS stalled, "
        "COALESCE(SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END), 0) AS failed, "
        "COUNT(*) AS total "
        "FROM AcquisitionOutcome"
    )
    return sql, []


def build_acquisition_recent_query(
    *, state: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[str, list]:
    """Newest-first page; optional state filter. queued_at is the stable order."""
    bindings: list[str | int] = []
    where = ""
    if state is not None:
        where = "WHERE state = ? "
        bindings.append(state)
    sql = (
        "SELECT qb_hash, video_code, href, category, state, queued_at, completed_at, last_seen_at "
        "FROM AcquisitionOutcome "
        f"{where}"
        "ORDER BY queued_at DESC "
        "LIMIT ? OFFSET ?"
    )
    bindings.extend([limit, offset])
    return sql, bindings


def build_acquisition_trend_query(*, cutoff: str) -> tuple[str, list]:
    """Daily completed/stalled/failed counts since cutoff (YYYY-MM-DD).

    substr() not DATE(): last_seen_at is a T-separated ISO timestamp DATE()
    cannot parse. last_seen_at is the transition date (refreshed every pass).
    """
    sql = (
        "SELECT substr(last_seen_at, 1, 10) AS d, "
        "COALESCE(SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END), 0) AS completed, "
        "COALESCE(SUM(CASE WHEN state='stalled' THEN 1 ELSE 0 END), 0) AS stalled, "
        "COALESCE(SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END), 0) AS failed "
        "FROM AcquisitionOutcome "
        "WHERE state IN ('completed','stalled','failed') AND last_seen_at >= ? "
        "GROUP BY d ORDER BY d"
    )
    return sql, [cutoff]


__all__ = [
    "build_acquisition_recent_query",
    "build_acquisition_summary_query",
    "build_acquisition_trend_query",
]
