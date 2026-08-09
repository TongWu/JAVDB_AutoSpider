# tests/unit/test_adr036_p2_shadow_consumer.py
"""Tests for AcquisitionOutcomeShadowConsumer projection and replay."""
import json
import sqlite3

import pytest

from javdb.pipeline.events import store
from javdb.pipeline.events.consumer import AcquisitionOutcomeShadowConsumer
from javdb.storage.repos.pipeline_event_repo import (
    AcquisitionOutcomeShadowRepo,
    PipelineEventRepo,
)

_EVENT_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (
  consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
"""
_SHADOW_DDL = """
CREATE TABLE AcquisitionOutcomeShadow (
  qb_hash      TEXT PRIMARY KEY NOT NULL,
  href         TEXT NOT NULL DEFAULT '',
  video_code   TEXT,
  category     TEXT,
  state        TEXT NOT NULL DEFAULT 'queued'
      CHECK (state IN ('queued','completed')),
  queued_at    TEXT,
  completed_at TEXT,
  session_id   TEXT,
  updated_at   TEXT NOT NULL
);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_EVENT_DDL + _SHADOW_DDL)
    return c


@pytest.fixture
def wire(conn):
    ev = PipelineEventRepo(conn)
    shadow = AcquisitionOutcomeShadowRepo(conn)
    consumer = AcquisitionOutcomeShadowConsumer(shadow)
    return ev, shadow, consumer


def test_torrent_queued_event_builds_shadow_row(wire):
    ev, shadow, consumer = wire
    store.emit(
        "TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="h" * 40,
        payload=json.dumps({
            "href": "/v/ABC-008",
            "video_code": "ABC-008",
            "category": "subtitle",
        }),
        repo=ev,
    )
    consumer.run_once(event_repo=ev)
    row = shadow.get("h" * 40)
    assert row is not None
    assert row["state"] == "queued"
    assert row["video_code"] == "ABC-008"
    assert row["session_id"] == "SESS-008"


def test_torrent_completed_event_marks_shadow_completed(wire):
    ev, shadow, consumer = wire
    store.emit(
        "TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="i" * 40,
        payload=json.dumps({
            "href": "/v/ABC-009", "video_code": "ABC-009", "category": "subtitle",
        }),
        repo=ev,
    )
    store.emit(
        "TorrentCompleted",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="i" * 40,
        payload=json.dumps({"completed_at": "2026-06-10T06:00:00Z"}),
        repo=ev,
    )
    consumer.run_once(event_repo=ev)
    row = shadow.get("i" * 40)
    assert row["state"] == "completed"
    assert row["completed_at"] == "2026-06-10T06:00:00Z"


def test_non_torrent_events_are_ignored(wire):
    ev, shadow, consumer = wire
    store.emit("RunStarted", session_id="SESS-008", entity_type="session",
               entity_id="SESS-008", repo=ev)
    store.emit("MovieDiscovered", session_id="SESS-008", entity_type="movie",
               entity_id="/v/X", repo=ev)
    consumer.run_once(event_repo=ev)
    assert shadow.list_all() == []


def test_replay_rebuilds_projection(wire):
    ev, shadow, consumer = wire
    store.emit(
        "TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="j" * 40,
        payload=json.dumps({
            "href": "/v/ABC-010", "video_code": "ABC-010", "category": "no_subtitle",
        }),
        repo=ev,
    )
    consumer.run_once(event_repo=ev)
    assert shadow.get("j" * 40) is not None

    # Replay: reset cursor + shadow, rebuild
    ev.advance_cursor(consumer.name, 0)
    shadow.reset()
    consumer.run_once(event_repo=ev)
    row = shadow.get("j" * 40)
    assert row is not None
    assert row["state"] == "queued"


def test_consumer_cursor_advances_between_runs(wire):
    ev, shadow, consumer = wire
    store.emit(
        "TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="k" * 40,
        payload=json.dumps({
            "href": "/v/K", "video_code": "K", "category": "subtitle",
        }),
        repo=ev,
    )
    n1 = consumer.run_once(event_repo=ev)
    n2 = consumer.run_once(event_repo=ev)
    assert n1 == 1
    assert n2 == 0  # cursor advanced; second run sees nothing


def test_invalid_payload_does_not_crash_consumer(wire):
    """Consumer must not crash on malformed payload JSON."""
    ev, shadow, consumer = wire
    from javdb.pipeline.events.models import PipelineEventRecord, utc_now_iso
    broken = PipelineEventRecord(
        event_type="TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id="l" * 40,
        payload="NOT_JSON",
        created_at=utc_now_iso(),
    )
    ev.append(broken)
    # Must not raise
    consumer.run_once(event_repo=ev)
    # Row may or may not be inserted (depends on impl); key is: no crash


def test_none_entity_id_torrent_queued_is_skipped(wire):
    """TorrentQueued with entity_id=None must be skipped (no NULL-key row)."""
    ev, shadow, consumer = wire
    from javdb.pipeline.events.models import PipelineEventRecord, utc_now_iso
    no_hash = PipelineEventRecord(
        event_type="TorrentQueued",
        session_id="SESS-008",
        entity_type="torrent",
        entity_id=None,
        payload=json.dumps({"href": "/v/X", "video_code": "X", "category": "subtitle"}),
        created_at=utc_now_iso(),
    )
    ev.append(no_hash)
    consumer.run_once(event_repo=ev)
    # Nothing in shadow
    assert shadow.list_all() == []
