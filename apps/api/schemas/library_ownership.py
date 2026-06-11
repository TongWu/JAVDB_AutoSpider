# apps/api/schemas/library_ownership.py
"""Pydantic schemas for Library ownership endpoints (ADR-034 FE-2)."""

from __future__ import annotations

from pydantic import BaseModel


class OwnershipSourceBreakdown(BaseModel):
    """Per-source counts for GET /api/library/ownership/summary."""

    source: str
    unique_titles: int
    present_rows: int
    total_bytes: int


class OwnershipSummary(BaseModel):
    """KPI + per-source breakdown for GET /api/library/ownership/summary."""

    total_owned_titles: int
    by_source: list[OwnershipSourceBreakdown]


class OwnershipRecentItem(BaseModel):
    """One OwnershipLedger row for GET /api/library/ownership/recent."""

    video_code: str
    source: str
    category: str
    path: str | None = None
    size: int | None = None
    present: int
    observed_at: str | None = None
