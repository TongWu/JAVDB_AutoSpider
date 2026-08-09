"""Unit tests for the recheck-based missingFiles purge service."""

from __future__ import annotations

import time

from javdb.integrations.qb.purge_missing_files import (
    _completed_before,
    decide_delete_files,
    purge_instance,
)

_NOW = time.time()
_OLD = _NOW - 100 * 3600   # completed 100h ago
_RECENT = _NOW - 1 * 3600  # completed 1h ago

MB = 1024 * 1024
GB = 1024 * MB
THRESHOLD = 100 * MB
TOTAL = 7 * GB


def _f(size, progress, priority=1):
    return {"size": size, "progress": progress, "priority": priority}


# --- decide_delete_files (pure) ---------------------------------------------

def test_all_files_gone_deletes():
    files = [_f(TOTAL, 0.0), _f(15 * MB, 0.0)]
    delete, present = decide_delete_files(files, TOTAL, THRESHOLD)
    assert delete is True and present == 0


def test_big_file_present_keeps():
    files = [_f(TOTAL, 1.0), _f(15 * MB, 0.0)]
    delete, present = decide_delete_files(files, TOTAL, THRESHOLD)
    assert delete is False and present == TOTAL


def test_small_residue_present_deletes():
    # big file gone, only a <=100MB sample remains -> shrank >50% & residue small
    files = [_f(TOTAL, 0.0), _f(15 * MB, 1.0)]
    delete, present = decide_delete_files(files, TOTAL, THRESHOLD)
    assert delete is True and present == 15 * MB


def test_large_residue_above_threshold_keeps():
    # 200MB residue exceeds the 100MB threshold -> blocked even though shrank >50%
    files = [_f(TOTAL, 0.0), _f(200 * MB, 1.0)]
    delete, _ = decide_delete_files(files, TOTAL, THRESHOLD)
    assert delete is False


def test_deselected_large_file_is_ignored():
    # a priority-0 file is never on disk; it must not count as present
    files = [_f(TOTAL, 0.0, priority=0), _f(10 * MB, 1.0)]
    delete, present = decide_delete_files(files, TOTAL, THRESHOLD)
    assert delete is True and present == 10 * MB


def test_unknown_total_size_keeps():
    delete, _ = decide_delete_files([_f(0, 0.0)], 0, THRESHOLD)
    assert delete is False


# --- _completed_before (age gate) -------------------------------------------

def test_completed_before_gate():
    age = 24 * 3600
    assert _completed_before({"completion_on": _OLD}, now=_NOW, min_age_seconds=age) is True
    assert _completed_before({"completion_on": _RECENT}, now=_NOW, min_age_seconds=age) is False
    # never completed -> excluded
    assert _completed_before({"completion_on": 0}, now=_NOW, min_age_seconds=age) is False
    assert _completed_before({}, now=_NOW, min_age_seconds=age) is False


# --- purge_instance (end-to-end with a fake client) -------------------------

class FakeClient:
    base_url = "http://qb.local:8080"

    def __init__(self, torrents, files_by_hash, info_omit=()):
        self._torrents = torrents
        self._files = files_by_hash
        self._info_omit = set(info_omit)
        self.stopped = []
        self.rechecked = []
        self.deleted = None

    def get_torrents(self, category=None, torrent_filter="downloading"):
        return self._torrents

    def get_torrents_by_hashes(self, hashes):
        # recheck already finished -> report a terminal (non-checking) state.
        # info_omit simulates a hash missing from the info read.
        return [{"hash": h, "state": "stoppedDL"} for h in hashes if h not in self._info_omit]

    def get_torrent_files(self, info_hash):
        # A missing key returns None == "read failed / unknown", NOT "no files".
        return self._files.get(info_hash)

    def stop_torrents(self, hashes):
        self.stopped.extend(hashes)

    def recheck_torrents(self, hashes):
        self.rechecked.extend(hashes)

    def delete_torrents(self, hashes, delete_files=True):
        self.deleted = (list(hashes), delete_files)
        return True


