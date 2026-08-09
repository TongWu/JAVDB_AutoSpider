"""Round-trip + invariant tests for TorrentQualityRepo (ADR-024 Phase 1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from javdb.quality.models import EvaluationRecord, EvidenceRecord
from javdb.storage.repos.torrent_quality_repo import TorrentQualityRepo

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = (
    _REPO_ROOT / "javdb/migrations/d1/2026_05_31_add_torrent_quality_tables.sql"
)
_MIGRATION_PROBE = (
    _REPO_ROOT / "javdb/migrations/d1/2026_06_19_add_torrent_probe_candidate.sql"
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
    assert row["reasons"] == ["main_video_detected"]
    assert row["features"] == {"container": "mkv"}


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
    with pytest.raises(
        ValueError, match=r"must not duplicate promoted columns.*video_file_count"
    ):
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
    assert rows[0]["javdb_tags"] == ["1080p", "subtitle"]
    assert rows[0]["reasons"] == ["subtitle_file_missing"]


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
    # Artificial timestamp for deterministic ordering in the repo test.
    conn.execute(
        """
        UPDATE TorrentQualityEvaluation
        SET created_at = ?
        WHERE info_hash = ? AND movie_href = ? AND scoring_version = ?
        """,
        ("2026-05-31T00:00:00.000Z", "ABC123", "/v/abc", "v1"),
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
    # Artificial timestamp for deterministic ordering in the repo test.
    conn.execute(
        """
        UPDATE TorrentQualityEvaluation
        SET created_at = ?
        WHERE info_hash = ? AND movie_href = ? AND scoring_version = ?
        """,
        ("2026-05-31T00:00:01.000Z", "DEF456", "/v/def", "v1"),
    )

    rows = repo.list_recent_evaluations(limit=1)
    assert len(rows) == 1
    assert rows[0]["info_hash"] == "DEF456"


def test_list_recent_evaluations_uses_pk_tiebreaker(conn):
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
    conn.execute(
        """
        UPDATE TorrentQualityEvaluation
        SET created_at = ?
        """,
        ("2026-05-31T00:00:00.000Z",),
    )

    rows = repo.list_recent_evaluations(limit=2)
    assert [row["info_hash"] for row in rows] == ["DEF456", "ABC123"]


@pytest.mark.parametrize("limit", [0, -1])
def test_list_recent_evaluations_rejects_non_positive_limit(conn, limit):
    repo = TorrentQualityRepo(conn)
    with pytest.raises(ValueError, match="limit must be positive"):
        repo.list_recent_evaluations(limit=limit)


# ---------------------------------------------------------------------------
# list_evidence_for_movie (ADR-024 IMP-08 Task 3)
# ---------------------------------------------------------------------------


@pytest.fixture
def conn_with_probe():
    """In-memory DB with all three quality tables + TorrentProbeCandidate."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MIGRATION.read_text(encoding="utf-8"))
    conn.executescript(_MIGRATION_PROBE.read_text(encoding="utf-8"))
    try:
        yield conn
    finally:
        conn.close()


def test_list_evidence_for_movie_joins_production_and_probe(conn_with_probe):
    conn = conn_with_probe
    repo = TorrentQualityRepo(conn)

    # Seed production evidence
    conn.execute(
        """
        INSERT INTO TorrentQualityEvidence
            (info_hash, probe_schema_version, target_role, metadata_status,
             total_size_bytes, main_video_size_bytes, main_video_ratio,
             video_file_count, subtitle_file_count, non_video_file_count,
             junk_size_bytes, junk_size_ratio, suspicious_file_count,
             features_json, reasons_json)
        VALUES (?, 'v1', 'production_download', 'metadata_received',
                5000000000, 4500000000, 0.9, 1, 1, 0, 0, 0.0, 0, '{}', '[]')
        """,
        ("prodhash",),
    )
    # Link production evidence to movie via TorrentQualityEvaluation
    conn.execute(
        """
        INSERT INTO TorrentQualityEvaluation
            (info_hash, movie_href, scoring_version, javdb_category, magnet_name)
        VALUES (?, '/v/abc', 'v1', 'subtitle', 'ABC-123-C')
        """,
        ("prodhash",),
    )

    # Seed probe evidence (metadata_received — should be included)
    conn.execute(
        """
        INSERT INTO TorrentQualityEvidence
            (info_hash, probe_schema_version, target_role, metadata_status,
             total_size_bytes, main_video_size_bytes, main_video_ratio,
             video_file_count, subtitle_file_count, non_video_file_count,
             junk_size_bytes, junk_size_ratio, suspicious_file_count,
             features_json, reasons_json)
        VALUES (?, 'v1', 'quality_probe', 'metadata_received',
                4800000000, 4300000000, 0.895, 1, 0, 0, 0, 0.0, 0, '{}', '[]')
        """,
        ("probehash",),
    )
    # Link probe evidence to movie via TorrentProbeCandidate
    conn.execute(
        """
        INSERT INTO TorrentProbeCandidate
            (info_hash, movie_href, javdb_category, magnet_uri, magnet_name, enqueued_at)
        VALUES (?, '/v/abc', 'subtitle', 'magnet:?xt=urn:btih:probehash', 'ABC-123 alt',
                '2026-06-20T00:00:00.000Z')
        """,
        ("probehash",),
    )

    # Seed a probe evidence row that is NOT metadata_received — must be excluded
    conn.execute(
        """
        INSERT INTO TorrentQualityEvidence
            (info_hash, probe_schema_version, target_role, metadata_status,
             features_json, reasons_json)
        VALUES (?, 'v1', 'quality_probe', 'pending_timeout', '{}', '[]')
        """,
        ("timeouthash",),
    )
    conn.execute(
        """
        INSERT INTO TorrentProbeCandidate
            (info_hash, movie_href, javdb_category, magnet_uri, magnet_name, enqueued_at)
        VALUES (?, '/v/abc', 'subtitle', 'magnet:?xt=urn:btih:timeouthash', 'ABC-123 timeout',
                '2026-06-20T00:00:01.000Z')
        """,
        ("timeouthash",),
    )
    conn.commit()

    rows = repo.list_evidence_for_movie("/v/abc")

    roles = {r["info_hash"]: r["target_role"] for r in rows}
    assert roles == {"prodhash": "production_download", "probehash": "quality_probe"}
    for r in rows:
        assert r["movie_href"] == "/v/abc"
        assert r["javdb_category"] == "subtitle"
        assert "main_video_ratio" in r and "junk_size_ratio" in r
    probe = next(r for r in rows if r["target_role"] == "quality_probe")
    assert probe["magnet_name"] == "ABC-123 alt"
    # pending_timeout probe row must be excluded
    assert "pending_timeout" not in {r.get("metadata_status") for r in rows}


