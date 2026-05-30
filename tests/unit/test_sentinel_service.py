# tests/unit/test_sentinel_service.py
import sqlite3

import pytest

from javdb.ops.sentinel import service
from javdb.ops.sentinel.models import FieldFill, SentinelOptions
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


class _FakeIncidentRepo:
    def __init__(self):
        self.records = []

    def upsert(self, record):
        self.records.append(record)


@pytest.fixture
def fill_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ParseRunFieldFillRepo(c)


def test_persist_run_writes_fills(fill_repo):
    n = service.persist_run([FieldFill("index", "href", 0.9, 100)], session_id="S1", repo=fill_repo)
    assert n == 1
    assert fill_repo.get_fills("S1")[0].field == "href"


def test_evaluate_session_critical_emits_incident_and_flags_critical(fill_repo):
    fill_repo.upsert_fills("S1", [FieldFill("index", "href", 0.10, 100)])  # critical
    inc = _FakeIncidentRepo()
    verdict = service.evaluate_session("S1", run_id="R", run_attempt=1,
                                       fill_repo=fill_repo, incident_repo=inc,
                                       options=SentinelOptions(min_sample=30))
    assert verdict.critical is True
    assert len(inc.records) == 1
    assert inc.records[0].incident_type == "site_drift"


def test_evaluate_session_clean_emits_nothing(fill_repo):
    fill_repo.upsert_fills("S1", [FieldFill("index", "href", 1.0, 100)])
    inc = _FakeIncidentRepo()
    verdict = service.evaluate_session("S1", fill_repo=fill_repo, incident_repo=inc)
    assert verdict.critical is False
    assert inc.records == []


def test_mark_committed_flips_flag(fill_repo):
    fill_repo.upsert_fills("S1", [FieldFill("index", "rate", 0.9, 100)])
    service.mark_committed("S1", repo=fill_repo)
    assert fill_repo.baseline("index", "rate", window=14) == 0.9
