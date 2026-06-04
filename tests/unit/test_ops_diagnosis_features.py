from __future__ import annotations

import json

from javdb.ops.diagnosis.features import build_incident_features
from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord


def _context() -> tuple[IncidentBundle, OpsIncidentRecord]:
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        run_id="100",
        run_attempt=2,
        session_id=None,
        workflow_name="DailyIngestion",
        workflow_result="failure",
    )
    result = DiagnosisResult(
        incident_type="failed_ingestion",
        confidence="medium",
        confirmed_findings=[
            "Workflow result is failure.",
            "qBittorrent upload side effect may already have happened.",
        ],
        likely_causes=["The ingestion workflow did not complete successfully."],
        unknowns=["Session id is missing; rollback safety cannot be proven."],
        recommended_next_actions=["Inspect the failed workflow job logs before retrying the run."],
        unsafe_actions=["Do not run forced rollback without locating the owning session."],
        evidence_refs=[],
        model_version="deterministic-fallback-v1",
        detector_version="adr026-detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(bundle, result).with_persistence_status("d1_written")
    return bundle, record


def test_build_incident_features_extracts_categorical_and_text_tokens():
    bundle, record = _context()
    features = build_incident_features(record, bundle=bundle)

    assert features.incident_id.startswith("opsinc_")
    assert features.incident_type == "failed_ingestion"
    assert features.workflow_name == "DailyIngestion"
    assert features.feature_version == "ops-incident-features-v1"
    assert json.loads(features.categorical_features_json)["confidence"] == "medium"
    assert "workflow" in json.loads(features.text_tokens_json)
    assert "rollback" in json.loads(features.unsafe_action_tokens_json)