def _probe_evidence(conn, info_hash, schema, movie_href):
    conn.execute(
        """
        INSERT INTO TorrentQualityEvidence
            (info_hash, probe_schema_version, target_role, metadata_status,
             total_size_bytes, main_video_size_bytes, main_video_ratio,
             video_file_count, subtitle_file_count, non_video_file_count,
             junk_size_bytes, junk_size_ratio, suspicious_file_count,
             features_json, reasons_json)
        VALUES (?, ?, 'quality_probe', 'metadata_received',
                1, 1, 0.9, 1, 0, 0, 0, 0.0, 0, '{}', '[]')
        """,
        (info_hash, schema),
    )
    conn.execute(
        """
        INSERT INTO TorrentProbeCandidate
            (info_hash, movie_href, javdb_category, magnet_uri, magnet_name, enqueued_at)
        VALUES (?, ?, 'subtitle', ?, ?, '2026-06-20T00:00:00.000Z')
        """,
        (info_hash, movie_href, f"magnet:?xt=urn:btih:{info_hash}", info_hash),
    )


def _production_evidence(conn, info_hash, schema, movie_href):
    conn.execute(
        """
        INSERT INTO TorrentQualityEvidence
            (info_hash, probe_schema_version, target_role, metadata_status,
             total_size_bytes, main_video_size_bytes, main_video_ratio,
             video_file_count, subtitle_file_count, non_video_file_count,
             junk_size_bytes, junk_size_ratio, suspicious_file_count,
             features_json, reasons_json)
        VALUES (?, ?, 'production_download', 'metadata_received',
                1, 1, 0.9, 1, 0, 0, 0, 0.0, 0, '{}', '[]')
        """,
        (info_hash, schema),
    )
    conn.execute(
        """
        INSERT INTO TorrentQualityEvaluation
            (info_hash, movie_href, scoring_version, javdb_category, magnet_name)
        VALUES (?, ?, 'v1', 'subtitle', ?)
        """,
        (info_hash, movie_href, info_hash),
    )


def test_list_evidence_for_movie_filters_by_probe_schema(conn_with_probe):
    conn = conn_with_probe
    repo = TorrentQualityRepo(conn)
    # probe branch: current + stale schema
    _probe_evidence(conn, "probe_cur", "adr024-probe-v1", "/v/x")
    _probe_evidence(conn, "probe_old", "old-v0", "/v/x")
    # production branch: current + stale schema (the filter applies here too)
    _production_evidence(conn, "prod_cur", "adr024-probe-v1", "/v/x")
    _production_evidence(conn, "prod_old", "old-v0", "/v/x")
    conn.commit()

    scoped = repo.list_evidence_for_movie("/v/x", probe_schema_version="adr024-probe-v1")
    # both old-schema rows excluded across BOTH branches
    assert {r["info_hash"] for r in scoped} == {"probe_cur", "prod_cur"}
    # unscoped (no filter) still returns all four
    assert {r["info_hash"] for r in repo.list_evidence_for_movie("/v/x")} == {
        "probe_cur", "probe_old", "prod_cur", "prod_old",
    }


def test_list_needs_review_filters_or_logic_and_validates_limit(conn):
    repo = TorrentQualityRepo(conn)

    def _ev(h, *, decision=None, would_replace=None):
        repo.upsert_evaluation(EvaluationRecord(
            info_hash=h, movie_href="/v/" + h, scoring_version="v1",
            decision=decision, would_replace_current_choice=would_replace,
        ))

    _ev("a", decision="needs_review")          # included via decision
    _ev("b", would_replace=True)               # included via replacement flag
    _ev("c", decision="accepted_shadow", would_replace=False)  # excluded

    rows = repo.list_needs_review(limit=10)
    assert {r["info_hash"] for r in rows} == {"a", "b"}

    with pytest.raises(ValueError):
        repo.list_needs_review(limit=0)
