# tests/unit/test_adr036_p2_emit_torrent_selected.py
"""
Tests that TorrentSelected events are emitted for new magnet links,
with defensive hash derivation that never raises.
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

_VALID_MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
_BAD_MAGNET = "not-a-real-magnet"


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def _emit_torrent_selected(session_id, href, magnet, video_code, category, event_repo):
    """Inline the same defensive logic that runner.py will use."""
    try:
        from javdb.integrations.qb.client import extract_hash_from_magnet
        qb_hash = extract_hash_from_magnet(magnet)
    except Exception:
        qb_hash = None
    entity_id = qb_hash if qb_hash else href
    return store.emit(
        "TorrentSelected",
        session_id=session_id,
        entity_type="torrent",
        entity_id=entity_id,
        payload=json.dumps({
            "category": category,
            "href": href,
            "video_code": video_code,
        }),
        repo=event_repo,
    )


def test_torrent_selected_uses_hash_as_entity_id(event_repo):
    seq = _emit_torrent_selected(
        "SESS-003", "/v/ABC-001", _VALID_MAGNET, "ABC-001", "subtitle", event_repo
    )
    assert seq is not None
    events = event_repo.read_since(0, limit=10)
    assert len(events) == 1
    e = events[0]
    assert e.event_type == "TorrentSelected"
    assert e.entity_id == "a" * 40
    p = json.loads(e.payload)
    assert p["video_code"] == "ABC-001"
    assert p["category"] == "subtitle"


def test_torrent_selected_falls_back_to_href_on_bad_magnet(event_repo):
    seq = _emit_torrent_selected(
        "SESS-003", "/v/ABC-001", _BAD_MAGNET, "ABC-001", "subtitle", event_repo
    )
    events = event_repo.read_since(0, limit=10)
    assert events[0].entity_id == "/v/ABC-001"


def test_torrent_selected_no_session_returns_none():
    result = store.emit(
        "TorrentSelected",
        session_id="",
        entity_type="torrent",
        entity_id="somehash",
    )
    assert result is None


def test_torrent_selected_best_effort_survives_emit_raise(event_repo):
    with patch.object(event_repo, "append", side_effect=RuntimeError("inject")):
        result = store.emit(
            "TorrentSelected",
            session_id="SESS-003",
            entity_type="torrent",
            entity_id="somehash",
            repo=event_repo,
        )
    assert result is None
