# tests/unit/test_pipeline_event_consumer.py
import sqlite3

from javdb.pipeline.events import store
from javdb.pipeline.events.consumer import RunEventSummaryConsumer
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


def _wire():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c), RunEventSummaryRepo(c)


def test_consumer_projects_then_advances():
    ev, sm = _wire()
    store.emit("RunStarted", session_id="S1", entity_type="session", repo=ev)
    store.emit("SessionCommitted", session_id="S1", entity_type="session", repo=ev)
    n = RunEventSummaryConsumer(sm).run_once(event_repo=ev)
    assert n == 2
    assert sm.get("S1") == {"RunStarted": 1, "SessionCommitted": 1}
    # cursor advanced -> a second run sees nothing
    assert RunEventSummaryConsumer(sm).run_once(event_repo=ev) == 0


def test_consumer_is_idempotent_no_double_count():
    ev, sm = _wire()
    store.emit("RunStarted", session_id="S1", entity_type="session", repo=ev)
    c = RunEventSummaryConsumer(sm)
    c.run_once(event_repo=ev)
    c.run_once(event_repo=ev)  # nothing new
    assert sm.get("S1")["RunStarted"] == 1


def test_replay_rebuilds_projection():
    ev, sm = _wire()
    store.emit("RunStarted", session_id="S1", entity_type="session", repo=ev)
    store.emit("SessionFailed", session_id="S1", entity_type="session", repo=ev)
    c = RunEventSummaryConsumer(sm)
    c.run_once(event_repo=ev)
    # replay: reset cursor + projection, re-run -> identical result
    ev.advance_cursor(c.name, 0)
    sm.reset()
    c.run_once(event_repo=ev)
    assert sm.get("S1") == {"RunStarted": 1, "SessionFailed": 1}
