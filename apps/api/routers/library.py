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
from apps.api.schemas.library import (
    AcquisitionRecentItem,
    AcquisitionSummary,
    AcquisitionTrendPoint,
)
from javdb.ops.reconcile.models import ACQUISITION_STATES

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
