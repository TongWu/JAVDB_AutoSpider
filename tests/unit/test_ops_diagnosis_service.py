from __future__ import annotations

import json

from javdb.ops.diagnosis.models import DiagnosisResult, EvidenceRef, IncidentBundle
from javdb.ops.diagnosis.service import diagnose_incident


class CapturingRepo:
    def __init__(self):
        self.records = []

    def upsert(self, record):
        self.records.append(record)


def test_service_runs_detector_and_persists_record():
    repo = CapturingRepo()
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id=None,
    )

    record = diagnose_incident(bundle, repo=repo)

    assert record.incident_type == "failed_ingestion"
    assert record.persistence_status == "d1_written"
    assert len(repo.records) == 1
    assert repo.records[0].incident_id == record.incident_id


def test_service_uses_ai_synthesizer_when_available():
    repo = CapturingRepo()

    def synthesize(bundle, detector_result):
        return DiagnosisResult(
            incident_type="failed_ingestion",
            confidence="high",
            confirmed_findings=detector_result.confirmed_findings + ["AI summary produced"],
            likely_causes=["Known failure pattern"],
            unknowns=[],
            recommended_next_actions=["Open diagnosis page"],
            unsafe_actions=["Do not force rollback"],
            evidence_refs=[EvidenceRef(kind="incident", ref="synthetic")],
            model_version="fake-ai-v1",
            detector_version=detector_result.detector_version,
        )

    record = diagnose_incident(
        IncidentBundle(trigger_source="manual_cli", workflow_result="failure", session_id="sid"),
        repo=repo,
        synthesizer=synthesize,
    )

    assert record.model_version == "fake-ai-v1"
    assert json.loads(record.confirmed_findings_json)[-1] == "AI summary produced"
