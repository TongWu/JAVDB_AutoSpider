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
