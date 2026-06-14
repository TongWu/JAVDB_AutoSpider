"""Watch-intent (watchlist) API routes (ADR-054 WS1)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.watchlist import (
    WatchIntentListResponse,
    WatchIntentResponse,
    WatchIntentUpsert,
)
from javdb.storage.repos.watchlist_repo import WatchIntentRepo

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

_NOT_FOUND = {"error": {"code": "watchlist.not_found", "message": "Record not found"}}
_INVALID_STATUS = {
    "error": {
        "code": "watchlist.invalid_status",
        "message": "status must be one of: want, viewed",
    }
}


def _row_to_intent(row: dict) -> WatchIntentResponse:
    return WatchIntentResponse(
        video_code=row["video_code"],
        href=row["href"],
        status=row["status"],
        notes=row.get("notes"),
        status_at=row.get("status_at"),
        updated_at=row["updated_at"],
    )


@router.get("", response_model=WatchIntentListResponse)
def list_watch_intents(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
):
    if status is not None and status not in {"want", "viewed"}:
        raise HTTPException(status_code=422, detail=_INVALID_STATUS)
    items, total = WatchIntentRepo().list(status=status, limit=limit, offset=offset)
    return WatchIntentListResponse(
        items=[_row_to_intent(r) for r in items], total=total
    )


@router.put("/{video_code}", response_model=WatchIntentResponse)
def upsert_watch_intent(
    video_code: str,
    body: WatchIntentUpsert,
    # Mutations are admin-only: a readonly account must not modify shared
    # watch-intent data (require_role still enforces auth via _require_auth).
    _admin=Depends(require_role("admin")),
):
    row = WatchIntentRepo().upsert(
        video_code=video_code, href=body.href, status=body.status, notes=body.notes
    )
    return _row_to_intent(row)


@router.get("/{video_code}", response_model=WatchIntentResponse)
def get_watch_intent(video_code: str, _user=Depends(_require_auth)):
    row = WatchIntentRepo().get(video_code)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _row_to_intent(row)


@router.delete("/{video_code}")
def delete_watch_intent(video_code: str, _admin=Depends(require_role("admin"))):
    deleted = WatchIntentRepo().delete(video_code)
    return {"deleted": deleted}
