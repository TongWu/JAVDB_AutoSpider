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


class MovieRatingUpsert(BaseModel):
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class MovieRatingResponse(BaseModel):
    href: str
    video_code: str
    rating: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    rated_at: Optional[str] = None
    updated_at: Optional[str] = None


class MovieRatingListResponse(BaseModel):
    items: List[MovieRatingResponse]
    total: int


class ContentPreferenceUpsert(BaseModel):
    content_name: str
    hearted: bool = False
    weight: float = Field(default=1.0, ge=0.0, le=10.0)


class ContentPreferenceResponse(BaseModel):
    content_type: str
    content_id: str
    content_name: str
    hearted: bool
    weight: float
    updated_at: Optional[str] = None


class ContentPreferenceListResponse(BaseModel):
    items: List[ContentPreferenceResponse]
