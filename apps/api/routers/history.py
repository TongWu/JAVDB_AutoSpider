"""History search and export endpoints (Phase 2, Task 1).

GET /api/history/movies          — search MovieHistory with cursor pagination
GET /api/history/movies/export   — stream full-dataset CSV
GET /api/history/torrents        — search TorrentHistory with cursor pagination
GET /api/history/torrents/export — stream full-dataset CSV
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from apps.api.infra.auth import _require_auth
from apps.api.schemas.history import (
    MovieSearchItem,
    MovieSearchResponse,
    TorrentSearchItem,
    TorrentSearchResponse,
)
from javdb.storage.repos.history_repo import HistoryRepo

router = APIRouter(prefix="/api/history", tags=["history"])


def _movie_row_to_item(row: dict) -> MovieSearchItem:
    return MovieSearchItem(
        id=row["Id"],
        video_code=row["VideoCode"],
        href=row["Href"],
        actor_name=row.get("ActorName"),
        actor_gender=row.get("ActorGender"),
        supporting_actors=row.get("SupportingActors"),
        perfect_match=bool(row.get("PerfectMatchIndicator", 0)),
        hi_res=bool(row.get("HiResIndicator", 0)),
        datetime_created=row.get("DateTimeCreated"),
        datetime_updated=row.get("DateTimeUpdated"),
        session_id=row.get("SessionId"),
        torrent_count=row.get("torrent_count", 0),
    )


def _torrent_row_to_item(row: dict) -> TorrentSearchItem:
    return TorrentSearchItem(
        id=row["Id"],
        movie_video_code=row.get("movie_video_code"),
        movie_href=row.get("movie_href"),
        magnet_uri=row.get("MagnetUri"),
        size=row.get("Size"),
        subtitle_indicator=int(row.get("SubtitleIndicator") or 0),
        censor_indicator=int(row.get("CensorIndicator") or 0),
        resolution_type=int(row.get("ResolutionType") or 0),
        file_count=int(row.get("FileCount") or 0),
        datetime_created=row.get("DateTimeCreated"),
        session_id=row.get("SessionId"),
    )


@router.get("/movies", response_model=MovieSearchResponse)
def search_movies(
    q: Optional[str] = Query(default=None),
    actor: Optional[str] = Query(default=None),
    perfect_match: Optional[bool] = Query(default=None),
    hi_res: Optional[bool] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(_require_auth),
) -> MovieSearchResponse:
    repo = HistoryRepo()
    items, next_cursor, total = repo.search_movies(
        q=q,
        actor=actor,
        perfect_match=perfect_match,
        hi_res=hi_res,
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    return MovieSearchResponse(
        items=[_movie_row_to_item(r) for r in items],
        next_cursor=next_cursor,
        total_estimate=total,
    )


@router.get("/movies/export")
def export_movies_csv(
    q: Optional[str] = Query(default=None),
    actor: Optional[str] = Query(default=None),
    perfect_match: Optional[bool] = Query(default=None),
    hi_res: Optional[bool] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    _user=Depends(_require_auth),
) -> StreamingResponse:
    repo = HistoryRepo()
    rows = repo.export_movies_csv(
        q=q,
        actor=actor,
        perfect_match=perfect_match,
        hi_res=hi_res,
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
    )
    return StreamingResponse(
        rows,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=movies.csv"},
    )


@router.get("/torrents", response_model=TorrentSearchResponse)
def search_torrents(
    q: Optional[str] = Query(default=None),
    resolution_type: Optional[int] = Query(default=None),
    has_subtitle: Optional[bool] = Query(default=None),
    uncensored: Optional[bool] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(_require_auth),
) -> TorrentSearchResponse:
    repo = HistoryRepo()
    items, next_cursor, total = repo.search_torrents(
        q=q,
        resolution_type=resolution_type,
        has_subtitle=has_subtitle,
        uncensored=uncensored,
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    return TorrentSearchResponse(
        items=[_torrent_row_to_item(r) for r in items],
        next_cursor=next_cursor,
        total_estimate=total,
    )


@router.get("/torrents/export")
def export_torrents_csv(
    q: Optional[str] = Query(default=None),
    resolution_type: Optional[int] = Query(default=None),
    has_subtitle: Optional[bool] = Query(default=None),
    uncensored: Optional[bool] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    _user=Depends(_require_auth),
) -> StreamingResponse:
    repo = HistoryRepo()
    rows = repo.export_torrents_csv(
        q=q,
        resolution_type=resolution_type,
        has_subtitle=has_subtitle,
        uncensored=uncensored,
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
    )
    return StreamingResponse(
        rows,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=torrents.csv"},
    )
