from __future__ import annotations

import json
import sqlite3

from javdb.ops.diagnosis.models import (
    DiagnosisResult,
    EvidenceRef,
    IncidentBundle,
    OpsIncidentRecord,
)
from javdb.ops.diagnosis.persistence import persist_incident
from javdb.ops.diagnosis.jsonl_store import read_incident_jsonl
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo


DDL = """
CREATE TABLE OpsIncidents (
  incident_id TEXT PRIMARY KEY,
  trigger_source TEXT NOT NULL,
  run_id TEXT,
  run_attempt INTEGER,
  session_id TEXT,
  incident_type TEXT NOT NULL,
  status TEXT NOT NULL,
  persistence_status TEXT NOT NULL,
  model_version TEXT NOT NULL,
  detector_version TEXT NOT NULL,
  bundle_schema_version TEXT NOT NULL,
  confidence TEXT NOT NULL,
  confirmed_findings_json TEXT NOT NULL,
  likely_causes_json TEXT NOT NULL,
  unknowns_json TEXT NOT NULL,
  recommended_next_actions_json TEXT NOT NULL,
  unsafe_actions_json TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
)
"""


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(DDL)
    return conn


def _record():
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        run_id="42",
        run_attempt=1,
        session_id="sid",
    )
    result = DiagnosisResult(
        incident_type="failed_ingestion",
        confidence="low",
        confirmed_findings=["workflow failed"],
        likely_causes=[],
        unknowns=["log artifact missing"],
        recommended_next_actions=["inspect logs"],
        unsafe_actions=["do not force rollback"],
        evidence_refs=[
            EvidenceRef(kind="runbook", ref="docs/handbook/en/ops/troubleshooting.md")
        ],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    return OpsIncidentRecord.from_bundle_and_result(bundle, result)


def test_repo_upserts_and_reads_incident():
    conn = _conn()
    repo = OpsIncidentRepo(conn)
    record = _record()

    repo.upsert(record.with_persistence_status("d1_written"))
    fetched = repo.get(record.incident_id)

    assert fetched is not None
    assert fetched.incident_id == record.incident_id
    assert fetched.persistence_status == "d1_written"
    assert json.loads(fetched.confirmed_findings_json) == ["workflow failed"]


def test_repo_lists_newest_first():
    conn = _conn()
    repo = OpsIncidentRepo(conn)
    first = _record().with_persistence_status("d1_written")
    second = OpsIncidentRecord(
        **{
            **first.__dict__,
            "incident_id": "opsinc_second",
            "created_at": "2099-01-01T00:00:00Z",
        }
    )

    repo.upsert(first)
    repo.upsert(second)
    items = repo.list(limit=10)

    assert [item.incident_id for item in items] == ["opsinc_second", first.incident_id]


def test_repo_logs_row_factory_failure(caplog):
    class BrokenRowFactoryConnection:
        def __setattr__(self, name, value):
            if name == "row_factory":
                raise RuntimeError("closed connection")
            object.__setattr__(self, name, value)

    with caplog.at_level("DEBUG"):
        OpsIncidentRepo(BrokenRowFactoryConnection())

    assert "Failed to set row_factory" in caplog.text


def test_persist_incident_falls_back_to_jsonl_when_d1_fails(tmp_path):
    class FailingRepo:
        def upsert(self, record):
            raise RuntimeError("d1 unavailable")

    record = _record()
    path = tmp_path / "ops_incidents.jsonl"

    persisted = persist_incident(record, repo=FailingRepo(), jsonl_path=path)

    assert persisted.persistence_status == "d1_failed_jsonl_written"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["incident_id"] == record.incident_id


def test_persist_incident_uses_reports_logical_db(monkeypatch):
    from javdb.ops.diagnosis import persistence

    conn = _conn()
    seen = []

    def fake_get_db(logical_name):
        seen.append(logical_name)
        return conn

    monkeypatch.setattr(persistence, "get_db", fake_get_db)

    persisted = persistence.persist_incident(_record())

    assert seen == ["reports"]
    assert persisted.persistence_status == "d1_written"
    assert OpsIncidentRepo(conn).get(persisted.incident_id) is not None


def test_read_incident_jsonl_skips_malformed_lines(tmp_path):
    path = tmp_path / "ops_incidents.jsonl"
    path.write_text(
        '{"incident_id":"opsinc_valid"}\n'
        'not json\n'
        '{"incident_id":"opsinc_second"}\n',
        encoding="utf-8",
    )

    rows = read_incident_jsonl(path)

    assert [row["incident_id"] for row in rows] == ["opsinc_valid", "opsinc_second"]


def test_read_incident_jsonl_skips_non_object_lines(tmp_path):
    path = tmp_path / "ops_incidents.jsonl"
    path.write_text(
        '{"incident_id":"opsinc_valid"}\n'
        '["not", "an", "object"]\n',
        encoding="utf-8",
    )

    rows = read_incident_jsonl(path)

    assert [row["incident_id"] for row in rows] == ["opsinc_valid"]
