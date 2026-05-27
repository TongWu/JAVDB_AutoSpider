from __future__ import annotations

from javdb.ops.diagnosis.detectors import detect_incident
from javdb.ops.diagnosis.models import IncidentBundle


def test_detector_classifies_failed_ingestion_with_unknown_rollback_when_session_missing():
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        session_id=None,
    )

    result = detect_incident(bundle)

    assert result.incident_type == "failed_ingestion"
    assert "Workflow result is failure." in result.confirmed_findings
    assert "Session id is missing; rollback safety cannot be proven." in result.unknowns
    assert "Do not run forced rollback without locating the owning session." in result.unsafe_actions


def test_detector_classifies_d1_drift_from_known_verdict():
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id="sid",
        drift_verdict="SAFE_TO_APPLY",
        session_status="committed",
    )

    result = detect_incident(bundle)

    assert result.incident_type == "d1_drift"
    assert result.confidence == "medium"
    assert any("drift_diagnose" in action for action in result.recommended_next_actions)
    assert "Do not rollback the committed session." in result.unsafe_actions


def test_detector_flags_dead_lettered_recovery_outbox():
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        recovery_outbox_summary={"dead_lettered_count": 2, "pending_count": 0},
    )

    result = detect_incident(bundle)

    assert result.incident_type == "d1_recovery_outbox"
    assert "Recovery outbox has 2 dead-lettered event(s)." in result.confirmed_findings
    assert (
        "Do not mark recovery work resolved before inspecting the dead-lettered ordering key."
        in result.unsafe_actions
    )


def test_detector_tolerates_non_numeric_recovery_counts():
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        recovery_outbox_summary={"dead_lettered_count": "N/A", "pending_count": ""},
    )

    result = detect_incident(bundle)

    assert result.incident_type == "unknown"
    assert "No known detector matched the incident bundle." in result.unknowns
