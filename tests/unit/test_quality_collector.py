"""Tests for ADR-024 Phase 1 production-download evidence collection."""

from __future__ import annotations

from types import SimpleNamespace

from javdb.quality.collector import (
    PRODUCTION_TARGET_ROLE,
    _build_context,
    collect_production_evidence,
)


class FakeQualityRepo:
    def __init__(self) -> None:
        self.evidence = []
        self.evaluations = []

    def upsert_evidence(self, record) -> None:
        self.evidence.append(record)

    def upsert_evaluation(self, record) -> None:
        self.evaluations.append(record)


def test_collects_evidence_and_evaluation_per_torrent():
    repo = FakeQualityRepo()
    torrent = {"hash": "HASH1", "name": "ABC-123-C 中文字幕"}
    files = [
        {"name": "ABC-123-C.mkv", "size": 5_000_000_000},
        {"name": "ABC-123-C.srt", "size": 60_000},
    ]

    summary = collect_production_evidence(
        torrents=[torrent],
        fetch_files=lambda info_hash: files if info_hash == "HASH1" else None,
        repo=repo,
        context_for=lambda _torrent: {
            "movie_href": "/v/abc",
            "video_code": "ABC-123",
            "javdb_category": "subtitle",
            "magnet_name": torrent["name"],
            "javdb_tags": ["中文字幕"],
            "javdb_size_text": None,
        },
    )

    assert summary["evidence_written"] == 1
    assert summary["evaluations_written"] == 1
    assert summary["probe_unavailable"] == 0
    assert len(repo.evidence) == 1
    assert len(repo.evaluations) == 1

    evidence = repo.evidence[0]
    assert evidence.info_hash == "HASH1"
    assert evidence.target_role == PRODUCTION_TARGET_ROLE
    assert evidence.metadata_status == "metadata_received"
    assert evidence.main_video_size_bytes == 5_000_000_000
    assert evidence.features == {"main_video_name": "ABC-123-C.mkv"}

    evaluation = repo.evaluations[0]
    assert evaluation.info_hash == "HASH1"
    assert evaluation.movie_href == "/v/abc"
    assert evaluation.policy_mode == "shadow"
    assert evaluation.decision == "accepted_shadow"


def test_records_probe_unavailable_when_metadata_missing():
    repo = FakeQualityRepo()

    summary = collect_production_evidence(
        torrents=[{"hash": "HASH1", "name": "ABC-123"}],
        fetch_files=lambda _info_hash: None,
        repo=repo,
        context_for=lambda _torrent: {},
    )

    assert summary["probe_unavailable"] == 1
    assert summary["evidence_written"] == 1
    assert summary["evaluations_written"] == 0
    assert len(repo.evidence) == 1
    assert repo.evidence[0].metadata_status == "probe_unavailable"
    assert repo.evidence[0].reasons == ["probe_unavailable"]


def test_skips_torrents_without_hash():
    repo = FakeQualityRepo()

    summary = collect_production_evidence(
        torrents=[{"name": "ABC-123"}],
        fetch_files=lambda _info_hash: [],
        repo=repo,
        context_for=lambda _torrent: {},
    )

    assert summary["skipped"] == 1
    assert summary["evidence_written"] == 0
    assert repo.evidence == []
    assert repo.evaluations == []


def test_build_context_uses_acquisition_outcome_join():
    torrent = {"hash": "HASH1", "name": "ABC-123-C", "category": "qB category"}
    outcome = SimpleNamespace(
        href="/v/abc",
        video_code="ABC-123",
        category="subtitle",
    )

    context = _build_context(torrent, outcome)

    assert context == {
        "movie_href": "/v/abc",
        "video_code": "ABC-123",
        "javdb_category": "subtitle",
        "magnet_name": "ABC-123-C",
        "javdb_tags": [],
        "javdb_size_text": None,
    }


def test_build_context_without_outcome_does_not_use_qb_category():
    torrent = {"hash": "HASH1", "name": "ABC-123", "category": "qB category"}

    context = _build_context(torrent, None)

    assert context["movie_href"] == ""
    assert context["video_code"] is None
    assert context["javdb_category"] is None
    assert context["magnet_name"] == "ABC-123"
