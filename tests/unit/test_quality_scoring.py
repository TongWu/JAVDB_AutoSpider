"""Tests for ADR-024 explainable shadow scoring (Phase 1)."""

from __future__ import annotations

from typing import Any

from javdb.quality.features import extract_file_features
from javdb.quality.scoring import SCORING_VERSION, score_torrent


def _features(files: list[dict[str, Any]]) -> dict[str, Any]:
    return extract_file_features(files)


def test_version_constant_is_stable():
    assert SCORING_VERSION == "adr024-shadow-v1"


def test_clean_subtitled_video_scores_high():
    feats = _features(
        [
            {"name": "ABC-123-C.mkv", "size": 5_000_000_000, "priority": 1},
            {"name": "ABC-123-C.srt", "size": 60_000, "priority": 1},
        ]
    )
    result = score_torrent(
        feats,
        {"javdb_category": "subtitle", "magnet_name": "ABC-123-C 中文字幕", "javdb_tags": ["中文字幕"]},
    )
    assert result["score"] >= 0.8
    assert "main_video_detected" in result["reasons"]
    assert "subtitle_file_present" in result["reasons"]
    assert result["category_consistent"] is True
    assert result["subtitle_evidence"] == "file_present"
    assert result["decision"] == "accepted_shadow"


def test_subtitle_category_without_subtitle_is_flagged():
    feats = _features([{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "subtitle", "magnet_name": "ABC-123", "javdb_tags": []},
    )
    assert "subtitle_file_missing" in result["reasons"]
    assert "category_mismatch" in result["reasons"]
    assert result["category_consistent"] is False
    assert result["subtitle_evidence"] == "absent"
    assert result["decision"] == "needs_review"


def test_multi_video_release_does_not_penalize_main_video_ratio():
    feats = _features(
        [
            {"name": "ABC-123-pt1.mkv", "size": 1_000_000_000, "priority": 1},
            {"name": "ABC-123-pt2.mkv", "size": 1_000_000_000, "priority": 1},
            {"name": "ABC-123-pt3.mkv", "size": 1_000_000_000, "priority": 1},
        ]
    )
    result = score_torrent(
        feats,
        {"javdb_category": "no_subtitle", "magnet_name": "ABC-123", "javdb_tags": []},
    )
    assert feats["video_file_count"] == 3
    assert "main_video_ratio_low" not in result["reasons"]
    assert result["decision"] == "accepted_shadow"


def test_resolution_claim_unsupported_is_flagged():
    feats = _features([{"name": "ABC-123-720p.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "no_subtitle", "magnet_name": "ABC-123 4K", "javdb_tags": []},
    )
    assert "resolution_claim_unsupported" in result["reasons"]
    assert result["resolution_consistent"] is False
    assert result["score"] > 0.4
    assert result["decision"] == "accepted_shadow"


def test_resolution_claim_without_file_marker_is_neutral():
    feats = _features([{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "no_subtitle", "magnet_name": "ABC-123 4K", "javdb_tags": []},
    )
    assert "resolution_claim_unsupported" not in result["reasons"]
    assert result["resolution_consistent"] is None
    assert result["decision"] == "accepted_shadow"


def test_resolution_claim_supported_is_accepted():
    feats = _features([{"name": "ABC-123-1080p.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "no_subtitle", "magnet_name": "ABC-123 1080p", "javdb_tags": []},
    )
    assert "resolution_claim_supported" in result["reasons"]
    assert result["resolution_consistent"] is True
    assert result["decision"] == "accepted_shadow"


def test_subtitle_category_with_name_hint_only_is_accepted():
    feats = _features([{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "subtitle", "magnet_name": "ABC-123-C", "javdb_tags": []},
    )
    assert "subtitle_name_hint" in result["reasons"]
    assert result["subtitle_evidence"] == "name_hint"
    assert result["inferred_category"] == "subtitle"
    assert result["category_consistent"] is True
    assert result["decision"] == "accepted_shadow"


def test_no_subtitle_category_with_subtitle_evidence_is_flagged():
    feats = _features(
        [
            {"name": "ABC-123.mkv", "size": 4_000_000_000, "priority": 1},
            {"name": "ABC-123.srt", "size": 60_000, "priority": 1},
        ]
    )
    result = score_torrent(
        feats,
        {"javdb_category": "no_subtitle", "magnet_name": "ABC-123", "javdb_tags": []},
    )
    assert "subtitle_file_present" in result["reasons"]
    assert "category_mismatch" in result["reasons"]
    assert result["category_consistent"] is False
    assert result["decision"] == "needs_review"


def test_ascii_name_hint_does_not_match_subject_substring():
    feats = _features([{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}])
    result = score_torrent(
        feats,
        {"javdb_category": "subtitle", "magnet_name": "ABC-123 subject", "javdb_tags": []},
    )
    assert "subtitle_name_hint" not in result["reasons"]
    assert "subtitle_file_missing" in result["reasons"]
    assert "category_mismatch" in result["reasons"]
    assert result["subtitle_evidence"] == "absent"
    assert result["category_consistent"] is False
    assert result["decision"] == "needs_review"


def test_inflated_junk_torrent_scores_low():
    feats = _features(
        [
            {"name": "movie.mp4", "size": 800_000_000, "priority": 1},
            {"name": "bonus.rar", "size": 4_000_000_000, "priority": 1},
            {"name": "广告.txt", "size": 5_000, "priority": 1},
        ]
    )
    result = score_torrent(
        feats,
        {"javdb_category": "no_subtitle", "magnet_name": "movie", "javdb_tags": []},
    )
    assert "junk_ratio_high" in result["reasons"]
    assert result["score"] < 0.4
    assert result["decision"] == "rejected_shadow"


def test_no_video_file_is_rejected():
    feats = _features([{"name": "readme.md", "size": 1_000, "priority": 1}])
    result = score_torrent(feats, {"javdb_category": "no_subtitle", "magnet_name": "x", "javdb_tags": []})
    assert "main_video_missing" in result["reasons"]
    assert result["score"] < 0.4
    assert result["decision"] == "rejected_shadow"
