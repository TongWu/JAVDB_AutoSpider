from __future__ import annotations

import json

from javdb.ops.diagnosis.models import (
    DiagnosisResult,
    EvidenceRef,
    IncidentBundle,
    OpsIncidentRecord,
    build_incident_id,
)


def test_incident_id_is_stable_for_run_attempt_session():
    first = build_incident_id(
        trigger_source="workflow_failure",
        run_id="123",
        run_attempt=2,
        session_id="20260527T120000.000000Z-abcd-0001",
        incident_type="failed_ingestion",
    )
    second = build_incident_id(
        trigger_source="workflow_failure",
        run_id="123",
        run_attempt=2,
        session_id="20260527T120000.000000Z-abcd-0001",
        incident_type="failed_ingestion",
    )

    assert first == second
    assert first.startswith("opsinc_")


def test_record_serializes_json_fields_as_compact_json_strings():
    result = DiagnosisResult(
        incident_type="d1_drift",
        confidence="medium",
        confirmed_findings=["D1 pending orphan exists"],
        likely_causes=["D1 write failed after SQLite success"],
        unknowns=["workflow logs unavailable"],
        recommended_next_actions=["Run drift diagnose CLI"],
        unsafe_actions=["Do not force rollback committed session"],
        evidence_refs=[EvidenceRef(kind="runbook", ref="docs/handbook/en/ops/d1-rollback.md")],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        run_id="123",
        run_attempt=1,
        session_id="sid",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        bundle_schema_version="bundle-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(bundle, result)

    assert record.incident_id.startswith("opsinc_")
    assert json.loads(record.confirmed_findings_json) == ["D1 pending orphan exists"]
    assert json.loads(record.evidence_refs_json) == [
        {"kind": "runbook", "ref": "docs/handbook/en/ops/d1-rollback.md", "label": None}
    ]
    assert record.status == "open"
    assert record.persistence_status == "not_written"
