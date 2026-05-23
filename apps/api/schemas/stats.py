"""Pydantic schemas for Statistics endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class StatsSummary(BaseModel):
    total_runs: int
    success_rate: Optional[float] = None
    avg_duration_seconds: Optional[float] = None
    total_movies: int
    total_torrents: int
    total_pikpak: int
    total_dedup_freed_bytes: int
    proxy_bans_last_7d: int


class TrendDataPoint(BaseModel):
    date: str
    value: float


class TrendResponse(BaseModel):
    metric: str
    period: str
    data_points: list[TrendDataPoint]


__all__ = [
    "StatsSummary",
    "TrendDataPoint",
    "TrendResponse",
]
