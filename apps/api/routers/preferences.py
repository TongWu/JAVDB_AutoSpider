"""Preferences and metadata API routes (ADR-022)."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth
from apps.api.schemas.preferences import (
    ContentPreferenceListResponse,
    ContentPreferenceResponse,
    ContentPreferenceUpsert,
    MovieMetadataResponse,
    MovieRatingListResponse,
    MovieRatingResponse,
    MovieRatingUpsert,
)
from javdb.storage.repos.metadata_repo import MetadataRepo
from javdb.storage.repos.preference_repo import PreferenceRepo
from javdb.storage.preference_tags import VALID_TAGS

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

_NOT_FOUND = {
    "error": {"code": "preferences.not_found", "message": "Record not found"}
}

_VALID_CONTENT_TYPES = {"actor", "category", "maker", "director"}
_INVALID_CONTENT_TYPE = {
    "error": {
        "code": "preferences.invalid_content_type",
        "message": "content_type must be one of: actor, category, maker, director",
    }
}


def _parse_json_field(value: Optional[str]):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _row_to_metadata(row: dict) -> MovieMetadataResponse:
    return MovieMetadataResponse(
        href=row["href"],
        title=row.get("title"),
        video_code=row.get("video_code"),
        release_date=row.get("release_date"),
        duration_minutes=row.get("duration_minutes"),
        rate=row.get("rate"),
        comment_count=row.get("comment_count"),
        review_count=row.get("review_count"),
        want_count=row.get("want_count"),
        watched_count=row.get("watched_count"),
        maker=_parse_json_field(row.get("maker")),
        publisher=_parse_json_field(row.get("publisher")),
        series=_parse_json_field(row.get("series")),
        directors=_parse_json_field(row.get("directors")),
        categories=_parse_json_field(row.get("categories")),
        poster_url=row.get("poster_url"),
        fanart_urls=_parse_json_field(row.get("fanart_urls")),
        trailer_url=row.get("trailer_url"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _row_to_rating(row: dict) -> MovieRatingResponse:
    return MovieRatingResponse(
        href=row["href"],
        video_code=row["video_code"],
        rating=row.get("rating"),
        tags=_parse_json_field(row.get("tags")) or [],
        notes=row.get("notes"),
        rated_at=row.get("rated_at"),
        updated_at=row.get("updated_at"),
    )


def _row_to_pref(row: dict) -> ContentPreferenceResponse:
    return ContentPreferenceResponse(
        content_type=row["content_type"],
        content_id=row["content_id"],
        content_name=row["content_name"],
        hearted=bool(row.get("hearted", 0)),
        weight=row.get("weight", 1.0),
        updated_at=row.get("updated_at"),
    )


@router.put("/movies/{href:path}/rating", response_model=MovieRatingResponse)
def upsert_movie_rating(
    href: str,
    body: MovieRatingUpsert,
    _user=Depends(_require_auth),
):
    invalid = [t for t in body.tags if t not in VALID_TAGS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "preferences.invalid_tags",
                    "message": f"Unknown tags: {invalid}. Valid: {sorted(VALID_TAGS)}",
                }
            },
        )
    row = PreferenceRepo().upsert_rating(
        href=href, rating=body.rating, tags=body.tags, notes=body.notes
    )
    return _row_to_rating(row)


@router.get("/movies/{href:path}/rating", response_model=MovieRatingResponse)
def get_movie_rating(href: str, _user=Depends(_require_auth)):
    row = PreferenceRepo().get_rating(href)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _row_to_rating(row)


@router.get("/movies/ratings", response_model=MovieRatingListResponse)
def list_movie_ratings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
):
    items, total = PreferenceRepo().list_ratings(limit=limit, offset=offset)
    return MovieRatingListResponse(
        items=[_row_to_rating(r) for r in items], total=total
    )


@router.put("/{content_type}/{content_id:path}", response_model=ContentPreferenceResponse)
def upsert_content_preference(
    content_type: str,
    content_id: str,
    body: ContentPreferenceUpsert,
    _user=Depends(_require_auth),
):
    if content_type not in _VALID_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=_INVALID_CONTENT_TYPE)
    row = PreferenceRepo().upsert_preference(
        content_type=content_type,
        content_id=content_id,
        content_name=body.content_name,
        hearted=body.hearted,
        weight=body.weight,
    )
    return _row_to_pref(row)


@router.get("/", response_model=ContentPreferenceListResponse)
def list_content_preferences(
    content_type: Optional[str] = None,
    hearted_only: bool = False,
    _user=Depends(_require_auth),
):
    if content_type is not None and content_type not in _VALID_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=_INVALID_CONTENT_TYPE)
    items = PreferenceRepo().list_preferences(
        content_type=content_type, hearted_only=hearted_only,
    )
    return ContentPreferenceListResponse(items=[_row_to_pref(r) for r in items])


@router.get("/metadata/{href:path}", response_model=MovieMetadataResponse)
def get_movie_metadata(href: str, _user=Depends(_require_auth)):
    row = MetadataRepo().get(href)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _row_to_metadata(row)
