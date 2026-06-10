# tests/unit/test_adr036_p2_emit_movie_selected.py
"""
Tests that MovieSelected events are emitted for each prepared detail candidate,
and that the emit is best-effort.
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


def _candidate(href, video_code, page=1):
    return {"href": href, "video_code": video_code, "page": page}


def test_movie_selected_emitted_per_candidate(event_repo):
    """One MovieSelected event per prepared candidate."""
    session_id = "SESS-002"
    candidates = [
        _candidate("/v/ABC-001", "ABC-001"),
        _candidate("/v/ABC-002", "ABC-002"),
    ]
    for c in candidates:
        store.emit(
            "MovieSelected",
            session_id=session_id,
            entity_type="movie",
            entity_id=c["href"],
            payload=json.dumps({
                "video_code": c["video_code"],
                "phase": 1,
                "page_num": c["page"],
            }),
            repo=event_repo,
        )

    events = event_repo.read_since(0, limit=100)
    selected = [e for e in events if e.event_type == "MovieSelected"]
    assert len(selected) == 2
    assert selected[0].entity_id == "/v/ABC-001"
    p = json.loads(selected[0].payload)
    assert p["video_code"] == "ABC-001"
    assert p["phase"] == 1


def test_movie_selected_no_session_returns_none():
    """emit() with no session returns None, no raise."""
    result = store.emit(
        "MovieSelected",
        session_id="",
        entity_type="movie",
        entity_id="/v/X",
    )
    assert result is None


def test_movie_selected_best_effort_survives_emit_raise(event_repo):
    """Pipeline step survives emit raising."""
    with patch.object(event_repo, "append", side_effect=RuntimeError("inject")):
        result = store.emit(
            "MovieSelected",
            session_id="SESS-002",
            entity_type="movie",
            entity_id="/v/X",
            repo=event_repo,
        )
    assert result is None
