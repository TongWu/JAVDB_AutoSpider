"""Pydantic schemas for watchlist (watch-intent) endpoints (ADR-054 WS1)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class WatchIntentUpsert(BaseModel):
    href: str = Field(min_length=1)
    status: Literal["want", "viewed"]
    notes: Optional[str] = None


class WatchIntentResponse(BaseModel):
    video_code: str
    href: str
    status: Literal["want", "viewed"]
    notes: Optional[str] = None
    status_at: Optional[str] = None
    updated_at: str


class WatchIntentListResponse(BaseModel):
    items: List[WatchIntentResponse]
    total: int


class WatchIntentDeleteResponse(BaseModel):
    deleted: bool
