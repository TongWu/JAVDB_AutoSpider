"""Rule-based detectors for ADR-026 operations diagnosis."""

from __future__ import annotations

from javdb.ops.diagnosis.models import DiagnosisResult, EvidenceRef, IncidentBundle


DETECTOR_VERSION = "adr026-detectors-v1"


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def detect_incident(bundle: IncidentBundle) -> DiagnosisResult:
    findings: list[str] = []
    causes: list[str] = []
    unknowns: list[str] = []
    actions: list[str] = []
    unsafe: list[str] = []
    evidence: list[EvidenceRef] = list(bundle.runbook_refs)
    confidence = "low"
    incident_type = "unknown"

    if bundle.workflow_result in {"failure", "cancelled"}:
        incident_type = "failed_ingestion"
        findings.append(f"Workflow result is {bundle.workflow_result}.")
        actions.append("Inspect the failed workflow job logs before retrying the run.")
        causes.append("The ingestion workflow did not complete successfully.")

    if bundle.drift_verdict:
        incident_type = "d1_drift"
        findings.append(f"Drift diagnosis verdict is {bundle.drift_verdict}.")
        evidence.append(
            EvidenceRef(
                kind="cli",
                ref="python3 -m apps.cli.db.drift_diagnose --since 24 --json",
            )
        )
        if bundle.drift_verdict == "SAFE_TO_APPLY":
            confidence = "medium"
            actions.append("Run drift_diagnose apply only after reviewing the suggested session id.")
            unsafe.append("Do not rollback the committed session.")
        elif bundle.drift_verdict != "CLEAN":
            actions.append("Escalate D1 drift investigation before any cleanup.")
            unsafe.append("Do not apply D1 deletes for non-SAFE_TO_APPLY drift verdicts.")

    dead_lettered = _safe_int(bundle.recovery_outbox_summary.get("dead_lettered_count", 0))
    pending = _safe_int(bundle.recovery_outbox_summary.get("pending_count", 0))
    if dead_lettered:
        incident_type = "d1_recovery_outbox"
        findings.append(f"Recovery outbox has {dead_lettered} dead-lettered event(s).")
        actions.append("Inspect the D1 recovery outbox and repair or abandon the affected ordering key.")
        unsafe.append("Do not mark recovery work resolved before inspecting the dead-lettered ordering key.")
    elif pending:
        incident_type = "d1_recovery_outbox"
        findings.append(f"Recovery outbox has {pending} pending event(s).")
        actions.append("Drain the recovery outbox before committing or retrying the affected session.")

    if bundle.session_id is None:
        unknowns.append("Session id is missing; rollback safety cannot be proven.")
        unsafe.append("Do not run forced rollback without locating the owning session.")
    elif bundle.session_status:
        findings.append(f"Session status is {bundle.session_status}.")

    if bundle.qb_side_effects.get("uploaded") is True:
        findings.append("qBittorrent upload side effect may already have happened.")
        unsafe.append("Do not assume DB rollback removes qBittorrent-side downloads.")

    if bundle.log_snippets:
        evidence.append(EvidenceRef(kind="log", ref="workflow log snippets", label="Collected log snippets"))

    if not findings:
        unknowns.append("No known detector matched the incident bundle.")
        actions.append("Review workflow logs and runbooks manually.")

    return DiagnosisResult(
        incident_type=incident_type,
        confidence=confidence,
        confirmed_findings=findings,
        likely_causes=causes,
        unknowns=unknowns,
        recommended_next_actions=actions,
        unsafe_actions=unsafe,
        evidence_refs=evidence,
        model_version="deterministic-fallback-v1",
        detector_version=DETECTOR_VERSION,
    )
