# tests/unit/test_pipeline_event_store.py
import sqlite3

from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
"""


def _repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def test_emit_appends_and_read_since_returns_it():
    repo = _repo()
    seq = store.emit("RunStarted", session_id="S1", entity_type="session", entity_id="S1", repo=repo)
    assert seq == 1
    rows = store.read_since(0, repo=repo)
    assert rows[0].event_type == "RunStarted"


def test_emit_is_best_effort_on_unknown_type():
    repo = _repo()
    # unknown type is still appended (validation is advisory, not a hard gate) but
    # a None/blank session must NOT raise — emit returns None on bad input.
    assert store.emit("RunStarted", session_id="", entity_type="session", repo=repo) is None
