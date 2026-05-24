"""Schemas for /api/diag/* diagnostics endpoints."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, model_validator


class JavdbSessionStatus(BaseModel):
    """Status of the current JavDB session cookie."""

    cookie_present: bool
    cookie_value_preview: Optional[str]  # first 8 chars + "...", or None
    last_refresh_time: Optional[str]     # ISO 8601 UTC from system_state KV
    estimated_expiry: Optional[str]      # best-effort; None when not derivable
    is_likely_valid: bool                # heuristic: last refresh < 24h ago


class JavdbSessionRefreshRequest(BaseModel):
    """Request body for POST /api/diag/javdb-session/refresh."""

    method: Literal["headless", "cookie_paste"] = "headless"
    cookie_value: Optional[str] = None

    @model_validator(mode="after")
    def _require_cookie_for_paste(self) -> "JavdbSessionRefreshRequest":
        if self.method == "cookie_paste" and not self.cookie_value:
            raise ValueError("cookie_value is required when method='cookie_paste'")
        return self


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
