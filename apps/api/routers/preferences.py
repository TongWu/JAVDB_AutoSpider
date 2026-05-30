"""Preferences and metadata API routes (ADR-022)."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from apps.api.infra.auth import _require_auth
from apps.api.schemas.preferences import MovieMetadataResponse
from javdb.storage.repos.metadata_repo import MetadataRepo

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

_NOT_FOUND = {
    "error": {"code": "preferences.not_found", "message": "Record not found"}
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


@router.get("/metadata/{href:path}", response_model=MovieMetadataResponse)
def get_movie_metadata(href: str, _user=Depends(_require_auth)):
    row = MetadataRepo().get(href)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _row_to_metadata(row)
