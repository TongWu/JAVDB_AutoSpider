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


def test_persist_incident_calls_get_db_with_reports_path(monkeypatch):
    """persist_incident must pass the reports DB *path* to get_db, not the
    logical name "reports". get_db takes a filesystem path — the bare string
    "reports" has no path/logical-name mapping and raises inside get_db.
    """
    from javdb.ops.diagnosis import persistence

    conn = _conn()
    seen = []

    def fake_get_db(db_path):
        seen.append(db_path)
        return conn

    monkeypatch.setattr(persistence, "get_db", fake_get_db)

    persisted = persistence.persist_incident(_record())

    assert seen == [persistence.REPORTS_DB_PATH]
    assert persisted.persistence_status == "d1_written"
    assert OpsIncidentRepo(conn).get(persisted.incident_id) is not None


def test_persist_incident_writes_to_reports_db_without_jsonl_fallback(tmp_path, monkeypatch):
    """Regression: under sqlite, persist_incident reaches the reports DB through
    the real get_db(REPORTS_DB_PATH) path and does NOT hit the JSONL fallback.

    Before the fix the code called get_db("reports") (a logical name, not a
    path). get_db opened the "reports/" directory as a SQLite file and raised;
    the broad except swallowed it, so every incident silently fell back to
    JSONL and never landed in the reports DB. This test exercises the real
    get_db (no mock) so that regression would fail it.
    """
    from javdb.ops.diagnosis import persistence

    db_path = tmp_path / "reports.db"
    setup = sqlite3.connect(db_path)
    setup.execute(DDL)
    setup.commit()
    setup.close()

    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("_STORAGE_BACKEND_INIT_OVERRIDE", raising=False)
    # Point persist_incident at the temp reports DB; do NOT mock get_db.
    monkeypatch.setattr(persistence, "REPORTS_DB_PATH", str(db_path))

    jsonl_path = tmp_path / "ops_incidents.jsonl"
    record = _record()

    persisted = persist_incident(record, jsonl_path=jsonl_path)

    # Happy path: the reports write succeeded, no fallback marker.
    assert persisted.persistence_status == "d1_written"
    # The JSONL fallback was NOT triggered.
    assert not jsonl_path.exists()
    # The row really landed in the reports DB.
    verify = sqlite3.connect(db_path)
    verify.row_factory = sqlite3.Row
    try:
        fetched = OpsIncidentRepo(verify).get(record.incident_id)
    finally:
        verify.close()
    assert fetched is not None
    assert fetched.persistence_status == "d1_written"


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
