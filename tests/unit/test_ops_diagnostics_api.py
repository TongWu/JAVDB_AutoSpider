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


def test_alert_policies_returns_items_with_channels(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsAlertPolicy

    policy = OpsAlertPolicy.create(
        incident_type="failed_ingestion",
        min_confidence="high",
        enabled=True,
        channels=["email", "ops"],
        updated_by="ted",
    )
    monkeypatch.setattr(diagnostics, "_list_alert_policies", lambda: [policy])

    response = admin_client.get("/api/diag/alert-policies")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["incident_type"] == "failed_ingestion"
    assert payload["items"][0]["min_confidence"] == "high"
    assert payload["items"][0]["channels"] == ["email", "ops"]
    assert "channels_json" not in payload["items"][0]


def test_alert_policy_schema_tolerates_malformed_channels_json():
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsAlertPolicy

    policy = OpsAlertPolicy(
        **{
            **OpsAlertPolicy.create(incident_type="failed_ingestion").__dict__,
            "channels_json": "{not-json",
        }
    )

    schema = diagnostics._policy_to_schema(policy)

    assert schema.channels == []


def test_alert_policy_upsert_is_admin_only_and_returns_policy(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsAlertPolicy

    captured = {}

    def fake_upsert(incident_type, *, min_confidence, enabled, channels, updated_by):
        captured.update(
            {
                "incident_type": incident_type,
                "min_confidence": min_confidence,
                "enabled": enabled,
                "channels": channels,
                "updated_by": updated_by,
            }
        )
        return OpsAlertPolicy.create(
            incident_type=incident_type,
            min_confidence=min_confidence,
            enabled=enabled,
            channels=channels,
            updated_by=updated_by,
        )

    monkeypatch.setattr(diagnostics, "_upsert_alert_policy", fake_upsert)

    response = admin_client.put(
        "/api/diag/alert-policies/failed_ingestion",
        json={"min_confidence": "low", "enabled": False, "channels": ["email"]},
    )

    assert response.status_code == 200
    assert response.json()["min_confidence"] == "low"
    assert response.json()["enabled"] is False
    assert response.json()["channels"] == ["email"]
    assert captured == {
        "incident_type": "failed_ingestion",
        "min_confidence": "low",
        "enabled": False,
        "channels": ["email"],
        "updated_by": "admin",
    }


def test_alert_policy_upsert_rejects_invalid_min_confidence(admin_client: TestClient):
    response = admin_client.put(
        "/api/diag/alert-policies/failed_ingestion",
        json={"min_confidence": "urgent", "channels": []},
    )

    assert response.status_code == 422


@pytest.mark.usefixtures("_isolate_sqlite")
def test_readonly_user_cannot_upsert_alert_policy():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})

    response = client.put(
        "/api/diag/alert-policies/failed_ingestion",
        json={"min_confidence": "medium", "channels": []},
    )

    assert response.status_code == 403


def test_alert_events_returns_items(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsAlertEvent

    event = OpsAlertEvent(
        alert_id="opsalert_abc",
        incident_id="opsinc_test",
        policy_id="opspolicy_abc",
        status="fired",
        reason="confidence high met threshold medium",
        fired_at="2026-06-15T00:00:00Z",
    )
    monkeypatch.setattr(diagnostics, "_list_alert_events", lambda _incident_id: [event])

    response = admin_client.get("/api/diag/ops-incidents/opsinc_test/alert-events")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == [
        {
            "alert_id": "opsalert_abc",
            "incident_id": "opsinc_test",
            "policy_id": "opspolicy_abc",
            "status": "fired",
            "reason": "confidence high met threshold medium",
            "fired_at": "2026-06-15T00:00:00Z",
        }
    ]
