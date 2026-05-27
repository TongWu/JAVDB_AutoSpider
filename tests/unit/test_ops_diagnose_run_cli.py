from __future__ import annotations

import json

from apps.cli.ops import diagnose_run


def test_cli_requires_at_least_one_run_or_session_identifier(capsys):
    code = diagnose_run.main(["--json"])

    assert code == 2
    assert "provide --run-id or --session-id" in capsys.readouterr().err


def test_cli_prints_json_record(monkeypatch, capsys):
    def fake_diagnose(bundle, **_kwargs):
        class Record:
            incident_id = "opsinc_test"
            incident_type = "failed_ingestion"
            confidence = "low"
            persistence_status = "d1_written"
            confirmed_findings_json = '["Workflow result is failure."]'
            likely_causes_json = "[]"
            unknowns_json = "[]"
            recommended_next_actions_json = "[]"
            unsafe_actions_json = "[]"
            evidence_refs_json = "[]"

        return Record()

    monkeypatch.setattr(diagnose_run, "diagnose_incident", fake_diagnose)

    code = diagnose_run.main([
        "--run-id", "123",
        "--attempt", "1",
        "--workflow-result", "failure",
        "--json",
    ])

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["incident_id"] == "opsinc_test"
    assert payload["incident_type"] == "failed_ingestion"


def test_cli_tolerates_malformed_record_json(monkeypatch, capsys):
    def fake_diagnose(bundle, **_kwargs):
        class Record:
            incident_id = "opsinc_test"
            incident_type = "failed_ingestion"
            confidence = "low"
            persistence_status = "d1_written"
            confirmed_findings_json = "{not-json"
            likely_causes_json = "{}"
            unknowns_json = "[]"
            recommended_next_actions_json = None
            unsafe_actions_json = "[]"
            evidence_refs_json = "[]"

        return Record()

    monkeypatch.setattr(diagnose_run, "diagnose_incident", fake_diagnose)

    code = diagnose_run.main(["--run-id", "123", "--json"])

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["confirmed_findings"] == []
    assert payload["likely_causes"] == []
    assert payload["recommended_next_actions"] == []


def test_cli_returns_distinct_code_for_unexpected_exception(monkeypatch, capsys):
    def fake_collect(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnose_run, "collect_incident_bundle", fake_collect)

    code = diagnose_run.main(["--run-id", "123", "--json"])

    assert code == 3
    assert "operations diagnosis failed unexpectedly" in capsys.readouterr().err
