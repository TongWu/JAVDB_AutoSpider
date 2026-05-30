# tests/unit/test_pipeline_event_repo.py
import sqlite3

import pytest

from javdb.pipeline.events.models import PipelineEventRecord
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo, RunEventSummaryRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE RunEventSummary (session_id TEXT NOT NULL, event_type TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (session_id, event_type));
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return c


def _rec(t, sid="S1"):
    return PipelineEventRecord(event_type=t, session_id=sid, entity_type="session",
                               entity_id=sid, created_at="t")


def test_append_returns_monotonic_seq(conn):
    repo = PipelineEventRepo(conn)
    s1 = repo.append(_rec("RunStarted"))
    s2 = repo.append(_rec("SessionCommitted"))
    assert s2 > s1


def test_read_since_returns_ordered_tail(conn):
    repo = PipelineEventRepo(conn)
    repo.append(_rec("RunStarted"))
    cut = repo.append(_rec("SessionCommitted"))
    repo.append(_rec("SessionFailed"))
    rows = repo.read_since(cut, limit=10)
    assert [r.event_type for r in rows] == ["SessionFailed"]


def test_cursor_get_default_zero_and_advance(conn):
    repo = PipelineEventRepo(conn)
    assert repo.get_cursor("c1") == 0
    repo.advance_cursor("c1", 7)
    assert repo.get_cursor("c1") == 7


def test_summary_bump_reset_get(conn):
    s = RunEventSummaryRepo(conn)
    s.bump("S1", "RunStarted")
    s.bump("S1", "RunStarted")
    assert s.get("S1")["RunStarted"] == 2
    s.reset()
    assert s.get("S1") == {}
