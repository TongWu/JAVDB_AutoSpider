from datetime import datetime, timedelta, timezone

import pytest

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord, ReconcileOptions
from javdb.ops.reconcile import service


def _old_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


@pytest.fixture
def repo(acquisition_outcome_repo):
    return acquisition_outcome_repo


class _FakeQb:
    def __init__(self, torrents):
        self._t = torrents

    def get_torrents_multiple_categories(self, categories, torrent_filter="downloading"):
        return self._t


class _CategoryQb:
    def __init__(self, by_category, fail_categories=()):
        self._by_category = by_category
        self._fail_categories = set(fail_categories)
        self.requests = []

    def get_torrents(self, category, torrent_filter="downloading"):
        self.requests.append((category, torrent_filter))
        if category in self._fail_categories:
            raise RuntimeError(f"{category} down")
        return self._by_category.get(category, [])


class _FailingQb:
    def get_torrents_multiple_categories(self, categories, torrent_filter="downloading"):
        raise RuntimeError("qb down")


class _FlakyCleanupRepo:
    def __init__(self, repo):
        self._repo = repo

    def mark_state(self, qb_hash, state, *, completed_at=None, last_seen_at=None):
        if qb_hash == "h1":
            raise RuntimeError("bad hash")
        return self._repo.mark_state(
            qb_hash,
            state,
            completed_at=completed_at,
            last_seen_at=last_seen_at,
        )

    def get(self, qb_hash):
        return self._repo.get(qb_hash)


class _FailingUpsertRepo:
    def __init__(self, repo):
        self._repo = repo

    def list_active(self):
        return self._repo.list_active()

    def upsert(self, record):
        raise RuntimeError("upsert failed")


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


def test_apply_cleanup_completed_keeps_processing_after_one_failure(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    wrapped = _FlakyCleanupRepo(repo)

    res = service.apply_cleanup_completed({"hashes": ["h1", "h2"]}, repo=wrapped)

    assert repo.get("h1").state == "queued"
    assert repo.get("h2").state == "completed"
    assert res.errors
    assert res.marked_completed == 1


def test_run_marks_downloading_from_observation(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="d1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(0)))
    qb = _FakeQb([{"hash": "d1", "progress": 0.5, "state": "downloading"}])
    res = service.run(ReconcileOptions(), repo=repo, qb_client=qb)
    assert repo.get("d1").state == "downloading"
    assert res.marked_downloading == 1


def test_run_refreshes_last_seen_when_observed_state_is_unchanged(repo):
    old_ts = _old_iso(10)
    repo.upsert(
        AcquisitionOutcomeRecord(
            qb_hash="d1",
            href="/v/1",
            state="downloading",
            last_seen_at=old_ts,
        )
    )
    qb = _FakeQb([{"hash": "d1", "progress": 0.5, "state": "downloading"}])

    res = service.run(ReconcileOptions(), repo=repo, qb_client=qb)

    got = repo.get("d1")
    assert got.state == "downloading"
    assert got.last_seen_at != old_ts
    assert res.outcomes_updated == 1
    assert res.marked_downloading == 0


def test_run_marks_stalled_when_absent_and_old(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="s1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(10)))
    qb = _FakeQb([])  # no longer in qB, and not completed
    res = service.run(ReconcileOptions(stalled_after_days=7), repo=repo, qb_client=qb)
    assert repo.get("s1").state == "stalled"
    assert res.marked_stalled == 1


def test_run_handles_naive_last_seen_timestamp_when_absent_and_old(repo):
    old_ts = (datetime.now() - timedelta(days=10)).isoformat()
    repo.upsert(
        AcquisitionOutcomeRecord(
            qb_hash="s1",
            href="/v/1",
            state="queued",
            last_seen_at=old_ts,
        )
    )
    qb = _FakeQb([])

    res = service.run(ReconcileOptions(stalled_after_days=7), repo=repo, qb_client=qb)

    assert repo.get("s1").state == "stalled"
    assert res.errors == []
    assert res.marked_stalled == 1


def test_run_skips_absent_transitions_when_category_scan_is_partial(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="s1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(10)))
    qb = _CategoryQb({"Movies": []})

    res = service.run(
        ReconcileOptions(
            categories=("Movies",),
            stalled_after_days=7,
            infer_absent=False,
        ),
        repo=repo,
        qb_client=qb,
    )

    assert repo.get("s1").state == "queued"
    assert res.marked_stalled == 0
    assert res.errors == []


def test_run_skips_transitions_when_one_qb_category_fails(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="s1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(10)))
    qb = _CategoryQb({"Movies": []}, fail_categories={"One Off"})

    res = service.run(
        ReconcileOptions(
            categories=("Movies", "One Off"),
            stalled_after_days=7,
        ),
        repo=repo,
        qb_client=qb,
    )

    assert repo.get("s1").state == "queued"
    assert res.observed == 0
    assert res.marked_stalled == 0
    assert res.errors == ["One Off down"]


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
    res = service.run(ReconcileOptions(dry_run=True), repo=repo, qb_client=qb)
    assert repo.get("d1").state == "queued"  # unchanged
    assert res.outcomes_updated == 0
    assert res.marked_downloading == 0


def test_run_returns_error_and_leaves_rows_unchanged_when_qb_fails(repo):
    old_ts = _old_iso(10)
    repo.upsert(
        AcquisitionOutcomeRecord(
            qb_hash="d1",
            href="/v/1",
            state="queued",
            last_seen_at=old_ts,
        )
    )

    res = service.run(ReconcileOptions(stalled_after_days=7), repo=repo, qb_client=_FailingQb())

    got = repo.get("d1")
    assert got.state == "queued"
    assert got.last_seen_at == old_ts
    assert res.errors


def test_run_rejects_unknown_sources_without_absent_state_transitions(repo):
    old_ts = _old_iso(10)
    repo.upsert(
        AcquisitionOutcomeRecord(
            qb_hash="s1",
            href="/v/1",
            state="queued",
            last_seen_at=old_ts,
        )
    )

    res = service.run(
        ReconcileOptions(sources=("qbb",), stalled_after_days=7),
        repo=repo,
        qb_client=_FakeQb([]),
    )

    got = repo.get("s1")
    assert got.state == "queued"
    assert got.last_seen_at == old_ts
    assert res.errors == ["unsupported source: qbb"]


def test_run_rejects_nonpositive_stalled_threshold_without_transitions(repo):
    old_ts = _old_iso(10)
    repo.upsert(
        AcquisitionOutcomeRecord(
            qb_hash="s1",
            href="/v/1",
            state="queued",
            last_seen_at=old_ts,
        )
    )

    res = service.run(
        ReconcileOptions(stalled_after_days=0),
        repo=repo,
        qb_client=_FakeQb([]),
    )

    got = repo.get("s1")
    assert got.state == "queued"
    assert got.last_seen_at == old_ts
    assert res.errors == ["stalled_after_days must be >= 1"]


def test_run_counts_marked_only_after_successful_upsert(repo):
    repo.upsert(
        AcquisitionOutcomeRecord(
            qb_hash="d1",
            href="/v/1",
            state="queued",
            last_seen_at=_old_iso(0),
        )
    )
    wrapped = _FailingUpsertRepo(repo)
    qb = _FakeQb([{"hash": "d1", "progress": 0.5, "state": "downloading"}])

    res = service.run(ReconcileOptions(), repo=wrapped, qb_client=qb)

    assert repo.get("d1").state == "queued"
    assert res.outcomes_updated == 0
    assert res.marked_downloading == 0
    assert res.errors == ["upsert failed"]
