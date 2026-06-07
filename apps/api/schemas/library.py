"""Pydantic schemas for Library acquisition endpoints (ADR-034 FE-1)."""

from __future__ import annotations

from pydantic import BaseModel


class AcquisitionSummary(BaseModel):
    """Funnel/KPI counts for GET /api/library/acquisition/summary."""

    queued: int
    downloading: int
    completed: int
    stalled: int
    failed: int
    total: int


class AcquisitionRecentItem(BaseModel):
    """One AcquisitionOutcome row for GET /api/library/acquisition/recent."""

    qb_hash: str
    video_code: str | None = None
    href: str
    category: str | None = None
    state: str
    queued_at: str | None = None
    completed_at: str | None = None
    last_seen_at: str | None = None


class AcquisitionTrendPoint(BaseModel):
    """One day in GET /api/library/acquisition/trend (ADR-027 trend shape)."""

    date: str
    completed: int
    stalled: int
    failed: int
