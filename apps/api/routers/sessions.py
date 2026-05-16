"""Sessions endpoints.

GET    /api/sessions              — cursor-paginated list of ReportSessions.
GET    /api/sessions/{id}         — session detail + associated writes.
POST   /api/sessions/{id}/rollback — rollback (dry-run or live).
POST   /api/sessions/{id}/commit  — force-commit a stuck-finalizing session.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.capabilities_payloads import (
    SessionItem,
    SessionListResponse,
)
from packages.python.javdb_platform.db_connection import REPORTS_DB_PATH, get_db
from packages.python.javdb_platform.db_layer.sessions_repo import SessionsRepo

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _row_to_item(row) -> SessionItem:
    return SessionItem(
        session_id=row.session_id,
        state=row.state,
        write_mode=row.write_mode,
        run_id=row.run_id,
        run_attempt=row.run_attempt,
        created_at=row.created_at,
    )


@router.get("", response_model=SessionListResponse)
def list_sessions(
    state: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(_require_auth),
) -> SessionListResponse:
    with get_db(REPORTS_DB_PATH) as conn:
        result = SessionsRepo(conn).list(state=state, cursor=cursor, limit=limit)
    return SessionListResponse(
        items=[_row_to_item(r) for r in result.items],
        next_cursor=result.next_cursor,
    )
