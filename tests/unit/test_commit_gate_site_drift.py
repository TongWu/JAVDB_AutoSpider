# tests/unit/test_commit_gate_site_drift.py
import sqlite3

from javdb.ops.sentinel import service
from javdb.ops.sentinel.models import FieldFill
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


class _Inc:
    def __init__(self): self.records = []
    def upsert(self, r): self.records.append(r)


def _repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ParseRunFieldFillRepo(c)


def test_gate_blocks_on_critical_drift():
    repo = _repo()
    repo.upsert_fills("S1", [FieldFill("index", "href", 0.05, 100)])  # critical
    v = service.evaluate_session("S1", fill_repo=repo, incident_repo=_Inc())
    assert v.critical is True  # caller must NOT commit


def test_gate_allows_clean_run():
    repo = _repo()
    repo.upsert_fills("S1", [FieldFill("index", "href", 1.0, 100)])
    v = service.evaluate_session("S1", fill_repo=repo, incident_repo=_Inc())
    assert v.critical is False  # caller commits, then mark_committed
