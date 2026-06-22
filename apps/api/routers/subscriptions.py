"""Actor-subscription + new-works API routes (ADR-054 WS2)."""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.subscriptions import (
    ActorSubscriptionDeleteResponse,
    ActorSubscriptionListResponse,
    ActorSubscriptionResponse,
    ActorSubscriptionUpsert,
    NewWorkDismissResponse,
    NewWorkListResponse,
    NewWorkResponse,
)
from javdb.storage.repos.subscription_repo import (
    ActorSubscriptionRepo,
    NewWorksRepo,
)

subscriptions_router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])
new_works_router = APIRouter(prefix="/api/new-works", tags=["subscriptions"])

_SUB_NOT_FOUND = {
    "error": {"code": "subscriptions.not_found", "message": "Record not found"}
}
_NW_NOT_FOUND = {
    "error": {"code": "new_works.not_found", "message": "Record not found"}
}
_INVALID_ACTOR_HREF = {
    "error": {
        "code": "subscriptions.invalid_actor_href",
        "message": "actor_href must match /actors/<id>",
    }
}
_ACTOR_HREF_RE = re.compile(r"/actors/[^/]+")


def _row_to_sub(row: dict) -> ActorSubscriptionResponse:
    return ActorSubscriptionResponse(
        actor_href=row["actor_href"],
        actor_name=row.get("actor_name"),
        active=bool(row["active"]),
        last_seen_href=row.get("last_seen_href"),
        last_checked_at=row.get("last_checked_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_new_work(row: dict) -> NewWorkResponse:
    return NewWorkResponse(
        video_code=row["video_code"],
        href=row["href"],
        actor_href=row["actor_href"],
        title=row.get("title"),
        release_date=row.get("release_date"),
        discovered_at=row["discovered_at"],
        dismissed=bool(row["dismissed"]),
    )


@subscriptions_router.get("", response_model=ActorSubscriptionListResponse)
def list_subscriptions(
    active_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
):
    items, total = ActorSubscriptionRepo().list(
        active_only=active_only, limit=limit, offset=offset
    )
    return ActorSubscriptionListResponse(
        items=[_row_to_sub(r) for r in items], total=total
    )


@subscriptions_router.put("/{actor_href:path}", response_model=ActorSubscriptionResponse)
def upsert_subscription(
    actor_href: str,
    body: ActorSubscriptionUpsert,
    _admin=Depends(require_role("admin")),
):
    row = ActorSubscriptionRepo().upsert(
        actor_href=_norm_actor_href(actor_href),
        actor_name=body.actor_name,
        active=1 if body.active else 0,
    )
    return _row_to_sub(row)


@subscriptions_router.get("/{actor_href:path}", response_model=ActorSubscriptionResponse)
def get_subscription(actor_href: str, _user=Depends(_require_auth)):
    row = ActorSubscriptionRepo().get(_norm_actor_href(actor_href))
    if row is None:
        raise HTTPException(status_code=404, detail=_SUB_NOT_FOUND)
    return _row_to_sub(row)


@subscriptions_router.delete(
    "/{actor_href:path}", response_model=ActorSubscriptionDeleteResponse
)
def delete_subscription(actor_href: str, _admin=Depends(require_role("admin"))):
    deleted = ActorSubscriptionRepo().delete(_norm_actor_href(actor_href))
    return ActorSubscriptionDeleteResponse(deleted=deleted)


@new_works_router.get("", response_model=NewWorkListResponse)
def list_new_works(
    actor_href: Optional[str] = Query(default=None),
    include_dismissed: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
):
    items, total = NewWorksRepo().list(
        actor_href=actor_href,
        include_dismissed=include_dismissed,
        limit=limit,
        offset=offset,
    )
    return NewWorkListResponse(items=[_row_to_new_work(r) for r in items], total=total)


@new_works_router.post("/{video_code}/dismiss", response_model=NewWorkDismissResponse)
def dismiss_new_work(
    video_code: str,
    actor_href: Optional[str] = Query(default=None),
    _admin=Depends(require_role("admin")),
):
    # actor_href scopes the dismiss to a single followed actor's feed row;
    # omitting it dismisses the release across every actor's feed (back-compat).
    # Query param mirrors the sibling GET /api/new-works?actor_href=… contract.
    dismissed = NewWorksRepo().dismiss(video_code, actor_href=actor_href)
    if not dismissed:
        raise HTTPException(status_code=404, detail=_NW_NOT_FOUND)
    return NewWorkDismissResponse(dismissed=True)


def _norm_actor_href(raw: str) -> str:
    """Normalize a path-captured actor identifier to /actors/<id>."""
    raw = raw.strip("/")
    normalized = "/" + raw if raw else raw
    if _ACTOR_HREF_RE.fullmatch(normalized) is None:
        raise HTTPException(status_code=422, detail=_INVALID_ACTOR_HREF)
    return normalized
