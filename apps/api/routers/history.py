"""History search and export endpoints (Phase 2, Task 1).

GET /api/history/movies          — search MovieHistory with cursor pagination
GET /api/history/movies/export   — stream full-dataset CSV
GET /api/history/torrents        — search TorrentHistory with cursor pagination
GET /api/history/torrents/export — stream full-dataset CSV
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from apps.api.infra.auth import _require_auth
from apps.api.schemas.history import (
    MovieSearchItem,
    MovieSearchParams,
    MovieSearchResponse,
    TorrentSearchItem,
    TorrentSearchParams,
    TorrentSearchResponse,
)
from javdb.storage.repos.history_repo import HistoryRepo

router = APIRouter(prefix="/api/history", tags=["history"])

_INVALID_CURSOR = {"error": {"code": "history.invalid_cursor", "message": "cursor is malformed"}}
_INVALID_DATE = {"error": {"code": "history.invalid_date", "message": "date_from or date_to could not be parsed"}}


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
    params: MovieSearchParams = Depends(),
    _user=Depends(_require_auth),
) -> MovieSearchResponse:
    repo = HistoryRepo()
    try:
        items, next_cursor, total = repo.search_movies(
            q=params.q,
            actor=params.actor,
            perfect_match=params.perfect_match,
            hi_res=params.hi_res,
            session_id=params.session_id,
            date_from=params.date_from,
            date_to=params.date_to,
            cursor=params.cursor,
            limit=params.limit,
        )
    except ValueError as exc:
        msg = str(exc)
        if "cursor" in msg:
            raise HTTPException(status_code=400, detail=_INVALID_CURSOR)
        raise HTTPException(status_code=400, detail=_INVALID_DATE)
    return MovieSearchResponse(
        items=[_movie_row_to_item(r) for r in items],
        next_cursor=next_cursor,
        total_estimate=total,
    )


@router.get("/movies/export")
def export_movies_csv(
    params: MovieSearchParams = Depends(),
    _user=Depends(_require_auth),
) -> StreamingResponse:
    repo = HistoryRepo()
    try:
        rows = repo.export_movies_csv(
            q=params.q,
            actor=params.actor,
            perfect_match=params.perfect_match,
            hi_res=params.hi_res,
            session_id=params.session_id,
            date_from=params.date_from,
            date_to=params.date_to,
        )
        # Consume the header eagerly so date-parse errors raise before we return 200.
        # The generator is then chained back for the data rows.
        header_chunk = next(rows)
    except ValueError:
        raise HTTPException(status_code=400, detail=_INVALID_DATE)

    def _stream():
        yield header_chunk
        yield from rows

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=movies.csv"},
    )


@router.get("/torrents", response_model=TorrentSearchResponse)
def search_torrents(
    params: TorrentSearchParams = Depends(),
    _user=Depends(_require_auth),
) -> TorrentSearchResponse:
    repo = HistoryRepo()
    try:
        items, next_cursor, total = repo.search_torrents(
            q=params.q,
            resolution_type=params.resolution_type,
            has_subtitle=params.has_subtitle,
            uncensored=params.uncensored,
            session_id=params.session_id,
            date_from=params.date_from,
            date_to=params.date_to,
            cursor=params.cursor,
            limit=params.limit,
        )
    except ValueError as exc:
        msg = str(exc)
        if "cursor" in msg:
            raise HTTPException(status_code=400, detail=_INVALID_CURSOR)
        raise HTTPException(status_code=400, detail=_INVALID_DATE)
    return TorrentSearchResponse(
        items=[_torrent_row_to_item(r) for r in items],
        next_cursor=next_cursor,
        total_estimate=total,
    )


@router.get("/torrents/export")
def export_torrents_csv(
    params: TorrentSearchParams = Depends(),
    _user=Depends(_require_auth),
) -> StreamingResponse:
    repo = HistoryRepo()
    try:
        rows = repo.export_torrents_csv(
            q=params.q,
            resolution_type=params.resolution_type,
            has_subtitle=params.has_subtitle,
            uncensored=params.uncensored,
            session_id=params.session_id,
            date_from=params.date_from,
            date_to=params.date_to,
        )
        header_chunk = next(rows)
    except ValueError:
        raise HTTPException(status_code=400, detail=_INVALID_DATE)

    def _stream():
        yield header_chunk
        yield from rows

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=torrents.csv"},
    )
