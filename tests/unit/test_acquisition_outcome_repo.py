import sqlite3

import pytest

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return c


def test_upsert_then_get(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    got = repo.get("h1")
    assert got.qb_hash == "h1"
    assert got.state == "queued"


def test_upsert_is_idempotent_on_hash(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="downloading"))
    assert repo.get("h1").state == "downloading"
    assert conn.execute("SELECT COUNT(*) FROM AcquisitionOutcome").fetchone()[0] == 1


def test_mark_state_updates_existing(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    repo.mark_state("h1", "completed", completed_at="t2", last_seen_at="t2")
    got = repo.get("h1")
    assert got.state == "completed"
    assert got.completed_at == "t2"


def test_mark_state_inserts_minimal_when_absent(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.mark_state("orphan", "completed", completed_at="t2", last_seen_at="t2")
    got = repo.get("orphan")
    assert got.state == "completed"
    assert got.href == ""


def test_list_active_excludes_terminal(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="a", state="queued"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="b", state="downloading"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="c", state="stalled"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="d", state="completed"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="e", state="failed"))
    active = {r.qb_hash for r in repo.list_active()}
    assert active == {"a", "b", "c"}
