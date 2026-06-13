from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(_isolate_sqlite):
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return client


@pytest.fixture
def anon_client(_isolate_sqlite):
    from apps.api.services.runtime import app

    return TestClient(app)


def test_ops_incidents_requires_auth(anon_client: TestClient):
    response = anon_client.get("/api/diag/ops-incidents")
    assert response.status_code in {401, 403}


def test_ops_incident_schema_from_record(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import (
        DiagnosisResult,
        IncidentBundle,
        OpsIncidentRecord,
    )

    result = DiagnosisResult(
        incident_type="failed_ingestion",
        confidence="low",
        confirmed_findings=["workflow failed"],
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=["inspect logs"],
        unsafe_actions=[],
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(trigger_source="manual_cli", run_id="123"),
        result,
    ).with_persistence_status("d1_written")

    monkeypatch.setattr(
        diagnostics,
        "_list_ops_incident_records",
        lambda **_kwargs: [record],
    )

    response = admin_client.get("/api/diag/ops-incidents")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["incident_type"] == "failed_ingestion"
    assert payload["items"][0]["confirmed_findings"] == ["workflow failed"]


def test_ops_incident_list_rejects_non_positive_limit(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics

    called = False

    def fake_list(**_kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(diagnostics, "_list_ops_incident_records", fake_list)

    response = admin_client.get("/api/diag/ops-incidents?limit=0")

    assert response.status_code == 400
    assert response.json()["detail"] == "limit must be a positive integer"
    assert called is False


def test_ops_incident_detail_returns_404(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "_get_ops_incident_record",
        lambda _incident_id: None,
    )

    response = admin_client.get("/api/diag/ops-incidents/missing")
    assert response.status_code == 404


def test_ops_incident_schema_tolerates_malformed_json_fields():
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord

    result = DiagnosisResult(
        incident_type="failed_ingestion",
        confidence="low",
        confirmed_findings=["workflow failed"],
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=[],
        unsafe_actions=[],
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    record = OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(trigger_source="manual_cli", run_id="123"),
        result,
    )
    malformed = OpsIncidentRecord(
        **{
            **record.__dict__,
            "confirmed_findings_json": "{not-json",
            "likely_causes_json": "{}",
            "evidence_refs_json": '[{"kind":"runbook"}, "bad"]',
        }
    )

    schema = diagnostics._ops_record_to_schema(malformed)

    assert schema.confirmed_findings == []
    assert schema.likely_causes == []
    assert schema.evidence_refs == []


def test_ops_incidents_accepts_type_and_confidence_filters(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics

    captured = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(diagnostics, "_list_ops_incident_records", fake_list)

    response = admin_client.get("/api/diag/ops-incidents?incident_type=d1_drift&confidence=high")

    assert response.status_code == 200
    assert captured["incident_type"] == "d1_drift"
    assert captured["confidence"] == "high"


def test_ops_incident_analytics_returns_summary(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics

    monkeypatch.setattr(diagnostics, "_list_ops_incident_records", lambda **_kwargs: [])

    response = admin_client.get("/api/diag/ops-incidents/analytics")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["by_type"] == {}


def test_get_similar_ops_incidents_returns_ranked_items(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import SimilarIncident

    fake_items = [
        SimilarIncident(
            incident_id="opsinc_abc",
            score=0.9,
            matched_reasons=["same_type", "same_confidence"],
        ),
        SimilarIncident(
            incident_id="opsinc_def",
            score=0.6,
            matched_reasons=["same_type"],
        ),
    ]
    monkeypatch.setattr(
        diagnostics,
        "_similar_ops_incident_records",
        lambda _incident_id, **_kwargs: fake_items,
    )

    response = admin_client.get("/api/diag/ops-incidents/opsinc_target/similar")

    assert response.status_code == 200
    payload = response.json()
    assert payload["incident_id"] == "opsinc_target"
    assert len(payload["items"]) == 2
    assert payload["items"][0]["incident_id"] == "opsinc_abc"
    assert payload["items"][0]["score"] == pytest.approx(0.9)
    assert payload["items"][0]["matched_reasons"] == ["same_type", "same_confidence"]
    assert payload["items"][1]["incident_id"] == "opsinc_def"
    assert payload["items"][1]["matched_reasons"] == ["same_type"]


def test_get_similar_ops_incidents_rejects_non_positive_limit(admin_client: TestClient):
    response = admin_client.get("/api/diag/ops-incidents/opsinc_test/similar?limit=0")
    assert response.status_code == 400


def test_get_similar_ops_incidents_returns_404_when_no_features(
    monkeypatch, admin_client: TestClient
):
    from apps.api.routers import diagnostics

    monkeypatch.setattr(
        diagnostics,
        "_similar_ops_incident_records",
        lambda _incident_id, **_kwargs: None,
    )

    response = admin_client.get("/api/diag/ops-incidents/opsinc_missing/similar")

    assert response.status_code == 404


def test_ops_remediation_proposals_returns_items(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsRemediationProposal

    proposal = OpsRemediationProposal.create(
        incident_id="opsinc_test",
        action_type="open_runbook",
        safety_level="safe_to_prepare",
        title="Open runbook",
        rationale="Review troubleshooting runbook.",
    )
    monkeypatch.setattr(diagnostics, "_list_remediation_proposals", lambda _incident_id: [proposal])

    response = admin_client.get("/api/diag/ops-incidents/opsinc_test/remediation-proposals")

    assert response.status_code == 200
    assert response.json()["items"][0]["action_type"] == "open_runbook"


def test_ops_remediation_decision_records_only_decision(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsRemediationProposal

    proposal = OpsRemediationProposal.create(
        incident_id="opsinc_test",
        action_type="open_runbook",
        safety_level="safe_to_prepare",
        title="Open runbook",
        rationale="Review troubleshooting runbook.",
    )
    decided = OpsRemediationProposal(
        **{**proposal.__dict__, "status": "approved", "decided_by": "admin", "decision_note": "Reviewed."}
    )
    monkeypatch.setattr(diagnostics, "_record_remediation_decision", lambda *_args, **_kwargs: decided)

    response = admin_client.post(
        f"/api/diag/remediation-proposals/{proposal.proposal_id}/decision",
        json={"status": "approved", "decision_note": "Reviewed."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["decision_note"] == "Reviewed."


def test_ops_remediation_decision_rejects_approving_blocked(monkeypatch, admin_client: TestClient):
    """Approving a safety-blocked proposal returns 409 — the repo guard raises
    ValueError and the endpoint translates it instead of recording the decision."""
    from apps.api.routers import diagnostics

    def _raise(*_args, **_kwargs):
        raise ValueError("Cannot approve a proposal blocked by the safety policy: opsprop_x")

    monkeypatch.setattr(diagnostics, "_record_remediation_decision", _raise)

    response = admin_client.post(
        "/api/diag/remediation-proposals/opsprop_x/decision",
        json={"status": "approved"},
    )

    assert response.status_code == 409
    assert "blocked" in response.json()["detail"].lower()
