"""Library acquisition endpoints (ADR-034 FE-1).

GET /api/library/acquisition/summary  — funnel/KPI counts
GET /api/library/acquisition/recent   — newest-first paginated rows
GET /api/library/acquisition/trend    — daily terminal-state counts
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth
from apps.api.routers.library_query_builders import (
    build_acquisition_recent_query,
    build_acquisition_summary_query,
    build_acquisition_trend_query,
)
from apps.api.routers.library_ownership_query_builders import (
    build_ownership_recent_query,
    build_ownership_summary_by_source_query,
    build_ownership_summary_distinct_query,
)
from apps.api.routers.library_consumption_query_builders import (
    build_consumption_recent_query,
    build_consumption_summary_query,
    build_consumption_summary_unresolved_count_query,
    build_consumption_trend_query,
    build_consumption_unresolved_query,
)
from apps.api.schemas.library import (
    AcquisitionRecentItem,
    AcquisitionSummary,
    AcquisitionTrendPoint,
)
from apps.api.schemas.library_ownership import (
    OwnershipRecentItem,
    OwnershipSourceBreakdown,
    OwnershipSummary,
)
from apps.api.schemas.library_consumption import (
    ConsumptionRecentItem,
    ConsumptionSummary,
    ConsumptionTrendPoint,
    UnresolvedItem,
)
from javdb.ops.reconcile.models import ACQUISITION_STATES, OWNERSHIP_SOURCES

router = APIRouter(prefix="/api/library", tags=["library"])

# Domain-validation 400 envelope. FastAPI wraps HTTPException(detail=...) as
# {"detail": <detail>}, so the wire body is {"detail": {"error": {code, message}}}
# — the schema below mirrors that exact shape (ADR-034 FE-1; TS Worker at parity).
_ERROR_ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "detail": {
            "type": "object",
            "properties": {
                "error": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["code", "message"],
                }
            },
            "required": ["error"],
        }
    },
    "required": ["detail"],
}


def _domain_400_response(description: str) -> dict:
    return {
        400: {
            "description": description,
            "content": {"application/json": {"schema": _ERROR_ENVELOPE_SCHEMA}},
        }
    }


_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}
# in_library omitted — Phase-2-gated; its rows still count toward total
_SUMMARY_KEYS = ("queued", "downloading", "completed", "stalled", "failed", "total")
_RECENT_COLS = (
    "qb_hash", "video_code", "href", "category",
    "state", "queued_at", "completed_at", "last_seen_at",
)

_OWNERSHIP_SOURCES = OWNERSHIP_SOURCES  # ("qb", "nas", "gdrive", "pikpak")
_OWNERSHIP_RECENT_COLS = (
    "video_code", "source", "category", "path", "size", "present", "observed_at",
)

_CONSUMPTION_RECENT_COLS = (
    "video_code", "source_type", "instance", "library_id", "library_name",
    "watched", "progress_pct", "play_count", "rating", "watched_at",
    "resolved_confidence", "observed_at",
)
_UNRESOLVED_COLS = (
    "instance", "source_type", "library_id", "library_name",
    "item_id", "raw_title", "file_path", "observed_at",
)


@router.get("/acquisition/summary", response_model=AcquisitionSummary)
def acquisition_summary(_user=Depends(_require_auth)) -> AcquisitionSummary:
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql, bindings = build_acquisition_summary_query()
    with get_db(OPERATIONS_DB_PATH) as conn:
        row = conn.execute(sql, bindings).fetchone()
    # Read by column name: D1 returns mapping rows; sqlite3 uses Row factory (ADR-018 dual-backend).
    values = {k: row[k] for k in _SUMMARY_KEYS} if row else {k: 0 for k in _SUMMARY_KEYS}
    return AcquisitionSummary(**values)


@router.get(
    "/acquisition/recent",
    response_model=list[AcquisitionRecentItem],
    responses=_domain_400_response("Invalid state filter (library.invalid_state)"),
)
def acquisition_recent(
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
) -> list[AcquisitionRecentItem]:
    if state is not None and state not in ACQUISITION_STATES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_state", "message": f"Invalid state: {state}"}},
        )
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql, bindings = build_acquisition_recent_query(state=state, limit=limit, offset=offset)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [AcquisitionRecentItem(**{c: r[c] for c in _RECENT_COLS}) for r in rows]


@router.get(
    "/acquisition/trend",
    response_model=list[AcquisitionTrendPoint],
    responses=_domain_400_response("Invalid period (library.invalid_period)"),
)
def acquisition_trend(
    period: str = Query(default="30d"),
    _user=Depends(_require_auth),
) -> list[AcquisitionTrendPoint]:
    if period not in _PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_period", "message": f"Invalid period: {period}"}},
        )
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_PERIOD_DAYS[period])).strftime("%Y-%m-%d")
    sql, bindings = build_acquisition_trend_query(cutoff=cutoff)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [
        AcquisitionTrendPoint(
            date=r["d"], completed=r["completed"], stalled=r["stalled"], failed=r["failed"]
        )
        for r in rows
    ]


@router.get("/ownership/summary", response_model=OwnershipSummary)
def ownership_summary(_user=Depends(_require_auth)) -> OwnershipSummary:
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql_per_source, b1 = build_ownership_summary_by_source_query()
    sql_distinct, b2 = build_ownership_summary_distinct_query()
    with get_db(OPERATIONS_DB_PATH) as conn:
        per_source_rows = conn.execute(sql_per_source, b1).fetchall()
        distinct_row = conn.execute(sql_distinct, b2).fetchone()
    total = (distinct_row["total_owned_titles"] if distinct_row else 0) or 0
    by_source = [
        OwnershipSourceBreakdown(
            source=r["source"],
            unique_titles=r["unique_titles"],
            present_rows=r["present_rows"],
            total_bytes=r["total_bytes"],
        )
        for r in per_source_rows
    ]
    return OwnershipSummary(total_owned_titles=total, by_source=by_source)


@router.get(
    "/ownership/recent",
    response_model=list[OwnershipRecentItem],
    responses=_domain_400_response("Invalid source filter (library.invalid_source)"),
)
def ownership_recent(
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
) -> list[OwnershipRecentItem]:
    if source is not None and source not in _OWNERSHIP_SOURCES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_source", "message": f"Invalid source: {source}"}},
        )
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql, bindings = build_ownership_recent_query(source=source, limit=limit, offset=offset)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [OwnershipRecentItem(**{c: r[c] for c in _OWNERSHIP_RECENT_COLS}) for r in rows]


@router.get("/consumption/summary", response_model=ConsumptionSummary)
def consumption_summary(_user=Depends(_require_auth)) -> ConsumptionSummary:
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql_sig, b1 = build_consumption_summary_query()
    sql_unres, b2 = build_consumption_summary_unresolved_count_query()
    with get_db(OPERATIONS_DB_PATH) as conn:
        sig_row = conn.execute(sql_sig, b1).fetchone()
        unres_row = conn.execute(sql_unres, b2).fetchone()
    if sig_row:
        return ConsumptionSummary(
            total_signals=sig_row["total_signals"] or 0,
            watched_count=sig_row["watched_count"] or 0,
            unwatched_count=sig_row["unwatched_count"] or 0,
            avg_rating=sig_row["avg_rating"],  # None when no ratings
            unique_titles=sig_row["unique_titles"] or 0,
            instance_count=sig_row["instance_count"] or 0,
            unresolved_count=(unres_row["unresolved_count"] if unres_row else 0) or 0,
        )
    return ConsumptionSummary(
        total_signals=0, watched_count=0, unwatched_count=0,
        avg_rating=None, unique_titles=0, instance_count=0,
        unresolved_count=(unres_row["unresolved_count"] if unres_row else 0) or 0,
    )


@router.get(
    "/consumption/recent",
    response_model=list[ConsumptionRecentItem],
    responses=_domain_400_response("Invalid watched filter (library.invalid_watched)"),
)
def consumption_recent(
    instance: str | None = Query(default=None),
    watched: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
) -> list[ConsumptionRecentItem]:
    # Parse watched string → bool | None
    watched_bool: bool | None = None
    if watched == "true":
        watched_bool = True
    elif watched == "false":
        watched_bool = False
    elif watched is not None:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_watched", "message": f"Invalid watched: {watched}"}},
        )
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql, bindings = build_consumption_recent_query(
        instance=instance, watched=watched_bool, limit=limit, offset=offset
    )
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    # Convert watched INTEGER (0/1/None) to bool/None
    result = []
    for r in rows:
        d = {c: r[c] for c in _CONSUMPTION_RECENT_COLS}
        if d["watched"] is not None:
            d["watched"] = bool(d["watched"])
        result.append(ConsumptionRecentItem(**d))
    return result


@router.get(
    "/consumption/trend",
    response_model=list[ConsumptionTrendPoint],
    responses=_domain_400_response("Invalid period (library.invalid_period)"),
)
def consumption_trend(
    period: str = Query(default="30d"),
    _user=Depends(_require_auth),
) -> list[ConsumptionTrendPoint]:
    if period not in _PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_period", "message": f"Invalid period: {period}"}},
        )
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_PERIOD_DAYS[period])).strftime("%Y-%m-%d")
    sql, bindings = build_consumption_trend_query(cutoff=cutoff)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [
        ConsumptionTrendPoint(date=r["d"], watched=r["watched"], total_signals=r["total_signals"])
        for r in rows
    ]


@router.get(
    "/consumption/unresolved",
    response_model=list[UnresolvedItem],
)
def consumption_unresolved(
    instance: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
) -> list[UnresolvedItem]:
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql, bindings = build_consumption_unresolved_query(instance=instance, limit=limit, offset=offset)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [UnresolvedItem(**{c: r[c] for c in _UNRESOLVED_COLS}) for r in rows]
