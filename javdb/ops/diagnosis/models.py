"""Typed contracts for ADR-026 operations diagnosis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal


Confidence = Literal["low", "medium", "high"]
IncidentStatus = Literal["open", "acknowledged", "resolved", "dismissed"]


def utc_now_iso() -> str:
    """Return an ISO 8601 UTC timestamp with a trailing Z."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_incident_id(
    *,
    trigger_source: str,
    run_id: str | None,
    run_attempt: int | None,
    session_id: str | None,
    incident_type: str,
) -> str:
    raw = "|".join([
        trigger_source or "",
        run_id or "",
        str(run_attempt or ""),
        session_id or "",
        incident_type or "",
    ])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"opsinc_{digest}"


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    ref: str
    label: str | None = None


@dataclass(frozen=True)
class IncidentBundle:
    trigger_source: str
    run_id: str | None = None
    run_attempt: int | None = None
    session_id: str | None = None
    workflow_name: str | None = None
    workflow_result: str | None = None
    bundle_schema_version: str = "bundle-v1"
    session_status: str | None = None
    drift_verdict: str | None = None
    recovery_outbox_summary: dict[str, Any] = field(default_factory=dict)
    rollback_safety: str | None = None
    qb_side_effects: dict[str, Any] = field(default_factory=dict)
    log_snippets: list[str] = field(default_factory=list)
    email_summary: str | None = None
    runbook_refs: list[EvidenceRef] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosisResult:
    incident_type: str
    confidence: Confidence
    confirmed_findings: list[str]
    likely_causes: list[str]
    unknowns: list[str]
    recommended_next_actions: list[str]
    unsafe_actions: list[str]
    evidence_refs: list[EvidenceRef]
    model_version: str
    detector_version: str


@dataclass(frozen=True)
class OpsIncidentRecord:
    incident_id: str
    trigger_source: str
    run_id: str | None
    run_attempt: int | None
    session_id: str | None
    incident_type: str
    status: IncidentStatus
    persistence_status: str
    model_version: str
    detector_version: str
    bundle_schema_version: str
    confidence: Confidence
    confirmed_findings_json: str
    likely_causes_json: str
    unknowns_json: str
    recommended_next_actions_json: str
    unsafe_actions_json: str
    evidence_refs_json: str
    created_at: str
    updated_at: str
    resolved_at: str | None = None

    @classmethod
    def from_bundle_and_result(
        cls,
        bundle: IncidentBundle,
        result: DiagnosisResult,
    ) -> "OpsIncidentRecord":
        now = utc_now_iso()
        incident_id = build_incident_id(
            trigger_source=bundle.trigger_source,
            run_id=bundle.run_id,
            run_attempt=bundle.run_attempt,
            session_id=bundle.session_id,
            incident_type=result.incident_type,
        )
        return cls(
            incident_id=incident_id,
            trigger_source=bundle.trigger_source,
            run_id=bundle.run_id,
            run_attempt=bundle.run_attempt,
            session_id=bundle.session_id,
            incident_type=result.incident_type,
            status="open",
            persistence_status="not_written",
            model_version=result.model_version,
            detector_version=result.detector_version,
            bundle_schema_version=bundle.bundle_schema_version,
            confidence=result.confidence,
            confirmed_findings_json=_json_dumps(result.confirmed_findings),
            likely_causes_json=_json_dumps(result.likely_causes),
            unknowns_json=_json_dumps(result.unknowns),
            recommended_next_actions_json=_json_dumps(result.recommended_next_actions),
            unsafe_actions_json=_json_dumps(result.unsafe_actions),
            evidence_refs_json=_json_dumps([asdict(ref) for ref in result.evidence_refs]),
            created_at=now,
            updated_at=now,
        )

    def with_persistence_status(self, status: str) -> "OpsIncidentRecord":
        data = asdict(self)
        data["persistence_status"] = status
        data["updated_at"] = utc_now_iso()
        return OpsIncidentRecord(**data)


@dataclass(frozen=True)
class OpsIncidentFeatures:
    incident_id: str
    incident_type: str
    status: IncidentStatus
    confidence: Confidence
    workflow_name: str | None
    run_id: str | None
    run_attempt: int | None
    session_id: str | None
    feature_version: str
    categorical_features_json: str
    text_tokens_json: str
    unsafe_action_tokens_json: str
    evidence_kinds_json: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SimilarIncident:
    incident_id: str
    score: float
    matched_reasons: list[str]


ActionType = Literal[
    "open_runbook",
    "prepare_rollback_workflow",
    "prepare_rerun_workflow",
    "prepare_drift_apply_command",
    "inspect_qb_side_effects",
    "inspect_recovery_outbox",
]
ProposalStatus = Literal["proposed", "approved", "rejected", "expired"]
SafetyLevel = Literal["safe_to_prepare", "requires_review", "blocked"]


def build_proposal_id(incident_id: str, action_type: str) -> str:
    digest = hashlib.sha256(f"{incident_id}|{action_type}".encode("utf-8")).hexdigest()[:24]
    return f"opsprop_{digest}"


@dataclass(frozen=True)
class OpsRemediationProposal:
    proposal_id: str
    incident_id: str
    action_type: ActionType
    status: ProposalStatus
    safety_level: SafetyLevel
    title: str
    rationale: str
    command_preview: str | None
    runbook_ref: str | None
    evidence_refs_json: str
    required_checks_json: str
    blocked_reasons_json: str
    proposed_by: str
    decided_by: str | None
    decision_note: str | None
    created_at: str
    updated_at: str
    decided_at: str | None = None

    @classmethod
    def create(
        cls,
        *,
        incident_id: str,
        action_type: ActionType,
        safety_level: SafetyLevel,
        title: str,
        rationale: str,
        command_preview: str | None = None,
        runbook_ref: str | None = None,
        evidence_refs: list[EvidenceRef] | None = None,
        required_checks: list[str] | None = None,
        blocked_reasons: list[str] | None = None,
        proposed_by: str = "adr026-policy-v1",
    ) -> "OpsRemediationProposal":
        now = utc_now_iso()
        return cls(
            proposal_id=build_proposal_id(incident_id, action_type),
            incident_id=incident_id,
            action_type=action_type,
            status="proposed",
            safety_level=safety_level,
            title=title,
            rationale=rationale,
            command_preview=command_preview,
            runbook_ref=runbook_ref,
            evidence_refs_json=_json_dumps([asdict(ref) for ref in evidence_refs or []]),
            required_checks_json=_json_dumps(required_checks or []),
            blocked_reasons_json=_json_dumps(blocked_reasons or []),
            proposed_by=proposed_by,
            decided_by=None,
            decision_note=None,
            created_at=now,
            updated_at=now,
        )
