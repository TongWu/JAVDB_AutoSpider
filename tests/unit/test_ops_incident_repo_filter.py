# tests/unit/test_ops_incident_repo_filter.py
import sqlite3

import pytest

from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo

_DDL = """
CREATE TABLE OpsIncidents (
  incident_id TEXT PRIMARY KEY, trigger_source TEXT, run_id TEXT, run_attempt INTEGER,
  session_id TEXT, incident_type TEXT, status TEXT, persistence_status TEXT,
  model_version TEXT, detector_version TEXT, bundle_schema_version TEXT, confidence TEXT,
  confirmed_findings_json TEXT, likely_causes_json TEXT, unknowns_json TEXT,
  recommended_next_actions_json TEXT, unsafe_actions_json TEXT, evidence_refs_json TEXT,
  created_at TEXT, updated_at TEXT, resolved_at TEXT
);
"""


def _insert(conn, incident_id, incident_type, created_at):
    conn.execute(
        "INSERT INTO OpsIncidents (incident_id, trigger_source, run_id, run_attempt, "
        "session_id, incident_type, status, persistence_status, model_version, "
        "detector_version, bundle_schema_version, confidence, confirmed_findings_json, "
        "likely_causes_json, unknowns_json, recommended_next_actions_json, "
        "unsafe_actions_json, evidence_refs_json, created_at, updated_at, resolved_at) "
        "VALUES (?, 'sentinel', NULL, NULL, NULL, ?, 'open', 'd1_written', 'n/a', "
        "'sentinel-v1', 'n/a', 'high', '[]', '[]', '[]', '[]', '[]', '[]', ?, ?, NULL)",
        [incident_id, incident_type, created_at, created_at],
    )


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    _insert(c, "i1", "site_drift", "2026-06-01T00:00:00Z")
    _insert(c, "i2", "failed_ingestion", "2026-06-02T00:00:00Z")
    _insert(c, "i3", "site_drift", "2026-06-03T00:00:00Z")
    return OpsIncidentRepo(c)


def test_filter_by_incident_type(repo):
    rows = repo.list(incident_type="site_drift")
    assert {r.incident_id for r in rows} == {"i1", "i3"}


def test_no_filter_returns_all(repo):
    assert len(repo.list()) == 3


def test_filter_combines_with_other_clauses(repo):
    rows = repo.list(incident_type="site_drift", status="open")
    assert {r.incident_id for r in rows} == {"i1", "i3"}
