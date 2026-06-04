from __future__ import annotations

from javdb.ops.diagnosis.features import build_incident_features
from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord
from javdb.ops.diagnosis.similarity import rank_similar_incidents


def _record(incident_id_suffix: str, incident_type: str, findings: list[str]) -> OpsIncidentRecord:
    result = DiagnosisResult(
        incident_type=incident_type,
        confidence="medium",
        confirmed_findings=findings,
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=[],
        unsafe_actions=[],
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(trigger_source="manual_cli", run_id=incident_id_suffix, run_attempt=1),
        result,
    )
    return record


def test_rank_similar_incidents_prefers_same_type_and_shared_tokens():
    target = build_incident_features(_record("target", "failed_ingestion", ["workflow failure rollback unknown"]))
    close = build_incident_features(_record("close", "failed_ingestion", ["workflow failed rollback unsafe"]))
    far = build_incident_features(_record("far", "d1_recovery_outbox", ["dead letter recovery outbox"]))

    ranked = rank_similar_incidents(target, [far, close])

    assert [item.incident_id for item in ranked] == [close.incident_id, far.incident_id]
    assert ranked[0].score > ranked[1].score
    assert "incident_type" in ranked[0].matched_reasons
    assert any(reason.startswith("text_tokens:") for reason in ranked[0].matched_reasons)


def test_rank_similar_incidents_handles_empty_and_self_exclusion():
    target = build_incident_features(_record("target", "failed_ingestion", ["workflow failure rollback"]))

    # No candidates -> empty result.
    assert rank_similar_incidents(target, []) == []

    # Target present in candidates -> excluded from its own ranking.
    other = build_incident_features(_record("other", "failed_ingestion", ["workflow failure rollback"]))
    ranked = rank_similar_incidents(target, [target, other])
    ids = [item.incident_id for item in ranked]
    assert target.incident_id not in ids
    assert other.incident_id in ids
