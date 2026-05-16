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
    SessionCommitPayload,
    SessionCommitResponse,
    SessionDetailResponse,
    SessionItem,
    SessionListResponse,
    SessionRollbackPayload,
    SessionRollbackResponse,
)
import javdb.storage.db.db_connection as _db_connection
from javdb.storage.repos.sessions_repo import SessionsRepo

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


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(
    session_id: str,
    _user=Depends(_require_auth),
) -> SessionDetailResponse:
    reports_path = _db_connection.REPORTS_DB_PATH
    with _db_connection.get_db(reports_path) as conn:
        repo = SessionsRepo(conn)
        row = repo.get(session_id)
        if not row:
            raise HTTPException(status_code=404, detail={"error": {"code": "session.not_found"}})
        movies, torrents = repo.get_writes(session_id)
    return SessionDetailResponse(
        session=_row_to_item(row),
        movies=movies,
        torrents=torrents,
    )


@router.post("/{session_id}/commit", response_model=SessionCommitResponse)
def post_commit(
    session_id: str,
    payload: SessionCommitPayload,
    _user=Depends(require_role("admin")),
) -> SessionCommitResponse:
    from javdb.storage.sessions import CommitRequest, commit_session
    reports_path = _db_connection.REPORTS_DB_PATH
    with _db_connection.get_db(reports_path) as conn:
        if not SessionsRepo(conn).get(session_id):
            raise HTTPException(status_code=404, detail={"error": {"code": "session.not_found"}})
    try:
        result = commit_session(CommitRequest(
            session_id=session_id,
            force=payload.force,
            drop_pending=payload.drop_pending,
        ))
        return SessionCommitResponse(
            session_id=result.session_id,
            new_state=result.new_state,
            pending_dropped=result.pending_dropped,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "session.not_found", "message": str(exc)}},
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "commit.failed", "message": str(exc)}},
        )


@router.post("/{session_id}/rollback", response_model=SessionRollbackResponse)
def post_rollback(
    session_id: str,
    payload: SessionRollbackPayload,
    _user=Depends(require_role("admin")),
) -> SessionRollbackResponse:
    from javdb.storage.rollback import (
        RollbackRequest,
        apply_rollback,
        plan_rollback,
    )
    reports_path = _db_connection.REPORTS_DB_PATH
    with _db_connection.get_db(reports_path) as conn:
        if not SessionsRepo(conn).get(session_id):
            raise HTTPException(status_code=404, detail={"error": {"code": "session.not_found"}})
    req = RollbackRequest(
        session_id=session_id,
        dry_run=payload.dry_run,
        include_pending=payload.include_pending,
        restore_from_audit=payload.restore_from_audit,
    )
    try:
        if payload.dry_run:
            result = plan_rollback(req)
            return SessionRollbackResponse(
                session_id=session_id,
                dry_run=True,
                actions=result.actions,
                summary=result.summary,
            )
        applied = apply_rollback(req)
        return SessionRollbackResponse(
            session_id=session_id,
            dry_run=False,
            actions=applied.applied,
            summary=applied.summary,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "session.not_found", "message": str(exc)}},
        )


@router.get("", response_model=SessionListResponse)
def list_sessions(
    state: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(_require_auth),
) -> SessionListResponse:
    reports_path = _db_connection.REPORTS_DB_PATH
    with _db_connection.get_db(reports_path) as conn:
        result = SessionsRepo(conn).list(state=state, cursor=cursor, limit=limit)
    return SessionListResponse(
        items=[_row_to_item(r) for r in result.items],
        next_cursor=result.next_cursor,
    )
