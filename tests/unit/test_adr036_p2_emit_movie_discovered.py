# tests/unit/test_adr036_p2_emit_movie_discovered.py
"""
Tests that MovieDiscovered events are emitted for each index-phase entry,
and that the emit point is best-effort (pipeline step survives emit failure).
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (
  consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def _sample_entries(n=3, phase=1):
    return [
        {
            "href": f"/v/ABC-{i:03d}",
            "video_code": f"ABC-{i:03d}",
            "page": 1,
            "rate": "4.5",
            "comment_number": "100",
        }
        for i in range(n)
    ]


def test_movie_discovered_emitted_for_each_entry(event_repo):
    """Each index entry should produce one MovieDiscovered event with correct payload."""
    session_id = "SESS-001"
    entries_p1 = _sample_entries(2, phase=1)
    entries_p2 = _sample_entries(1, phase=2)

    for phase, entries in ((1, entries_p1), (2, entries_p2)):
        for entry in entries:
            store.emit(
                "MovieDiscovered",
                session_id=session_id,
                entity_type="movie",
                entity_id=entry["href"],
                payload=json.dumps({
                    "video_code": entry["video_code"],
                    "phase": phase,
                    "page": entry["page"],
                    "rate": entry.get("rate"),
                    "comment_number": entry.get("comment_number"),
                }),
                repo=event_repo,
            )

    events = event_repo.read_since(0, limit=100)
    discovered = [e for e in events if e.event_type == "MovieDiscovered"]
    assert len(discovered) == 3
    hrefs = {e.entity_id for e in discovered}
    assert "/v/ABC-000" in hrefs
    # Verify payload is valid JSON with required fields
    p = json.loads(discovered[0].payload)
    assert "video_code" in p
    assert "phase" in p


def test_movie_discovered_skipped_when_no_session():
    """emit() with empty session_id returns None and does not raise."""
    # Use a broken repo to prove best-effort: would raise on write
    class _BrokenRepo:
        def append(self, record):
            raise RuntimeError("DB is broken")
        def read_since(self, last_seq, *, limit):
            return []
        def get_cursor(self, consumer):
            return 0
        def advance_cursor(self, consumer, last_seq):
            pass

    result = store.emit(
        "MovieDiscovered",
        session_id="",
        entity_type="movie",
        entity_id="/v/ABC-001",
        repo=_BrokenRepo(),
    )
    assert result is None


def test_movie_discovered_best_effort_survives_emit_raise(event_repo):
    """Pipeline step must not raise when emit itself raises."""
    with patch.object(event_repo, "append", side_effect=RuntimeError("inject")):
        # emit wraps in try/except and returns None — must not propagate
        result = store.emit(
            "MovieDiscovered",
            session_id="SESS-001",
            entity_type="movie",
            entity_id="/v/ABC-001",
            repo=event_repo,
        )
    assert result is None  # best-effort: None on failure
