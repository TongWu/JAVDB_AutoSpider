import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord, ReconcileOptions
from javdb.ops.reconcile import service
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


def _old_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return AcquisitionOutcomeRepo(c)


class _FakeQb:
    def __init__(self, torrents):
        self._t = torrents

    def get_torrents_multiple_categories(self, categories, torrent_filter="downloading"):
        return self._t


def test_record_queued_writes_queued_row(repo):
    torrent = {"magnet": "magnet:?xt=urn:btih:" + "a" * 40, "href": "/v/1",
               "video_code": "ABC-123", "type": "subtitle"}
    service.record_queued(torrent, session_id="S1", repo=repo)
    got = repo.get("a" * 40)
    assert got.state == "queued"
    assert got.video_code == "ABC-123"
    assert got.category == "subtitle"
    assert got.session_id == "S1"


def test_record_queued_ignores_unparseable_magnet(repo):
    service.record_queued({"magnet": "not-a-magnet"}, session_id="S1", repo=repo)
    assert repo.list_active() == []


def test_apply_cleanup_completed_marks_hashes(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    res = service.apply_cleanup_completed({"hashes": ["h1", "h2"]}, repo=repo)
    assert repo.get("h1").state == "completed"
    assert repo.get("h2").state == "completed"   # minimal-insert for orphan
    assert res.marked_completed == 2


def test_run_marks_downloading_from_observation(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="d1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(0)))
    qb = _FakeQb([{"hash": "d1", "progress": 0.5, "state": "downloading"}])
    res = service.run(ReconcileOptions(), repo=repo, qb_client=qb)
    assert repo.get("d1").state == "downloading"
    assert res.marked_downloading == 1


def test_run_marks_stalled_when_absent_and_old(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="s1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(10)))
    qb = _FakeQb([])  # no longer in qB, and not completed
    res = service.run(ReconcileOptions(stalled_after_days=7), repo=repo, qb_client=qb)
    assert repo.get("s1").state == "stalled"
    assert res.marked_stalled == 1


def test_run_marks_failed_when_long_overdue(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="f1", href="/v/1", state="downloading",
                                         last_seen_at=_old_iso(20)))
    qb = _FakeQb([])
    res = service.run(ReconcileOptions(stalled_after_days=7), repo=repo, qb_client=qb)
    assert repo.get("f1").state == "failed"   # > 2x threshold
    assert res.marked_failed == 1


def test_run_dry_run_writes_nothing(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="d1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(0)))
    qb = _FakeQb([{"hash": "d1", "progress": 0.5, "state": "downloading"}])
    service.run(ReconcileOptions(dry_run=True), repo=repo, qb_client=qb)
    assert repo.get("d1").state == "queued"  # unchanged
