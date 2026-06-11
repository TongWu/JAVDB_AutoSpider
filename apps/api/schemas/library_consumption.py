# apps/api/schemas/library_consumption.py
"""Pydantic schemas for Library consumption endpoints (ADR-034 FE-3)."""

from __future__ import annotations

from pydantic import BaseModel


class ConsumptionSummary(BaseModel):
    """KPI counts for GET /api/library/consumption/summary."""

    total_signals: int
    watched_count: int
    unwatched_count: int
    avg_rating: float | None  # NULL when no ratings — do NOT coalesce to 0
    unique_titles: int
    instance_count: int
    unresolved_count: int


class ConsumptionRecentItem(BaseModel):
    """One ConsumptionSignal row for GET /api/library/consumption/recent."""

    video_code: str
    source_type: str
    instance: str
    library_id: str
    library_name: str | None = None
    watched: bool | None = None  # INTEGER 0/1/NULL → bool/None
    progress_pct: int | None = None
    play_count: int | None = None
    rating: float | None = None
    watched_at: str | None = None
    resolved_confidence: str | None = None
    observed_at: str | None = None


class ConsumptionTrendPoint(BaseModel):
    """One day in GET /api/library/consumption/trend."""

    date: str
    watched: int
    total_signals: int


class UnresolvedItem(BaseModel):
    """One UnresolvedMediaItem row for GET /api/library/consumption/unresolved."""

    instance: str
    source_type: str | None = None
    library_id: str
    library_name: str | None = None
    item_id: str
    raw_title: str | None = None
    file_path: str | None = None
    observed_at: str | None = None
