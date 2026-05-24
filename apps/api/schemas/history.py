"""Pydantic schemas for history search and export endpoints (Phase 2, Task 1)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Query param models ────────────────────────────────────────────────────────


class MovieSearchParams(BaseModel):
    """Query parameters for GET /api/history/movies."""

    q: Optional[str] = Field(
        default=None,
        description="LIKE search on VideoCode, ActorName, SupportingActors",
    )
    actor: Optional[str] = Field(
        default=None,
        description="Exact match on ActorName",
    )
    perfect_match: Optional[bool] = Field(
        default=None,
        description="Filter by PerfectMatchIndicator",
    )
    hi_res: Optional[bool] = Field(
        default=None,
        description="Filter by HiResIndicator",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Filter by SessionId",
    )
    date_from: Optional[str] = Field(
        default=None,
        description=(
            "Lower bound on DateTimeCreated (inclusive). "
            "Accepts ISO 8601 date (``2026-01-01``) or datetime "
            "(``2026-01-01T10:00:00Z``). "
            "A date-only value is treated as ``00:00:00`` of that day."
        ),
    )
    date_to: Optional[str] = Field(
        default=None,
        description=(
            "Upper bound on DateTimeCreated (inclusive). "
            "Accepts ISO 8601 date (``2026-01-01``) or datetime "
            "(``2026-01-01T23:59:59Z``). "
            "A date-only value is treated as ``23:59:59`` of that day "
            "(inclusive of the whole day)."
        ),
    )
    cursor: Optional[str] = Field(
        default=None,
        description="Base64-encoded Id for keyset pagination",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Page size (1–200)",
    )


class TorrentSearchParams(BaseModel):
    """Query parameters for GET /api/history/torrents."""

    q: Optional[str] = Field(
        default=None,
        description="LIKE search on parent movie VideoCode",
    )
    resolution_type: Optional[int] = Field(
        default=None,
        description="0=unknown, 1=SD, 2=HD, 3=FHD, 4=4K",
    )
    has_subtitle: Optional[bool] = Field(
        default=None,
        description="Filter by SubtitleIndicator",
    )
    uncensored: Optional[bool] = Field(
        default=None,
        description="Filter by CensorIndicator == 0",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Filter by SessionId",
    )
    date_from: Optional[str] = Field(
        default=None,
        description=(
            "Lower bound on DateTimeCreated (inclusive). "
            "Accepts ISO 8601 date (``2026-01-01``) or datetime "
            "(``2026-01-01T10:00:00Z``). "
            "A date-only value is treated as ``00:00:00`` of that day."
        ),
    )
    date_to: Optional[str] = Field(
        default=None,
        description=(
            "Upper bound on DateTimeCreated (inclusive). "
            "Accepts ISO 8601 date (``2026-01-01``) or datetime "
            "(``2026-01-01T23:59:59Z``). "
            "A date-only value is treated as ``23:59:59`` of that day "
            "(inclusive of the whole day)."
        ),
    )
    cursor: Optional[str] = Field(
        default=None,
        description="Base64-encoded Id for keyset pagination",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Page size (1–200)",
    )


# ── Response item models ──────────────────────────────────────────────────────


class MovieSearchItem(BaseModel):
    """A single MovieHistory row in the search response."""

    id: int
    video_code: str
    href: str
    actor_name: Optional[str] = None
    actor_gender: Optional[str] = None
    supporting_actors: Optional[str] = None
    perfect_match: bool
    hi_res: bool
    datetime_created: Optional[str] = None
    datetime_updated: Optional[str] = None
    session_id: Optional[str] = None
    torrent_count: int


class TorrentSearchItem(BaseModel):
    """A single TorrentHistory row (with joined movie data) in the search response."""

    id: int
    movie_video_code: Optional[str] = None
    movie_href: Optional[str] = None
    magnet_uri: Optional[str] = None
    size: Optional[str] = None
    subtitle_indicator: int
    censor_indicator: int
    resolution_type: int
    file_count: int
    datetime_created: Optional[str] = None
    session_id: Optional[str] = None


# ── Response envelope models ──────────────────────────────────────────────────


class MovieSearchResponse(BaseModel):
    """Paginated response for GET /api/history/movies."""

    items: List[MovieSearchItem]
    next_cursor: Optional[str] = None
    total_estimate: int


class TorrentSearchResponse(BaseModel):
    """Paginated response for GET /api/history/torrents."""

    items: List[TorrentSearchItem]
    next_cursor: Optional[str] = None
    total_estimate: int
