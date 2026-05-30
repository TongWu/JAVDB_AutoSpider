"""Pure SQL builders for stats trend queries (ADR-018 Task 8 Slice 1)."""

from __future__ import annotations

from dataclasses import dataclass

from javdb.storage.db import _db_connection as _db_conn_mod

_SQL_BACKED_TREND_METRICS = frozenset(
    {
        "success_rate",
        "movies",
        "torrents",
        "history_growth",
        "pikpak",
        "dedup",
    }
)


@dataclass(frozen=True)
class StatsTrendQuery:
    db_path: str
    sql: str
    params: tuple[str, ...]


def is_sql_backed_trend_metric(metric: str) -> bool:
    return metric in _SQL_BACKED_TREND_METRICS


def build_stats_trend_query(*, metric: str, cutoff: str) -> StatsTrendQuery:
    """Return DB path + SQL + params for SQL-backed /api/stats/trend metrics."""
    return build_stats_trend_query_with_paths(
        metric=metric,
        cutoff=cutoff,
        reports_db_path=_db_conn_mod.REPORTS_DB_PATH,
        history_db_path=_db_conn_mod.HISTORY_DB_PATH,
        operations_db_path=_db_conn_mod.OPERATIONS_DB_PATH,
    )


def build_stats_trend_query_with_paths(
    *,
    metric: str,
    cutoff: str,
    reports_db_path: str,
    history_db_path: str,
    operations_db_path: str,
) -> StatsTrendQuery:
    """Return DB path + SQL + params for SQL-backed /api/stats/trend metrics."""
    if metric == "success_rate":
        return StatsTrendQuery(
            db_path=reports_db_path,
            sql=(
                "SELECT DATE(DateTimeCreated), "
                "CAST(SUM(CASE WHEN Status='committed' THEN 1 ELSE 0 END) AS REAL) / COUNT(*) "
                "FROM ReportSessions WHERE DateTimeCreated >= ? "
                "GROUP BY DATE(DateTimeCreated) ORDER BY DATE(DateTimeCreated)"
            ),
            params=(cutoff,),
        )
    if metric == "movies":
        return StatsTrendQuery(
            db_path=reports_db_path,
            sql=(
                "SELECT DATE(rs.DateTimeCreated) AS d, COUNT(rm.Id) "
                "FROM ReportSessions rs LEFT JOIN ReportMovies rm ON rm.SessionId = rs.Id "
                "WHERE rs.DateTimeCreated >= ? "
                "GROUP BY d ORDER BY d"
            ),
            params=(cutoff,),
        )
    if metric == "torrents":
        return StatsTrendQuery(
            db_path=reports_db_path,
            sql=(
                "SELECT DATE(rs.DateTimeCreated) AS d, COUNT(rt.Id) "
                "FROM ReportSessions rs "
                "LEFT JOIN ReportMovies rm ON rm.SessionId = rs.Id "
                "LEFT JOIN ReportTorrents rt ON rt.ReportMovieId = rm.Id "
                "WHERE rs.DateTimeCreated >= ? "
                "GROUP BY d ORDER BY d"
            ),
            params=(cutoff,),
        )
    if metric == "history_growth":
        return StatsTrendQuery(
            db_path=history_db_path,
            sql=(
                "SELECT DATE(DateTimeCreated), COUNT(*) "
                "FROM MovieHistory WHERE DateTimeCreated >= ? "
                "GROUP BY DATE(DateTimeCreated) ORDER BY DATE(DateTimeCreated)"
            ),
            params=(cutoff,),
        )
    if metric == "pikpak":
        return StatsTrendQuery(
            db_path=operations_db_path,
            sql=(
                "SELECT DATE(DateTimeUploadedToPikpak), COUNT(*) "
                "FROM PikpakHistory WHERE DateTimeUploadedToPikpak >= ? "
                "GROUP BY DATE(DateTimeUploadedToPikpak) ORDER BY DATE(DateTimeUploadedToPikpak)"
            ),
            params=(cutoff,),
        )
    if metric == "dedup":
        return StatsTrendQuery(
            db_path=operations_db_path,
            sql=(
                "SELECT DATE(DateTimeDetected), COALESCE(SUM(ExistingFolderSize), 0) "
                "FROM DedupRecords WHERE IsDeleted=1 AND DateTimeDetected >= ? "
                "GROUP BY DATE(DateTimeDetected) ORDER BY DATE(DateTimeDetected)"
            ),
            params=(cutoff,),
        )
    raise ValueError(f"metric has no SQL trend builder: {metric}")


__all__ = [
    "StatsTrendQuery",
    "build_stats_trend_query",
    "build_stats_trend_query_with_paths",
    "is_sql_backed_trend_metric",
]
