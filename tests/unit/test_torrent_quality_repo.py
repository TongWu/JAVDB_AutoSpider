"""Round-trip + invariant tests for TorrentQualityRepo (ADR-024 Phase 1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from javdb.quality.models import EvaluationRecord, EvidenceRecord
from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

_MIGRATION = Path(
    "javdb/migrations/d1/2026_05_31_add_torrent_quality_tables.sql"
)


@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MIGRATION.read_text(encoding="utf-8"))
    try:
        yield conn
    finally:
        conn.close()


def test_upsert_and_get_evidence(conn):
    repo = TorrentQualityRepo(conn)
    rec = EvidenceRecord(
        info_hash="ABC123",
        probe_schema_version="v1",
        target_role="production_download",
        total_size_bytes=1000,
        main_video_size_bytes=900,
        main_video_ratio=0.9,
        reasons=["main_video_detected"],
        features={"container": "mkv"},  # non-promoted only
    )
    repo.upsert_evidence(rec)

    row = repo.get_evidence("ABC123", "v1", "production_download")
    assert row is not None
    assert row["total_size_bytes"] == 1000
    assert row["main_video_ratio"] == 0.9
    assert "main_video_detected" in row["reasons_json"]
    assert "mkv" in row["features_json"]


def test_upsert_evidence_is_idempotent(conn):
    repo = TorrentQualityRepo(conn)
    rec = EvidenceRecord(
        info_hash="ABC123",
        probe_schema_version="v1",
        target_role="production_download",
        total_size_bytes=1000,
    )
    repo.upsert_evidence(rec)
    rec.total_size_bytes = 2000
    repo.upsert_evidence(rec)

    row = repo.get_evidence("ABC123", "v1", "production_download")
    assert row["total_size_bytes"] == 2000
    assert conn.execute(
        "SELECT COUNT(*) FROM TorrentQualityEvidence"
    ).fetchone()[0] == 1


def test_features_must_not_duplicate_promoted_columns(conn):
    repo = TorrentQualityRepo(conn)
    rec = EvidenceRecord(
        info_hash="ABC123",
        probe_schema_version="v1",
        target_role="production_download",
        video_file_count=1,
        features={"video_file_count": 1},  # collides with a promoted column
    )
    with pytest.raises(ValueError):
        repo.upsert_evidence(rec)


def test_upsert_and_list_evaluation(conn):
    repo = TorrentQualityRepo(conn)
    rec = EvaluationRecord(
        info_hash="ABC123",
        movie_href="/v/abc",
        scoring_version="v1",
        video_code="ABC-123",
        javdb_category="subtitle",
        javdb_tags=["1080p", "subtitle"],
        score=0.82,
        shadow_rank=1,
        would_replace_current_choice=False,
        decision="accepted_shadow",
        reasons=["subtitle_file_missing"],
    )
    repo.upsert_evaluation(rec)

    rows = repo.list_evaluations_for_movie("/v/abc")
    assert len(rows) == 1
    assert rows[0]["video_code"] == "ABC-123"
    assert rows[0]["score"] == 0.82
    assert rows[0]["policy_mode"] == "shadow"
    assert rows[0]["would_replace_current_choice"] == 0
    assert "1080p" in rows[0]["javdb_tags_json"]


def test_list_recent_evaluations_orders_by_created_at(conn):
    repo = TorrentQualityRepo(conn)
    repo.upsert_evaluation(
        EvaluationRecord(
            info_hash="ABC123",
            movie_href="/v/abc",
            scoring_version="v1",
            video_code="ABC-123",
            shadow_rank=2,
        )
    )
    repo.upsert_evaluation(
        EvaluationRecord(
            info_hash="DEF456",
            movie_href="/v/def",
            scoring_version="v1",
            video_code="DEF-456",
            shadow_rank=1,
        )
    )

    rows = repo.list_recent_evaluations(limit=1)
    assert len(rows) == 1
    assert rows[0]["info_hash"] == "DEF456"
