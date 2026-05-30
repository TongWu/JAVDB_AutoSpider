"""Pydantic schemas for preferences and metadata endpoints (ADR-022)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MovieMetadataResponse(BaseModel):
    href: str
    title: Optional[str] = None
    video_code: Optional[str] = None
    release_date: Optional[str] = None
    duration_minutes: Optional[int] = None
    rate: Optional[float] = None
    comment_count: Optional[int] = None
    review_count: Optional[int] = None
    want_count: Optional[int] = None
    watched_count: Optional[int] = None
    maker: Optional[Dict[str, str]] = None
    publisher: Optional[Dict[str, str]] = None
    series: Optional[Dict[str, str]] = None
    directors: Optional[List[Dict[str, str]]] = None
    categories: Optional[List[Dict[str, str]]] = None
    poster_url: Optional[str] = None
    fanart_urls: Optional[List[str]] = None
    trailer_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# Rating and preference schemas are added in IMP-ADR022-03.