_TORRENTS = [
    {"hash": "gone", "name": "gone", "state": "missingFiles", "total_size": TOTAL, "completion_on": _OLD},
    {"hash": "present", "name": "present", "state": "missingFiles", "total_size": TOTAL, "completion_on": _OLD},
    {"hash": "seeding", "name": "seeding", "state": "uploading", "total_size": TOTAL, "completion_on": _OLD},
]
_FILES = {
    "gone": [_f(TOTAL, 0.0)],
    "present": [_f(TOTAL, 1.0)],
}

_FAST = dict(settle=0, poll_interval=0, poll_timeout=1)


def test_purge_deletes_gone_keeps_present():
    client = FakeClient(_TORRENTS, _FILES)
    stats = purge_instance(client, label="Primary", dry_run=False, **_FAST)

    assert stats["missing"] == 2  # only the two missingFiles ones
    assert stats["to_delete"] == 1 and stats["left_present"] == 1
    assert stats["deleted"] == 1
    # genuinely-gone torrent deleted WITH files; present one untouched
    assert client.deleted == (["gone"], True)
    # every missingFiles torrent was stopped before recheck
    assert set(client.stopped) == {"gone", "present"}
    assert set(client.rechecked) == {"gone", "present"}


def test_dry_run_rechecks_but_deletes_nothing():
    client = FakeClient(_TORRENTS, _FILES)
    stats = purge_instance(client, label="Primary", dry_run=True, **_FAST)

    assert stats["to_delete"] == 1 and stats["deleted"] == 0
    assert client.deleted is None
    # dry-run still stops + rechecks (that is how the decision is computed)
    assert set(client.rechecked) == {"gone", "present"}


def test_file_list_unavailable_is_kept_not_deleted():
    # get_torrent_files returns None (read failed) -> must NOT be treated as empty
    torrents = [{"hash": "x", "name": "x", "state": "missingFiles", "total_size": TOTAL, "completion_on": _OLD}]
    client = FakeClient(torrents, files_by_hash={})  # no entry -> get_torrent_files returns None
    stats = purge_instance(client, label="Primary", dry_run=False, **_FAST)

    assert stats["missing"] == 1 and stats["unverified"] == 1 and stats["to_delete"] == 0
    assert client.deleted is None


def test_missing_info_row_is_kept_not_deleted():
    # hash absent from the info read -> unverifiable -> never deleted
    torrents = [{"hash": "x", "name": "x", "state": "missingFiles", "total_size": TOTAL, "completion_on": _OLD}]
    client = FakeClient(torrents, {"x": [_f(TOTAL, 0.0)]}, info_omit={"x"})
    stats = purge_instance(client, label="Primary", dry_run=False, **_FAST)

    assert stats["unverified"] == 1 and stats["to_delete"] == 0
    assert client.deleted is None


def test_recently_completed_is_skipped_not_rechecked():
    torrents = [
        {"hash": "gone", "name": "gone", "state": "missingFiles", "total_size": TOTAL, "completion_on": _OLD},
        {"hash": "fresh", "name": "fresh", "state": "missingFiles", "total_size": TOTAL, "completion_on": _RECENT},
    ]
    client = FakeClient(torrents, {"gone": [_f(TOTAL, 0.0)], "fresh": [_f(TOTAL, 0.0)]})
    stats = purge_instance(client, label="Primary", dry_run=False, min_age_hours=24, **_FAST)

    assert stats["missing"] == 1 and stats["skipped_recent"] == 1
    # the recent torrent is never even stopped/rechecked
    assert set(client.rechecked) == {"gone"}
    assert client.deleted == (["gone"], True)


# --- CLI --min-age-hours validation -----------------------------------------

def test_min_age_hours_rejects_negative_and_non_finite():
    import pytest
    from apps.cli.qb.purge_missing_files import parse_args

    assert parse_args(["--min-age-hours", "0"]).min_age_hours == 0
    assert parse_args(["--min-age-hours", "48"]).min_age_hours == 48
    for bad in ("-1", "nan", "inf"):
        with pytest.raises(SystemExit):
            parse_args(["--min-age-hours", bad])
