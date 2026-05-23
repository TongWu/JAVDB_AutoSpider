"""Statistics endpoints — summary dashboard and trend data.

GET /api/stats/summary — aggregate counts across all databases
GET /api/stats/trend   — time-series data for a given metric
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth
from apps.api.schemas.stats import StatsSummary, TrendDataPoint, TrendResponse
from apps.api.services import context
from javdb.storage.db._db_connection import (
    HISTORY_DB_PATH,
    OPERATIONS_DB_PATH,
    REPORTS_DB_PATH,
    get_db,
)

router = APIRouter(prefix="/api/stats", tags=["stats"])

logger = logging.getLogger(__name__)

_LOGS_DIR = context.RESOLVED_JOB_LOG_DIR

_VALID_METRICS = {
    "success_rate",
    "duration",
    "movies",
    "torrents",
    "history_growth",
    "pikpak",
    "dedup",
    "proxy_bans",
}

_VALID_PERIODS = {"7d", "30d", "90d"}

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_query_one(db_path: str, sql: str, params: tuple = ()) -> Optional[Any]:
    """Execute a single-row query, returning None on any error."""
    try:
        with get_db(db_path) as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            return row[0] if not isinstance(row, dict) else list(row.values())[0]
    except Exception:
        logger.debug("safe_query_one failed for %s", sql, exc_info=True)
        return None


def _safe_query_all(db_path: str, sql: str, params: tuple = ()) -> List[tuple]:
    """Execute a multi-row query, returning [] on any error."""
    try:
        with get_db(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [(row[0], row[1]) for row in rows]
    except Exception:
        logger.debug("safe_query_all failed for %s", sql, exc_info=True)
        return []


def _count_proxy_bans_in_logs(days: int) -> int:
    """Count lines containing 'ban' (case-insensitive) in recent log files."""
    if not _LOGS_DIR.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0

    for meta_path in _LOGS_DIR.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
        except (json.JSONDecodeError, OSError):
            continue

        created_at = meta.get("created_at", "")
        if not created_at or created_at < cutoff.isoformat():
            continue

        log_path = meta_path.with_suffix("").with_suffix(".log")
        if not log_path.exists():
            continue

        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if "ban" in line.lower():
                    count += 1
        except OSError:
            continue

    return count


def _count_proxy_bans_by_date(days: int) -> List[tuple]:
    """Count 'ban' lines per day from log files within the period."""
    if not _LOGS_DIR.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    counts: Dict[str, int] = {}

    for meta_path in _LOGS_DIR.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
        except (json.JSONDecodeError, OSError):
            continue

        created_at = meta.get("created_at", "")
        if not created_at or created_at < cutoff.isoformat():
            continue

        date_str = created_at[:10]

        log_path = meta_path.with_suffix("").with_suffix(".log")
        if not log_path.exists():
            continue

        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            ban_count = sum(1 for line in text.splitlines() if "ban" in line.lower())
            if ban_count > 0:
                counts[date_str] = counts.get(date_str, 0) + ban_count
        except OSError:
            continue

    return sorted(counts.items())


# ---------------------------------------------------------------------------
# GET /api/stats/summary
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=StatsSummary)
def stats_summary(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> StatsSummary:
    """Aggregate statistics across all databases."""
    total_runs = _safe_query_one(
        REPORTS_DB_PATH,
        "SELECT COUNT(*) FROM ReportSessions",
    ) or 0

    success_rate_raw = _safe_query_one(
        REPORTS_DB_PATH,
        "SELECT CAST(SUM(CASE WHEN Status='committed' THEN 1 ELSE 0 END) AS REAL) / COUNT(*) "
        "FROM ReportSessions",
    )
    success_rate = float(success_rate_raw) if success_rate_raw is not None else None

    total_movies = _safe_query_one(
        HISTORY_DB_PATH,
        "SELECT COUNT(*) FROM MovieHistory",
    ) or 0

    total_torrents = _safe_query_one(
        REPORTS_DB_PATH,
        "SELECT COUNT(*) FROM ReportTorrents",
    ) or 0

    total_pikpak = _safe_query_one(
        OPERATIONS_DB_PATH,
        "SELECT COUNT(*) FROM PikpakHistory",
    ) or 0

    total_dedup_freed_bytes = _safe_query_one(
        OPERATIONS_DB_PATH,
        "SELECT COALESCE(SUM(ExistingFolderSize), 0) FROM DedupRecords",
    ) or 0

    proxy_bans_last_7d = _count_proxy_bans_in_logs(7)

    return StatsSummary(
        total_runs=int(total_runs),
        success_rate=success_rate,
        avg_duration_seconds=None,
        total_movies=int(total_movies),
        total_torrents=int(total_torrents),
        total_pikpak=int(total_pikpak),
        total_dedup_freed_bytes=int(total_dedup_freed_bytes),
        proxy_bans_last_7d=proxy_bans_last_7d,
    )


# ---------------------------------------------------------------------------
# GET /api/stats/trend
# ---------------------------------------------------------------------------


@router.get("/trend", response_model=TrendResponse)
def stats_trend(
    metric: str = Query(...),
    period: str = Query("30d"),
    _user: Dict[str, Any] = Depends(_require_auth),
) -> TrendResponse:
    """Time-series data for a given metric over the requested period."""
    if metric not in _VALID_METRICS:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "stats.invalid_metric", "message": f"Invalid metric: {metric}. Valid: {sorted(_VALID_METRICS)}"}},
        )
    if period not in _VALID_PERIODS:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "stats.invalid_period", "message": f"Invalid period: {period}. Valid: {sorted(_VALID_PERIODS)}"}},
        )

    days = _PERIOD_DAYS[period]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    data_points: List[TrendDataPoint] = []

    if metric == "success_rate":
        rows = _safe_query_all(
            REPORTS_DB_PATH,
            "SELECT DATE(DateTimeCreated), "
            "CAST(SUM(CASE WHEN Status='committed' THEN 1 ELSE 0 END) AS REAL) / COUNT(*) "
            "FROM ReportSessions WHERE DateTimeCreated >= ? "
            "GROUP BY DATE(DateTimeCreated) ORDER BY DATE(DateTimeCreated)",
            (cutoff,),
        )
        data_points = [TrendDataPoint(date=r[0], value=r[1]) for r in rows]

    elif metric == "duration":
        pass

    elif metric == "movies":
        rows = _safe_query_all(
            REPORTS_DB_PATH,
            "SELECT DATE(rs.DateTimeCreated) AS d, COUNT(rm.Id) "
            "FROM ReportSessions rs LEFT JOIN ReportMovies rm ON rm.SessionId = rs.Id "
            "WHERE rs.DateTimeCreated >= ? "
            "GROUP BY d ORDER BY d",
            (cutoff,),
        )
        data_points = [TrendDataPoint(date=r[0], value=r[1]) for r in rows]

    elif metric == "torrents":
        rows = _safe_query_all(
            REPORTS_DB_PATH,
            "SELECT DATE(rs.DateTimeCreated) AS d, COUNT(rt.Id) "
            "FROM ReportSessions rs "
            "LEFT JOIN ReportMovies rm ON rm.SessionId = rs.Id "
            "LEFT JOIN ReportTorrents rt ON rt.ReportMovieId = rm.Id "
            "WHERE rs.DateTimeCreated >= ? "
            "GROUP BY d ORDER BY d",
            (cutoff,),
        )
        data_points = [TrendDataPoint(date=r[0], value=r[1]) for r in rows]

    elif metric == "history_growth":
        rows = _safe_query_all(
            HISTORY_DB_PATH,
            "SELECT DATE(DateTimeCreated), COUNT(*) "
            "FROM MovieHistory WHERE DateTimeCreated >= ? "
            "GROUP BY DATE(DateTimeCreated) ORDER BY DATE(DateTimeCreated)",
            (cutoff,),
        )
        data_points = [TrendDataPoint(date=r[0], value=r[1]) for r in rows]

    elif metric == "pikpak":
        rows = _safe_query_all(
            OPERATIONS_DB_PATH,
            "SELECT DATE(DateTimeUploadedToPikpak), COUNT(*) "
            "FROM PikpakHistory WHERE DateTimeUploadedToPikpak >= ? "
            "GROUP BY DATE(DateTimeUploadedToPikpak) ORDER BY DATE(DateTimeUploadedToPikpak)",
            (cutoff,),
        )
        data_points = [TrendDataPoint(date=r[0], value=r[1]) for r in rows]

    elif metric == "dedup":
        rows = _safe_query_all(
            OPERATIONS_DB_PATH,
            "SELECT DATE(DateTimeDetected), COALESCE(SUM(ExistingFolderSize), 0) "
            "FROM DedupRecords WHERE DateTimeDetected >= ? "
            "GROUP BY DATE(DateTimeDetected) ORDER BY DATE(DateTimeDetected)",
            (cutoff,),
        )
        data_points = [TrendDataPoint(date=r[0], value=r[1]) for r in rows]

    elif metric == "proxy_bans":
        ban_rows = _count_proxy_bans_by_date(days)
        data_points = [TrendDataPoint(date=r[0], value=r[1]) for r in ban_rows]

    return TrendResponse(metric=metric, period=period, data_points=data_points)


__all__ = [
    "router",
]
