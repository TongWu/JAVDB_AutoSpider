"""Tests for ADR-024 file-list feature extraction (Phase 1)."""

from __future__ import annotations

from javdb.quality.features import PROBE_SCHEMA_VERSION, extract_file_features


def test_version_constant_is_stable():
    assert PROBE_SCHEMA_VERSION == "adr024-probe-v1"


def test_single_clean_video():
    files = [{"name": "ABC-123.mp4", "size": 4_000_000_000, "priority": 1}]
    f = extract_file_features(files)
    assert f["total_size_bytes"] == 4_000_000_000
    assert f["main_video_size_bytes"] == 4_000_000_000
    assert f["main_video_ratio"] == 1.0
    assert f["video_file_count"] == 1
    assert f["subtitle_file_count"] == 0
    assert f["non_video_file_count"] == 0
    assert f["junk_size_bytes"] == 0
    assert f["junk_size_ratio"] == 0.0
    assert f["suspicious_file_count"] == 0


def test_video_with_subtitle_and_junk():
    files = [
        {"name": "ABC-123/ABC-123.mkv", "size": 5_000_000_000, "priority": 1},
        {"name": "ABC-123/ABC-123.srt", "size": 50_000, "priority": 1},
        {"name": "ABC-123/读我.txt", "size": 1_000, "priority": 1},
        {"name": "ABC-123/广告.jpg", "size": 200_000, "priority": 1},
    ]
    f = extract_file_features(files)
    assert f["video_file_count"] == 1
    assert f["subtitle_file_count"] == 1
    assert f["non_video_file_count"] == 3  # srt + txt + jpg are non-video
    assert f["junk_size_bytes"] == 201_000  # txt + jpg
    assert f["main_video_size_bytes"] == 5_000_000_000
    assert 0.0 < f["junk_size_ratio"] < 0.001
    assert f["suspicious_file_count"] == 2


def test_inflated_torrent_with_ad_archive():
    files = [
        {"name": "movie.mp4", "size": 1_000_000_000, "priority": 1},
        {"name": "【最新地址】.txt", "size": 2_000, "priority": 1},
        {"name": "bonus.rar", "size": 3_000_000_000, "priority": 1},
    ]
    f = extract_file_features(files)
    # rar archive + txt are junk; the .rar dominates size
    assert f["junk_size_bytes"] == 3_000_002_000
    assert f["junk_size_ratio"] > 0.7
    assert f["main_video_size_bytes"] == 1_000_000_000
    assert f["main_video_ratio"] < 0.3


def test_empty_file_list():
    f = extract_file_features([])
    assert f["total_size_bytes"] == 0
    assert f["main_video_ratio"] == 0.0
    assert f["video_file_count"] == 0
