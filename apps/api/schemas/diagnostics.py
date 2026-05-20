"""Schemas for /api/diag/* diagnostics endpoints."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class JavdbSessionStatus(BaseModel):
    """Status of the current JavDB session cookie."""

    cookie_present: bool
    cookie_value_preview: Optional[str]  # first 8 chars + "...", or None
    last_refresh_time: Optional[str]     # ISO 8601 UTC from system_state KV
    estimated_expiry: Optional[str]      # best-effort; None when not derivable
    is_likely_valid: bool                # heuristic: last refresh < 24h ago


class JavdbSessionRefreshRequest(BaseModel):
    """Request body for POST /api/diag/javdb-session/refresh."""

    method: str = "headless"            # "headless" | "cookie_paste"
    cookie_value: Optional[str] = None  # required when method="cookie_paste"


class JavdbSessionRefreshResponse(BaseModel):
    """Response for POST /api/diag/javdb-session/refresh."""

    success: bool
    method: str
    new_cookie_preview: Optional[str] = None
    error: Optional[str] = None


__all__ = [
    "JavdbSessionRefreshRequest",
    "JavdbSessionRefreshResponse",
    "JavdbSessionStatus",
]
