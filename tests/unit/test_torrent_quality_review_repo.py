"""ADR-024 IMP-08: operator review-label repository."""

from __future__ import annotations

import sqlite3

import pytest

from javdb.storage.repos.torrent_quality_review_repo import (
    ReviewLabel,
    TorrentQualityReviewRepo,
)

_DDL = """
CREATE TABLE TorrentQualityReviewLabel (
    info_hash TEXT NOT NULL, movie_href TEXT NOT NULL, scoring_version TEXT NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('accept','reject','skip')),
    reviewer TEXT, note TEXT, reviewed_at TEXT NOT NULL,
    PRIMARY KEY (info_hash, movie_href, scoring_version)
);
"""


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    return TorrentQualityReviewRepo(conn)


def _label(label="accept"):
    return ReviewLabel(
        info_hash="h1", movie_href="/v/abc", scoring_version="adr024-shadow-v1",
        label=label, reviewer="ted", note="looks good",
    )


def test_upsert_then_list(repo):
    repo.upsert_label(_label(), reviewed_at="2026-06-20T00:00:00Z")
    rows = repo.list_labels(movie_href="/v/abc")
    assert len(rows) == 1
    assert rows[0]["label"] == "accept"
    assert rows[0]["reviewer"] == "ted"


def test_upsert_is_idempotent_and_updates(repo):
    repo.upsert_label(_label("accept"), reviewed_at="2026-06-20T00:00:00Z")
    repo.upsert_label(_label("reject"), reviewed_at="2026-06-20T01:00:00Z")
    rows = repo.list_labels(movie_href="/v/abc")
    assert len(rows) == 1
    assert rows[0]["label"] == "reject"


def test_invalid_label_rejected_by_check(repo):
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert_label(_label("maybe"), reviewed_at="2026-06-20T00:00:00Z")


def test_list_labels_limit_truncates(repo):
    for i in range(3):
        repo.upsert_label(
            ReviewLabel(info_hash=f"h{i}", movie_href="/v/abc",
                        scoring_version="adr024-shadow-v1", label="accept"),
            reviewed_at=f"2026-06-20T0{i}:00:00Z",
        )
    assert len(repo.list_labels(movie_href="/v/abc", limit=1)) == 1
    assert len(repo.list_labels(movie_href="/v/abc")) == 3


def test_list_labels_rejects_non_positive_limit(repo):
    repo.upsert_label(_label(), reviewed_at="2026-06-20T00:00:00Z")
    with pytest.raises(ValueError):
        repo.list_labels(limit=0)
    with pytest.raises(ValueError):
        repo.list_labels(limit=-1)
