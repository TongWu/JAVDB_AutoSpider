"""ADR-024 IMP-10: probe-queue repository."""

from __future__ import annotations

import sqlite3

import pytest

from javdb.storage.repos.torrent_probe_repo import (
    ProbeCandidate,
    TorrentProbeRepo,
)

_DDL = """
CREATE TABLE TorrentProbeCandidate (
    info_hash TEXT NOT NULL, movie_href TEXT NOT NULL, video_code TEXT,
    javdb_category TEXT, magnet_uri TEXT NOT NULL, magnet_name TEXT,
    javdb_tags_json TEXT, javdb_size_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','probed','failed')),
    enqueued_at TEXT NOT NULL, probed_at TEXT,
    PRIMARY KEY (info_hash, movie_href)
);
"""


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    return TorrentProbeRepo(conn)


def _cand(h="HASH1", href="/v/abc"):
    return ProbeCandidate(
        info_hash=h, movie_href=href, video_code="ABC-123",
        javdb_category="subtitle", magnet_uri=f"magnet:?xt=urn:btih:{h}",
        magnet_name="ABC-123-C", javdb_tags=["中文字幕"], javdb_size_text="8GB",
    )


def test_enqueue_then_list_pending(repo):
    repo.enqueue(_cand(), enqueued_at="2026-06-19T00:00:00Z")
    pending = repo.list_pending()
    assert len(pending) == 1
    assert pending[0].info_hash == "HASH1"
    assert pending[0].magnet_uri == "magnet:?xt=urn:btih:HASH1"


def test_enqueue_is_idempotent_upsert(repo):
    repo.enqueue(_cand(), enqueued_at="2026-06-19T00:00:00Z")
    repo.enqueue(_cand(), enqueued_at="2026-06-19T01:00:00Z")  # same PK
    assert len(repo.list_pending()) == 1


def test_mark_probed_removes_from_pending(repo):
    repo.enqueue(_cand(), enqueued_at="2026-06-19T00:00:00Z")
    repo.mark_status("HASH1", "/v/abc", "probed", probed_at="2026-06-19T02:00:00Z")
    assert repo.list_pending() == []


def test_mark_failed_also_leaves_pending_empty(repo):
    repo.enqueue(_cand(), enqueued_at="2026-06-19T00:00:00Z")
    repo.mark_status("HASH1", "/v/abc", "failed", probed_at="2026-06-19T02:00:00Z")
    assert repo.list_pending() == []


def test_list_pending_respects_limit(repo):
    for i in range(5):
        repo.enqueue(_cand(h=f"H{i}"), enqueued_at="2026-06-19T00:00:00Z")
    assert len(repo.list_pending(limit=3)) == 3
