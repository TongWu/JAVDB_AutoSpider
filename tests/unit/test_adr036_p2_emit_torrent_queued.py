# tests/unit/test_adr036_p2_emit_torrent_queued.py
"""
Tests that TorrentQueued events are emitted after a successful qB add
and record_queued_acquisition call, and that the pipeline survives emit failure.
"""
import json
import sqlite3
from unittest.mock import MagicMock, patch

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
_VALID_HASH = "d" * 40


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def test_torrent_queued_emitted_after_successful_add(event_repo):
    """Simulate the emit that should follow _record_queued_acquisition success."""
    torrent = {
        "magnet": "magnet:?xt=urn:btih:" + _VALID_HASH,
        "href": "/v/ABC-004",
        "video_code": "ABC-004",
        "type": "subtitle",
    }
    store.emit(
        "TorrentQueued",
        session_id="SESS-004",
        entity_type="torrent",
        entity_id=_VALID_HASH,
        payload=json.dumps({
            "href": torrent["href"],
            "video_code": torrent["video_code"],
            "category": torrent["type"],
        }),
        repo=event_repo,
    )
    events = event_repo.read_since(0, limit=10)
    assert len(events) == 1
    e = events[0]
    assert e.event_type == "TorrentQueued"
    assert e.entity_id == _VALID_HASH
    p = json.loads(e.payload)
    assert p["href"] == "/v/ABC-004"
    assert p["category"] == "subtitle"


def test_torrent_queued_no_session_returns_none():
    result = store.emit(
        "TorrentQueued",
        session_id="",
        entity_type="torrent",
        entity_id=_VALID_HASH,
    )
    assert result is None


def test_torrent_queued_best_effort_survives_emit_raise(event_repo):
    with patch.object(event_repo, "append", side_effect=RuntimeError("inject")):
        result = store.emit(
            "TorrentQueued",
            session_id="SESS-004",
            entity_type="torrent",
            entity_id=_VALID_HASH,
            repo=event_repo,
        )
    assert result is None


def test_run_uploader_success_path_emits_torrent_queued(monkeypatch):
    """run_uploader's success branch must call _emit_event with TorrentQueued."""
    from javdb.integrations.qb.uploader import service as uploader_service
    from javdb.integrations.qb.uploader.options import QbUploaderOptions

    queued_events = []
    sink = MagicMock()
    sink.saved = False
    sink.error = None
    sink.backend = "mock"

    monkeypatch.setattr(uploader_service, "global_proxy_helper", None)
    monkeypatch.setattr(uploader_service, "initialize_proxy_helper", lambda *a, **kw: None)
    monkeypatch.setattr(uploader_service, "test_qbittorrent_connection", lambda *a, **kw: True)
    monkeypatch.setattr(
        uploader_service, "resolve_qb_uploader_csv_path",
        lambda **kw: MagicMock(source="manual", path="fake.csv"),
    )
    monkeypatch.setattr(uploader_service, "read_csv_file", lambda p: ([{
        "magnet": "magnet:?xt=urn:btih:" + _VALID_HASH,
        "title": "ABC-004 [sub]",
        "type": "subtitle",
        "href": "/v/ABC-004",
        "video_code": "ABC-004",
    }], True))
    monkeypatch.setattr(uploader_service, "login_to_qbittorrent", lambda *a, **kw: True)
    monkeypatch.setattr(uploader_service, "get_existing_torrents", lambda *a, **kw: set())
    monkeypatch.setattr(uploader_service, "add_torrent_to_qbittorrent", lambda *a, **kw: True)
    monkeypatch.setattr(uploader_service, "time", MagicMock(sleep=lambda *a, **kw: None))
    monkeypatch.setattr(uploader_service, "save_uploader_stats", lambda *a, **kw: sink)
    monkeypatch.setattr(uploader_service, "commit_workflow_outputs", lambda *a, **kw: None)
    monkeypatch.setattr(uploader_service, "_record_acquisition_queued", lambda t, s: None)
    monkeypatch.setattr(
        uploader_service, "_emit_event",
        lambda event_type, **kw: queued_events.append(event_type),
    )

    result = uploader_service.run_uploader(QbUploaderOptions(mode="daily", session_id="SESS-004"))

    assert result.exit_code == 0
    assert "TorrentQueued" in queued_events
