"""Pydantic schemas for Logs endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class LogSearchItem(BaseModel):
    job_id: str
    line_number: int
    text: str
    kind: str
    created_at: str


class LogSearchResponse(BaseModel):
    results: list[LogSearchItem]
    total_matched: int
    truncated: bool


__all__ = [
    "LogSearchItem",
    "LogSearchResponse",
]
