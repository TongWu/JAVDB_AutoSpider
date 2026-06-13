"""Deterministic, non-executing remediation proposals for ADR-026 Phase 3."""

from __future__ import annotations

import json

from javdb.ops.diagnosis.models import EvidenceRef, OpsIncidentRecord, OpsRemediationProposal

POLICY_VERSION = "adr026-remediation-policy-v1"


def _list(raw: str) -> list[str]:
    value = json.loads(raw or "[]")
    return [str(item) for item in value] if isinstance(value, list) else []


def _has_unsafe_rollback(record: OpsIncidentRecord) -> bool:
    text = " ".join(_list(record.unsafe_actions_json)).lower()
    return "rollback" in text and ("do not" in text or "unsafe" in text or "cannot" in text)


def _incident_ref(record: OpsIncidentRecord) -> EvidenceRef:
    return EvidenceRef(kind="incident", ref=record.incident_id)


def propose_remediation(record: OpsIncidentRecord) -> list[OpsRemediationProposal]:
    proposals: list[OpsRemediationProposal] = [
        OpsRemediationProposal.create(
            incident_id=record.incident_id,
            action_type="open_runbook",
            safety_level="safe_to_prepare",
            title="Open operations troubleshooting runbook",
            rationale="Every incident should start with the read-only runbook context.",
            runbook_ref="docs/handbook/en/ops/troubleshooting.md",
            evidence_refs=[_incident_ref(record)],
            required_checks=["Review confirmed findings and unknowns before taking action."],
            blocked_reasons=[],
            proposed_by=POLICY_VERSION,
        )
    ]

    if record.incident_type == "failed_ingestion":
        proposals.append(
            OpsRemediationProposal.create(
                incident_id=record.incident_id,
                action_type="prepare_rerun_workflow",
                safety_level="requires_review",
                title="Prepare ingestion workflow rerun",
                rationale="The incident is a failed ingestion. A rerun may be appropriate after checking external side effects.",
                command_preview=(
                    f"gh run rerun {record.run_id}"
                    if record.run_id
                    else "gh workflow run DailyIngestion.yml"
                ),
                runbook_ref="docs/handbook/en/ops/troubleshooting.md",
                evidence_refs=[_incident_ref(record)],
                required_checks=[
                    "Confirm the failure was not caused by an active D1 recovery outbox dead-letter.",
                    "Confirm qBittorrent side effects do not make a rerun unsafe.",
                ],
                blocked_reasons=[],
                proposed_by=POLICY_VERSION,
            )
        )

        rollback_blocked_reasons: list[str] = []
        if record.session_id is None:
            rollback_blocked_reasons.append("Session id is missing.")
        if _has_unsafe_rollback(record):
            rollback_blocked_reasons.append("Diagnosis flagged rollback as unsafe.")
        rollback_blocked = bool(rollback_blocked_reasons)
        proposals.append(
            OpsRemediationProposal.create(
                incident_id=record.incident_id,
                action_type="prepare_rollback_workflow",
                safety_level="blocked" if rollback_blocked else "requires_review",
                title="Prepare rollback workflow",
                rationale="Rollback may be appropriate only when the session is known and the rollback safety matrix permits it.",
                command_preview=(
                    f"gh workflow run RollbackD1.yml -f session_id={record.session_id}"
                    if record.session_id else None
                ),
                runbook_ref="docs/handbook/en/ops/d1-rollback.md",
                evidence_refs=[_incident_ref(record)],
                required_checks=[
                    "Confirm session status and write mode.",
                    "Confirm rollback does not conflict with committed history.",
                ],
                blocked_reasons=rollback_blocked_reasons,
                proposed_by=POLICY_VERSION,
            )
        )

    if record.incident_type == "d1_drift":
        proposals.append(
            OpsRemediationProposal.create(
                incident_id=record.incident_id,
                action_type="prepare_drift_apply_command",
                safety_level="requires_review",
                title="Prepare drift diagnose review command",
                rationale="D1 drift must be reviewed with ADR-009 tooling before any apply step is considered.",
                command_preview="python3 -m apps.cli.db.drift_diagnose --since 24 --json",
                runbook_ref="docs/handbook/en/ops/d1-rollback.md",
                evidence_refs=[_incident_ref(record)],
                required_checks=[
                    "Run the command and confirm the verdict is SAFE_TO_APPLY before considering apply.",
                    "Do not append --apply until a human has reviewed the exact affected rows.",
                ],
                blocked_reasons=[],
                proposed_by=POLICY_VERSION,
            )
        )

    if record.incident_type == "d1_recovery_outbox":
        proposals.append(
            OpsRemediationProposal.create(
                incident_id=record.incident_id,
                action_type="inspect_recovery_outbox",
                safety_level="requires_review",
                title="Inspect D1 recovery outbox",
                rationale="Recovery outbox incidents require ordering-key inspection before any state is marked resolved.",
                command_preview="python3 -m apps.cli.db.d1_recovery inspect",
                runbook_ref="docs/handbook/en/ops/d1-rollback.md",
                evidence_refs=[_incident_ref(record)],
                required_checks=["Confirm whether dead-lettered work blocks the affected session ordering key."],
                blocked_reasons=[],
                proposed_by=POLICY_VERSION,
            )
        )

    return proposals
