from __future__ import annotations

import json

from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord
from javdb.ops.diagnosis.remediation import propose_remediation


def _record(
    incident_type: str,
    unsafe_actions: list[str],
    session_id: str | None = "20260527T120000.000000Z-0001-0001",
) -> OpsIncidentRecord:
    result = DiagnosisResult(
        incident_type=incident_type,
        confidence="medium",
        confirmed_findings=["Workflow result is failure."],
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=["Inspect logs."],
        unsafe_actions=unsafe_actions,
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    return OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(
            trigger_source="workflow_failure",
            run_id="100",
            run_attempt=1,
            session_id=session_id,
            workflow_name="DailyIngestion",
            workflow_result="failure",
        ),
        result,
    ).with_persistence_status("d1_written")


def test_failed_ingestion_with_session_gets_rerun_and_rollback_preparation_proposals():
    proposals = propose_remediation(_record("failed_ingestion", []))

    action_types = {proposal.action_type for proposal in proposals}
    assert "prepare_rerun_workflow" in action_types
    assert "prepare_rollback_workflow" in action_types
    assert all(proposal.status == "proposed" for proposal in proposals)


def test_unsafe_rollback_blocks_rollback_proposal():
    proposals = propose_remediation(_record("failed_ingestion", ["Do not run forced rollback without locating the owning session."], session_id=None))

    rollback = next(p for p in proposals if p.action_type == "prepare_rollback_workflow")
    assert rollback.safety_level == "blocked"
    assert "Session id is missing." in json.loads(rollback.blocked_reasons_json)


def test_unsafe_rollback_with_session_reports_unsafe_reason_not_missing_session():
    # Session id IS present, but the diagnosis flagged rollback as unsafe. The
    # block reason must name the real cause, not the (false) "missing session".
    proposals = propose_remediation(
        _record("failed_ingestion", ["Do not run forced rollback until the owning session is confirmed."])
    )

    rollback = next(p for p in proposals if p.action_type == "prepare_rollback_workflow")
    assert rollback.safety_level == "blocked"
    blocked_reasons = json.loads(rollback.blocked_reasons_json)
    assert "Diagnosis flagged rollback as unsafe." in blocked_reasons
    assert "Session id is missing." not in blocked_reasons


def test_d1_drift_gets_command_preview_not_apply_execution():
    proposals = propose_remediation(_record("d1_drift", []))

    drift = next(p for p in proposals if p.action_type == "prepare_drift_apply_command")
    assert drift.command_preview is not None
    assert "drift_diagnose" in drift.command_preview
    assert "--apply" not in drift.command_preview
