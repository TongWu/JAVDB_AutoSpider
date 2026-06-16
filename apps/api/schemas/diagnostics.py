"""Schemas for /api/diag/* diagnostics endpoints."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


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


class SimilarIncidentSchema(BaseModel):
    incident_id: str
    score: float
    matched_reasons: list[str]


class OpsIncidentSimilarityResponse(BaseModel):
    incident_id: str
    items: list[SimilarIncidentSchema]


class OpsIncidentAnalyticsResponse(BaseModel):
    total: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_confidence: dict[str, int]
    open_high_confidence: int


class ParseFieldHealthItem(BaseModel):
    """Latest committed parse health for one contract field (ADR-035 Phase 3)."""

    page_type: str
    field: str
    severity: str
    fill_rate: float
    sample_count: int
    observed_at: Optional[str] = None
    baseline: Optional[float] = None
    threshold: Optional[float] = None
    status: str


class ParseFieldHealthResponse(BaseModel):
    """List response for per-field parse health."""

    items: list[ParseFieldHealthItem]


class OpsRemediationProposalSchema(BaseModel):
    proposal_id: str
    incident_id: str
    action_type: str
    status: str
    safety_level: str
    title: str
    rationale: str
    command_preview: Optional[str] = None
    runbook_ref: Optional[str] = None
    evidence_refs: list[EvidenceRefSchema]
    required_checks: list[str]
    blocked_reasons: list[str]
    proposed_by: str
    decided_by: Optional[str] = None
    decision_note: Optional[str] = None
    created_at: str
    updated_at: str
    decided_at: Optional[str] = None


class OpsRemediationProposalListResponse(BaseModel):
    items: list[OpsRemediationProposalSchema]


class OpsRemediationDecisionRequest(BaseModel):
    status: Literal["approved", "rejected"]
    decision_note: Optional[str] = None


class OpsAlertPolicySchema(BaseModel):
    policy_id: str
    incident_type: str
    min_confidence: Literal["low", "medium", "high"]
    enabled: bool
    channels: list[str]
    updated_by: Optional[str] = None
    created_at: str
    updated_at: str


class OpsAlertPolicyListResponse(BaseModel):
    items: list[OpsAlertPolicySchema]


class OpsAlertPolicyUpsertRequest(BaseModel):
    min_confidence: Literal["low", "medium", "high"] = "medium"
    enabled: bool = True
    channels: list[str] = Field(default_factory=list)


class OpsAlertEventSchema(BaseModel):
    alert_id: str
    incident_id: str
    policy_id: Optional[str] = None
    status: Literal["fired", "suppressed", "skipped"]
    reason: Optional[str] = None
    fired_at: str


class OpsAlertEventListResponse(BaseModel):
    items: list[OpsAlertEventSchema]


__all__ = [
    "EvidenceRefSchema",
    "JavdbSessionRefreshRequest",
    "JavdbSessionRefreshResponse",
    "JavdbSessionStatus",
    "OpsAlertEventListResponse",
    "OpsAlertEventSchema",
    "OpsAlertPolicyListResponse",
    "OpsAlertPolicySchema",
    "OpsAlertPolicyUpsertRequest",
    "OpsIncidentAnalyticsResponse",
    "OpsIncidentListResponse",
    "OpsIncidentSchema",
    "OpsIncidentSimilarityResponse",
    "OpsRemediationDecisionRequest",
    "OpsRemediationProposalListResponse",
    "OpsRemediationProposalSchema",
    "ParseFieldHealthItem",
    "ParseFieldHealthResponse",
    "SimilarIncidentSchema",
]
