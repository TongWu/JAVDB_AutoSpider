# tests/unit/test_adr036_p2_emit_torrent_completed.py
"""
Tests that TorrentCompleted events are emitted after mark_state("completed"),
with session_id recovered from the AcquisitionOutcome row.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.ops.reconcile import service as reconcile_service
from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

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


@pytest.fixture
def event_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_EVENT_DDL)
    return PipelineEventRepo(c)


def test_torrent_completed_emitted_with_session_id_from_row(
    acquisition_outcome_repo, monkeypatch
):
    """apply_cleanup_completed must emit TorrentCompleted with the row's session_id."""
    qb_hash = "e" * 40
    acquisition_outcome_repo.upsert(AcquisitionOutcomeRecord(
        qb_hash=qb_hash, href="/v/ABC-005", state="queued", session_id="SESS-005"
    ))

    emitted = []
    monkeypatch.setattr(
        reconcile_service, "_emit_event",
        lambda event_type, **kw: emitted.append((event_type, kw)),
    )

    reconcile_service.apply_cleanup_completed(
        {"hashes": [qb_hash]},
        repo=acquisition_outcome_repo,
    )

    assert len(emitted) == 1
    et, kw = emitted[0]
    assert et == "TorrentCompleted"
    assert kw["entity_id"] == qb_hash
    assert kw["session_id"] == "SESS-005"
    p = json.loads(kw["payload"])
    assert "completed_at" in p


def test_torrent_completed_orphan_row_session_empty(acquisition_outcome_repo, monkeypatch):
    """Orphan hash (not in DB before cleanup) gets session_id='' -> emit returns None."""
    qb_hash = "f" * 40

    emitted = []
    monkeypatch.setattr(
        reconcile_service, "_emit_event",
        lambda event_type, **kw: emitted.append((event_type, kw)),
    )

    reconcile_service.apply_cleanup_completed(
        {"hashes": [qb_hash]},
        repo=acquisition_outcome_repo,
    )

    # Orphan rows have no session_id; the emit should still be called
    # (emit internally returns None when session_id is falsy)
    assert len(emitted) == 1
    et, kw = emitted[0]
    assert et == "TorrentCompleted"
    # session_id from a freshly-inserted orphan row is None -> coerced to ""
    assert kw["session_id"] in ("", None)


def test_apply_cleanup_completed_still_succeeds_when_emit_raises(
    acquisition_outcome_repo, monkeypatch
):
    """Pipeline step succeeds even if _emit_event raises."""
    qb_hash = "g" * 40
    acquisition_outcome_repo.upsert(AcquisitionOutcomeRecord(
        qb_hash=qb_hash, href="/v/X", state="queued", session_id="SESS-X"
    ))

    monkeypatch.setattr(
        reconcile_service, "_emit_event",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("inject")),
    )

    # apply_cleanup_completed must not raise even if _emit_event does
    result = reconcile_service.apply_cleanup_completed(
        {"hashes": [qb_hash]},
        repo=acquisition_outcome_repo,
    )
    assert result.marked_completed == 1
