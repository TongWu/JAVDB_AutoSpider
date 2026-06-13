from __future__ import annotations

import json

from javdb.ops.diagnosis.models import DiagnosisResult, EvidenceRef, IncidentBundle
from javdb.ops.diagnosis.service import diagnose_incident


class CapturingRepo:
    def __init__(self):
        self.records = []
        self.features = []

    def upsert(self, record):
        self.records.append(record)

    def upsert_features(self, features):
        self.features.append(features)


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


def test_service_persists_feature_row_when_incident_is_written():
    repo = CapturingRepo()
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        session_id="20260527T120000.000000Z-0001-0001",
    )

    record = diagnose_incident(bundle, repo=repo)

    assert record.persistence_status == "d1_written"
    assert repo.features[0].incident_id == record.incident_id
    assert repo.features[0].workflow_name == "DailyIngestion"


def test_service_does_not_persist_feature_row_on_jsonl_fallback(tmp_path):
    class FailingUpsertRepo:
        def __init__(self):
            self.features = []

        def upsert(self, record):
            raise RuntimeError("simulated D1 failure")

        def upsert_features(self, features):
            self.features.append(features)

    repo = FailingUpsertRepo()
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        session_id=None,
    )

    record = diagnose_incident(bundle, repo=repo, jsonl_path=tmp_path / "fallback.jsonl")

    assert record.persistence_status == "d1_failed_jsonl_written"
    assert repo.features == []


def test_service_can_generate_remediation_proposals():
    class ProposalRepo:
        def __init__(self):
            self.proposals = []

        def upsert(self, proposal):
            self.proposals.append(proposal)

    incident_repo = CapturingRepo()
    proposal_repo = ProposalRepo()
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id="20260527T120000.000000Z-0001-0001",
    )

    record = diagnose_incident(
        bundle,
        repo=incident_repo,
        remediation_repo=proposal_repo,
        generate_remediation=True,
    )

    assert record.incident_type == "failed_ingestion"
    assert proposal_repo.proposals
    assert {proposal.action_type for proposal in proposal_repo.proposals}


def test_service_swallows_remediation_persistence_failure():
    """Remediation proposals are non-critical: a failure to persist them must not
    break the main diagnosis flow (graceful degradation)."""

    class ExplodingProposalRepo:
        def upsert(self, proposal):
            raise RuntimeError("boom")

    incident_repo = CapturingRepo()
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id="20260527T120000.000000Z-0001-0001",
    )

    # Must not raise despite the proposal repo blowing up mid-persist.
    record = diagnose_incident(
        bundle,
        repo=incident_repo,
        remediation_repo=ExplodingProposalRepo(),
        generate_remediation=True,
    )

    assert record.incident_type == "failed_ingestion"
    assert record.persistence_status == "d1_written"
