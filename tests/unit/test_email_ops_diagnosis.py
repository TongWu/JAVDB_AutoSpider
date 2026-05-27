from __future__ import annotations

import json

from javdb.integrations.notify import email


def test_build_ops_diagnosis_advisory_from_json_file(tmp_path):
    path = tmp_path / "ops_diagnosis.json"
    path.write_text(json.dumps({
        "incident_id": "opsinc_test",
        "incident_type": "failed_ingestion",
        "confidence": "low",
        "persistence_status": "d1_written",
        "confirmed_findings": ["Workflow result is failure."],
        "recommended_next_actions": ["Inspect failed job logs."],
    }), encoding="utf-8")

    advisory = email._build_ops_diagnosis_advisory(str(path))

    assert "Operations Diagnosis" in advisory
    assert "opsinc_test" in advisory
    assert "failed_ingestion" in advisory
    assert "Inspect failed job logs." in advisory
    assert "Full diagnosis: /api/diag/ops-incidents/opsinc_test" in advisory


def test_build_ops_diagnosis_advisory_for_jsonl_fallback_avoids_dead_api_link(tmp_path):
    path = tmp_path / "ops_diagnosis.json"
    path.write_text(json.dumps({
        "incident_id": "opsinc_test",
        "incident_type": "failed_ingestion",
        "confidence": "low",
        "persistence_status": "d1_failed_jsonl_written",
        "confirmed_findings": ["Workflow result is failure."],
        "recommended_next_actions": ["Inspect failed job logs."],
    }), encoding="utf-8")

    advisory = email._build_ops_diagnosis_advisory(str(path))

    assert "/api/diag/ops-incidents/opsinc_test" not in advisory
    assert "workflow artifact JSONL fallback" in advisory


def test_build_ops_diagnosis_advisory_missing_file_is_empty(tmp_path):
    assert email._build_ops_diagnosis_advisory(str(tmp_path / "missing.json")) == ""


def test_build_ops_diagnosis_advisory_non_object_json_is_empty(tmp_path):
    path = tmp_path / "ops_diagnosis.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    assert email._build_ops_diagnosis_advisory(str(path)) == ""
