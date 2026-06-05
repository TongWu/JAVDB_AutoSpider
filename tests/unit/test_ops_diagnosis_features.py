from __future__ import annotations

import dataclasses
import json

from javdb.ops.diagnosis.features import _evidence_kinds, _json_load_list, _tokens, build_incident_features
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


# ---------------------------------------------------------------------------
# _json_load_list — non-list fallback
# ---------------------------------------------------------------------------

def test_json_load_list_non_list_returns_empty():
    assert _json_load_list('{"not": "a list"}') == []


def test_json_load_list_empty_string_returns_empty():
    assert _json_load_list("") == []


def test_json_load_list_null_returns_empty():
    assert _json_load_list("null") == []


def test_build_incident_features_non_list_confirmed_findings_does_not_raise():
    """A stored confirmed_findings_json that is a JSON object (not a list) produces no tokens."""
    _bundle, base_record = _context()
    record = dataclasses.replace(base_record, confirmed_findings_json='{"a": 1}')
    features = build_incident_features(record)
    # No exception; text_tokens_json is valid JSON (may contain tokens from other fields).
    assert isinstance(json.loads(features.text_tokens_json), list)


# ---------------------------------------------------------------------------
# _tokens — dedup, stopword removal, 80-cap
# ---------------------------------------------------------------------------

def test_tokens_dedup():
    result = _tokens(["alpha alpha beta"])
    assert result == ["alpha", "beta"]


def test_tokens_stopword_removal():
    result = _tokens(["the alpha and beta"])
    assert result == ["alpha", "beta"]


def test_tokens_80_cap():
    big_input = " ".join(f"tok{i:03d}" for i in range(100))
    result = _tokens([big_input])
    assert len(result) == 80


# ---------------------------------------------------------------------------
# _evidence_kinds — dedup, non-dict filter, missing-kind filter
# ---------------------------------------------------------------------------

def test_evidence_kinds_dedup_and_filter():
    raw = '[{"kind":"runbook"},{"kind":"runbook"},"notadict",{"kind":"cli"},{"nokind":1}]'
    result = _evidence_kinds(raw)
    assert result == ["runbook", "cli"]
