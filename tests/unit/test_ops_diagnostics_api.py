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
