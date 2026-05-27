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


class EvidenceRefSchema(BaseModel):
    """Evidence reference attached to an operations diagnosis."""

    kind: str
    ref: str
    label: Optional[str] = None


class OpsIncidentSchema(BaseModel):
    """Persisted ADR-026 operations incident summary."""

    incident_id: str
    trigger_source: str
    run_id: Optional[str] = None
    run_attempt: Optional[int] = None
    session_id: Optional[str] = None
    incident_type: str
    status: str
    persistence_status: str
    model_version: str
    detector_version: str
    confidence: str
    confirmed_findings: list[str]
    likely_causes: list[str]
    unknowns: list[str]
    recommended_next_actions: list[str]
    unsafe_actions: list[str]
    evidence_refs: list[EvidenceRefSchema]
    created_at: str
    updated_at: str
    resolved_at: Optional[str] = None


class OpsIncidentListResponse(BaseModel):
    """List response for operations incidents."""

    items: list[OpsIncidentSchema]


__all__ = [
    "EvidenceRefSchema",
    "JavdbSessionRefreshRequest",
    "JavdbSessionRefreshResponse",
    "JavdbSessionStatus",
    "OpsIncidentListResponse",
    "OpsIncidentSchema",
]
