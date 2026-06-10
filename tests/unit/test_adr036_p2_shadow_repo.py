# tests/unit/test_adr036_p2_shadow_repo.py
"""Tests for AcquisitionOutcomeShadowRepo."""
import sqlite3

import pytest

from javdb.storage.repos.pipeline_event_repo import AcquisitionOutcomeShadowRepo

_DDL = """
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
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return AcquisitionOutcomeShadowRepo(c)


def test_upsert_queued_inserts_new_row(repo):
    repo.upsert_queued(
        qb_hash="h1",
        href="/v/ABC-001",
        video_code="ABC-001",
        category="subtitle",
        queued_at="2026-06-10T01:00:00Z",
        session_id="SESS-007",
    )
    row = repo.get("h1")
    assert row is not None
    assert row["state"] == "queued"
    assert row["href"] == "/v/ABC-001"
    assert row["video_code"] == "ABC-001"
    assert row["category"] == "subtitle"
    assert row["session_id"] == "SESS-007"


def test_upsert_queued_is_idempotent(repo):
    """Second upsert with same hash should update, not error."""
    repo.upsert_queued("h1", "/v/X", "X", "subtitle", "2026-06-10T01:00:00Z", "S1")
    repo.upsert_queued("h1", "/v/Y", "Y", "no_subtitle", "2026-06-10T02:00:00Z", "S2")
    row = repo.get("h1")
    assert row["href"] == "/v/Y"
    assert row["video_code"] == "Y"


def test_mark_completed_updates_state_and_completed_at(repo):
    repo.upsert_queued("h2", "/v/B", "B", "subtitle", "2026-06-10T01:00:00Z", "S1")
    repo.mark_completed("h2", completed_at="2026-06-10T05:00:00Z")
    row = repo.get("h2")
    assert row["state"] == "completed"
    assert row["completed_at"] == "2026-06-10T05:00:00Z"


def test_mark_completed_noop_for_unknown_hash(repo):
    """mark_completed on an unknown hash should not raise."""
    repo.mark_completed("unknown", completed_at="2026-06-10T00:00:00Z")
    assert repo.get("unknown") is None


def test_list_all_returns_all_rows(repo):
    repo.upsert_queued("h3", "/v/C", "C", "subtitle", "2026-06-10T01:00:00Z", "S1")
    repo.upsert_queued("h4", "/v/D", "D", "subtitle", "2026-06-10T01:00:00Z", "S1")
    rows = repo.list_all()
    assert len(rows) == 2


def test_reset_clears_all_rows(repo):
    repo.upsert_queued("h5", "/v/E", "E", "subtitle", "2026-06-10T01:00:00Z", "S1")
    repo.reset()
    assert repo.list_all() == []


def test_get_returns_none_for_missing(repo):
    assert repo.get("nonexistent") is None
