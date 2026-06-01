"""Tests for the shared read-only qB helpers (ADR-024)."""

from __future__ import annotations

import requests

from javdb.integrations.qb import readonly


def test_filter_recent_torrents_by_time_and_category():
    now = 1_000_000
    torrents = [
        {"hash": "A", "added_on": now - 10, "category": "Daily Ingestion"},
        {"hash": "B", "added_on": now - 10_000_000, "category": "Daily Ingestion"},  # too old
        {"hash": "C", "added_on": now - 10, "category": "Other"},  # wrong category
    ]
    out = readonly.filter_recent_torrents(
        torrents, days=2, categories=["Daily Ingestion"], now=now
    )
    assert [t["hash"] for t in out] == ["A"]


def test_filter_recent_torrents_no_category_keeps_all_recent():
    now = 1_000_000
    torrents = [
        {"hash": "A", "added_on": now - 10, "category": "X"},
        {"hash": "B", "added_on": now - 10, "category": "Y"},
    ]
    out = readonly.filter_recent_torrents(torrents, days=2, categories=None, now=now)
    assert {t["hash"] for t in out} == {"A", "B"}


def test_get_torrent_files_returns_list_on_200():
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return [{"name": "v.mp4", "size": 1}]

    class _Session:
        def get(self, *a, **k):
            return _Resp()

    files = readonly.get_torrent_files(
        _Session(), "http://qb", "HASH", proxies=None, verify=True, timeout=5
    )
    assert files == [{"name": "v.mp4", "size": 1}]


def test_get_torrent_files_returns_none_on_error_status():
    class _Resp:
        status_code = 500

    class _Session:
        def get(self, *a, **k):
            return _Resp()

    files = readonly.get_torrent_files(
        _Session(), "http://qb", "HASH", proxies=None, verify=True, timeout=5
    )
    assert files is None


def test_get_torrent_files_returns_none_on_request_exception():
    class _Session:
        def get(self, *a, **k):
            raise requests.RequestException("network error")

    files = readonly.get_torrent_files(_Session(), "http://qb", "HASH")
    assert files is None


def test_get_torrent_files_has_request_defaults():
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return []

    class _Session:
        def __init__(self):
            self.kwargs = None

        def get(self, *a, **k):
            self.kwargs = k
            return _Resp()

    session = _Session()
    assert readonly.get_torrent_files(session, "http://qb", "HASH") == []
    assert session.kwargs["proxies"] is None
    assert session.kwargs["verify"] is True
    assert session.kwargs["timeout"] == 30


def test_wait_for_metadata_readiness_uses_injected_fetcher():
    now = 1_000_000
    torrents = [{"hash": "A", "added_on": now - 10}]
    calls = {"n": 0}

    def fetch(_hash):
        calls["n"] += 1
        return [{"name": "v.mp4", "size": 10}]

    summary = readonly.wait_for_metadata_readiness(
        torrents,
        fetch_files=fetch,
        max_wait_seconds=5,
        poll_interval_seconds=1,
        recent_window_seconds=900,
        now=now,
    )
    assert summary["ready"] == 1
    assert summary["pending"] == 0
    assert calls["n"] == 1


def test_wait_for_metadata_readiness_keeps_polling_when_only_api_failures(monkeypatch):
    now = 1_000_000
    torrents = [
        {"hash": "A", "added_on": now - 10},
        {"hash": "B", "added_on": now - 10},
    ]
    calls = {"n": 0}
    sleeps = []
    monotonic_values = iter([0.0, 0.0, 2.0])

    monkeypatch.setattr(readonly.time, "monotonic", lambda: next(monotonic_values))

    def fetch(_hash):
        calls["n"] += 1
        return None

    summary = readonly.wait_for_metadata_readiness(
        torrents,
        fetch_files=fetch,
        max_wait_seconds=2,
        poll_interval_seconds=1,
        recent_window_seconds=900,
        now=now,
        sleep=sleeps.append,
    )

    assert summary["ready"] == 0
    assert summary["pending"] == 0
    assert summary["api_failures"] == 2
    assert summary["waited_seconds"] == 1
    assert calls["n"] == 4
    assert sleeps == [1]


def test_wait_for_metadata_readiness_has_wait_defaults():
    now = 1_000_000
    torrents = [{"hash": "A", "added_on": now - 10}]

    summary = readonly.wait_for_metadata_readiness(
        torrents,
        fetch_files=lambda _hash: [{"name": "v.mp4", "size": 10}],
        now=now,
    )

    assert summary["checked"] == 1
    assert summary["ready"] == 1


def test_wait_for_metadata_readiness_counts_fetch_time_against_deadline(monkeypatch):
    now = 1_000_000
    torrents = [{"hash": "A", "added_on": now - 10}]
    calls = {"n": 0}
    sleeps = []
    monotonic_values = iter([0.0, 6.0])

    monkeypatch.setattr(readonly.time, "monotonic", lambda: next(monotonic_values))

    def fetch(_hash):
        calls["n"] += 1
        return []

    summary = readonly.wait_for_metadata_readiness(
        torrents,
        fetch_files=fetch,
        max_wait_seconds=5,
        poll_interval_seconds=1,
        recent_window_seconds=900,
        now=now,
        sleep=sleeps.append,
    )

    assert summary["pending"] == 1
    assert summary["waited_seconds"] == 0
    assert calls["n"] == 1
    assert sleeps == []
