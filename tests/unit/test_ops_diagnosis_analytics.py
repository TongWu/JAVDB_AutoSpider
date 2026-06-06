from __future__ import annotations

from dataclasses import replace

from javdb.ops.diagnosis.analytics import summarize_incidents
from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord


def _record(incident_type: str, status: str, confidence: str) -> OpsIncidentRecord:
    result = DiagnosisResult(
        incident_type=incident_type,
        confidence=confidence,
        confirmed_findings=[],
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=[],
        unsafe_actions=[],
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(trigger_source="manual_cli"),
        result,
    )
    return replace(record, status=status)


def test_summarize_incidents_counts_type_status_and_confidence():
    summary = summarize_incidents([
        _record("failed_ingestion", "open", "low"),
        _record("failed_ingestion", "resolved", "medium"),
        _record("d1_drift", "open", "medium"),
    ])

    assert summary["total"] == 3
    assert summary["by_type"] == {"failed_ingestion": 2, "d1_drift": 1}
    assert summary["by_status"] == {"open": 2, "resolved": 1}
    assert summary["by_confidence"] == {"low": 1, "medium": 2}


def test_summarize_incidents_counts_open_high_confidence():
    summary = summarize_incidents([
        _record("failed_ingestion", "open", "high"),   # open AND high -> counts
        _record("d1_drift", "resolved", "high"),        # high but not open
        _record("d1_drift", "open", "low"),             # open but not high
    ])

    assert summary["open_high_confidence"] == 1
