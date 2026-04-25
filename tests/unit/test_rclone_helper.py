"""
Tests for utils/rclone_helper.py — shared rclone data structures and functions.
"""

import os
import sys
import base64
import json
import pytest
from unittest.mock import patch, MagicMock
from typing import List

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from utils.rclone_helper import (
    SensorCategory,
    SubtitleCategory,
    FolderInfo,
    DedupResult,
    FolderCache,
    SIZE_THRESHOLD_RATIO,
    DRY_RUN_MAX_YEARS,
    DRY_RUN_MAX_ACTORS_PER_YEAR,
    DRY_RUN_MAX_COMBINATIONS,
    parse_folder_name,
    parse_leaf_name,
    analyze_duplicates_for_code,
    group_folders_by_movie_code,
    format_size,
    _process_wuma_dedup,
    _process_subtitle_dedup,
    _apply_sensor_priority,
    check_rclone_installed,
    check_remote_exists,
    get_year_folders,
    get_actor_folders,
    get_movie_folders,
    get_configured_root_folder,
    strip_root_folder,
    prepend_root_folder,
    to_full_remote_path,
    setup_rclone_config_from_base64,
    rclone_purge,
)


# ============================================================================
# Test SensorCategory Class
# ============================================================================

class TestSensorCategory:
    def test_is_wuma_category_wuma(self):
        assert SensorCategory.is_wuma_category("无码") is True

    def test_is_wuma_category_wuma_liuchu(self):
        assert SensorCategory.is_wuma_category("无码流出") is True

    def test_is_wuma_category_wuma_pojie(self):
        assert SensorCategory.is_wuma_category("无码破解") is True

    def test_is_wuma_category_youma(self):
        assert SensorCategory.is_wuma_category("有码") is False

    def test_is_wuma_category_unknown(self):
        assert SensorCategory.is_wuma_category("unknown") is False

    def test_get_priority_wuma_liuchu(self):
        assert SensorCategory.get_priority("无码流出") == 3

    def test_get_priority_wuma(self):
        assert SensorCategory.get_priority("无码") == 2

    def test_get_priority_wuma_pojie(self):
        assert SensorCategory.get_priority("无码破解") == 1

    def test_get_priority_youma(self):
        assert SensorCategory.get_priority("有码") == 0

    def test_priority_order(self):
        liuchu = SensorCategory.get_priority("无码流出")
        wuma = SensorCategory.get_priority("无码")
        pojie = SensorCategory.get_priority("无码破解")
        assert liuchu > wuma > pojie


# ============================================================================
# Test parse_folder_name
# ============================================================================

class TestParseFolderName:
    def test_parse_standard_youma_zhongzi(self):
        result = parse_folder_name("ABC-123 [有码-中字]")
        assert result == ("ABC-123", "有码", "中字")

    def test_parse_standard_youma_wuzi(self):
        result = parse_folder_name("DEF-456 [有码-无字]")
        assert result == ("DEF-456", "有码", "无字")

    def test_parse_wuma_liuchu_zhongzi(self):
        result = parse_folder_name("GHI-789 [无码流出-中字]")
        assert result == ("GHI-789", "无码流出", "中字")

    def test_parse_wuma_pojie_wuzi(self):
        result = parse_folder_name("JKL-012 [无码破解-无字]")
        assert result == ("JKL-012", "无码破解", "无字")

    def test_parse_movie_code_with_spaces(self):
        result = parse_folder_name("ABC 123 [无码-中字]")
        assert result == ("ABC 123", "无码", "中字")

    def test_parse_with_extra_spaces(self):
        result = parse_folder_name("  ABC-123  [有码-中字]  ")
        assert result == ("ABC-123", "有码", "中字")

    def test_parse_invalid_no_brackets(self):
        assert parse_folder_name("ABC-123") is None

    def test_parse_invalid_wrong_format(self):
        assert parse_folder_name("ABC-123 [有码]") is None

    def test_parse_invalid_sensor_category(self):
        assert parse_folder_name("ABC-123 [未知-中字]") is None

    def test_parse_invalid_subtitle_category(self):
        assert parse_folder_name("ABC-123 [有码-未知]") is None


# ============================================================================
# Test parse_leaf_name (new code/leaf layout)
# ============================================================================

class TestParseLeafName:
    def test_youma_zhongzi(self):
        assert parse_leaf_name("有码-中字") == ("有码", "中字")

    def test_wuma_liuchu_zhongzi(self):
        assert parse_leaf_name("无码流出-中字") == ("无码流出", "中字")

    def test_wuma_pojie_wuzi(self):
        assert parse_leaf_name("无码破解-无字") == ("无码破解", "无字")

    def test_strips_brackets(self):
        # tolerate residual brackets in case of partial migrations
        assert parse_leaf_name("[有码-中字]") == ("有码", "中字")
        assert parse_leaf_name("(无码-无字)") == ("无码", "无字")

    def test_invalid_no_dash(self):
        assert parse_leaf_name("有码中字") is None

    def test_invalid_sensor(self):
        assert parse_leaf_name("未知-中字") is None

    def test_invalid_subtitle(self):
        assert parse_leaf_name("有码-未知") is None

    def test_empty(self):
        assert parse_leaf_name("") is None


# ============================================================================
# Test root-folder path helpers
# ============================================================================

class TestRootFolderHelpers:
    @patch('packages.python.javdb_platform.config_helper.cfg', autospec=True)
    def test_get_configured_root_folder_prefers_folder_path(self, mock_cfg):
        mock_cfg.side_effect = lambda k, d=None: {
            'RCLONE_FOLDER_PATH': 'gdrive:/剧集/不可以色色/JAV-Sync',
            'RCLONE_ROOT_FOLDER': '/should/not/use',
        }.get(k, d)
        assert get_configured_root_folder() == '剧集/不可以色色/JAV-Sync'

    @patch('packages.python.javdb_platform.config_helper.cfg', autospec=True)
    def test_get_configured_root_folder_falls_back_to_root_folder(self, mock_cfg):
        mock_cfg.side_effect = lambda k, d=None: {
            'RCLONE_FOLDER_PATH': None,
            'RCLONE_ROOT_FOLDER': '/剧集/不可以色色/JAV-Sync/',
        }.get(k, d)
        assert get_configured_root_folder() == '剧集/不可以色色/JAV-Sync'

    def test_strip_root_folder_idempotent_relative(self):
        assert strip_root_folder('2012/演员/010112-902/无码-无字', root='剧集/不可以色色/JAV-Sync') == '2012/演员/010112-902/无码-无字'

    def test_strip_root_folder_strips_drive_and_root(self):
        p = 'gdrive:剧集/不可以色色/JAV-Sync/2012/演员/010112-902/无码-无字'
        assert strip_root_folder(p, root='剧集/不可以色色/JAV-Sync') == '2012/演员/010112-902/无码-无字'

    def test_strip_root_folder_accepts_leading_slash(self):
        p = '/剧集/不可以色色/JAV-Sync/2012/演员/010112-902/无码-无字'
        assert strip_root_folder(p, root='剧集/不可以色色/JAV-Sync') == '2012/演员/010112-902/无码-无字'

    def test_prepend_root_folder_noop_when_already_prefixed(self):
        rel = '剧集/不可以色色/JAV-Sync/2012/演员/010112-902/无码-无字'
        assert prepend_root_folder(rel, root='剧集/不可以色色/JAV-Sync') == rel

    def test_prepend_root_folder_adds_root(self):
        rel = '2012/演员/010112-902/无码-无字'
        assert prepend_root_folder(rel, root='剧集/不可以色色/JAV-Sync') == '剧集/不可以色色/JAV-Sync/2012/演员/010112-902/无码-无字'

    def test_to_full_remote_path_builds_drive_root(self):
        rel = '2012/演员/010112-902/无码-无字'
        assert to_full_remote_path(rel, drive='gdrive', root='剧集/不可以色色/JAV-Sync') == 'gdrive:剧集/不可以色色/JAV-Sync/2012/演员/010112-902/无码-无字'

# ============================================================================
# Test format_size
# ============================================================================

class TestFormatSize:
    def test_format_bytes(self):
        assert format_size(500) == "500.00 B"

    def test_format_kilobytes(self):
        assert format_size(1024) == "1.00 KB"
        assert format_size(2048) == "2.00 KB"

    def test_format_megabytes(self):
        assert format_size(1024 * 1024) == "1.00 MB"
        assert format_size(5 * 1024 * 1024) == "5.00 MB"

    def test_format_gigabytes(self):
        assert format_size(1024 * 1024 * 1024) == "1.00 GB"

    def test_format_terabytes(self):
        assert format_size(1024 * 1024 * 1024 * 1024) == "1.00 TB"

    def test_format_zero(self):
        assert format_size(0) == "0.00 B"


# ============================================================================
# Helper
# ============================================================================

def create_folder_info(
    movie_code: str,
    sensor: str,
    subtitle: str,
    year: str = "2024",
    actor: str = "Test Actor",
    size: int = 1024 * 1024 * 100,
    file_count: int = 5,
) -> FolderInfo:
    folder_name = f"{movie_code} [{sensor}-{subtitle}]"
    return FolderInfo(
        full_path=f"gdrive:Movies/{year}/{actor}/{folder_name}",
        year=year, actor=actor,
        movie_code=movie_code,
        sensor_category=sensor,
        subtitle_category=subtitle,
        folder_name=folder_name,
        size=size, file_count=file_count,
    )


# ============================================================================
# Test Deduplication Logic
# ============================================================================

class TestAnalyzeDuplicates:
    def test_single_folder_no_dedup(self):
        folders = [create_folder_info("ABC-123", "有码", "中字")]
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert len(result.folders_to_keep) == 1
        assert len(result.folders_to_delete) == 0

    def test_youma_zhongzi_over_wuzi(self):
        folders = [
            create_folder_info("ABC-123", "有码", "中字"),
            create_folder_info("ABC-123", "有码", "无字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert len(result.folders_to_keep) == 1
        assert result.folders_to_keep[0].subtitle_category == "中字"
        assert len(result.folders_to_delete) == 1
        assert result.folders_to_delete[0][0].subtitle_category == "无字"

    def test_wuma_liuchu_over_wuma(self):
        folders = [
            create_folder_info("ABC-123", "无码流出", "无字"),
            create_folder_info("ABC-123", "无码", "无字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        keep_sensors = [f.sensor_category for f in result.folders_to_keep]
        delete_sensors = [f.sensor_category for f, _ in result.folders_to_delete]
        assert "无码流出" in keep_sensors
        assert "无码" in delete_sensors

    def test_wuma_over_wuma_pojie(self):
        folders = [
            create_folder_info("ABC-123", "无码", "无字"),
            create_folder_info("ABC-123", "无码破解", "无字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        keep_sensors = [f.sensor_category for f in result.folders_to_keep]
        delete_sensors = [f.sensor_category for f, _ in result.folders_to_delete]
        assert "无码" in keep_sensors
        assert "无码破解" in delete_sensors

    def test_wuma_zhongzi_beats_all_wuzi(self):
        folders = [
            create_folder_info("ABC-123", "无码流出", "无字"),
            create_folder_info("ABC-123", "无码", "中字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        keep_subtitles = [f.subtitle_category for f in result.folders_to_keep]
        delete_subtitles = [f.subtitle_category for f, _ in result.folders_to_delete]
        assert "中字" in keep_subtitles
        assert "无字" in delete_subtitles

    def test_youma_and_wuma_separate(self):
        folders = [
            create_folder_info("ABC-123", "有码", "中字"),
            create_folder_info("ABC-123", "有码", "无字"),
            create_folder_info("ABC-123", "无码", "中字"),
            create_folder_info("ABC-123", "无码", "无字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        keep_combos = [(f.sensor_category, f.subtitle_category) for f in result.folders_to_keep]
        assert ("有码", "中字") in keep_combos
        assert ("无码", "中字") in keep_combos
        assert len(result.folders_to_delete) == 2

    def test_complex_wuma_scenario(self):
        folders = [
            create_folder_info("ABC-123", "无码流出", "中字"),
            create_folder_info("ABC-123", "无码流出", "无字"),
            create_folder_info("ABC-123", "无码", "中字"),
            create_folder_info("ABC-123", "无码破解", "无字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert len(result.folders_to_keep) == 1
        kept = result.folders_to_keep[0]
        assert kept.sensor_category == "无码流出"
        assert kept.subtitle_category == "中字"
        assert len(result.folders_to_delete) == 3


# ============================================================================
# Test Size Exception
# ============================================================================

class TestSizeException:
    def test_size_exception_wuzi_larger_than_30_percent(self):
        zhongzi_size = 100 * 1024 * 1024
        wuzi_size = 150 * 1024 * 1024
        folders = [
            create_folder_info("ABC-123", "有码", "中字", size=zhongzi_size),
            create_folder_info("ABC-123", "有码", "无字", size=wuzi_size),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert len(result.folders_to_keep) == 2
        assert len(result.folders_to_delete) == 0

    def test_size_exception_wuzi_exactly_30_percent(self):
        zhongzi_size = 100 * 1024 * 1024
        wuzi_size = 130 * 1024 * 1024
        folders = [
            create_folder_info("ABC-123", "有码", "中字", size=zhongzi_size),
            create_folder_info("ABC-123", "有码", "无字", size=wuzi_size),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert len(result.folders_to_keep) == 1
        assert result.folders_to_keep[0].subtitle_category == "中字"
        assert len(result.folders_to_delete) == 1

    def test_size_exception_wuzi_smaller(self):
        zhongzi_size = 100 * 1024 * 1024
        wuzi_size = 80 * 1024 * 1024
        folders = [
            create_folder_info("ABC-123", "有码", "中字", size=zhongzi_size),
            create_folder_info("ABC-123", "有码", "无字", size=wuzi_size),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert len(result.folders_to_keep) == 1
        assert result.folders_to_keep[0].subtitle_category == "中字"
        assert len(result.folders_to_delete) == 1

    def test_size_exception_wuma_category(self):
        zhongzi_size = 100 * 1024 * 1024
        wuzi_size = 140 * 1024 * 1024
        folders = [
            create_folder_info("ABC-123", "无码流出", "中字", size=zhongzi_size),
            create_folder_info("ABC-123", "无码流出", "无字", size=wuzi_size),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert len(result.folders_to_keep) == 2
        keep_subtitles = [f.subtitle_category for f in result.folders_to_keep]
        assert "中字" in keep_subtitles
        assert "无字" in keep_subtitles

    def test_size_exception_zero_zhongzi_size(self):
        folders = [
            create_folder_info("ABC-123", "有码", "中字", size=0),
            create_folder_info("ABC-123", "有码", "无字", size=100 * 1024 * 1024),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        assert len(result.folders_to_keep) == 1
        assert result.folders_to_keep[0].subtitle_category == "中字"
        assert len(result.folders_to_delete) == 1

    def test_size_threshold_constant(self):
        assert SIZE_THRESHOLD_RATIO == 1.30


# ============================================================================
# Test Dry-Run Constants
# ============================================================================

class TestDryRunConstants:
    def test_dry_run_max_years(self):
        assert DRY_RUN_MAX_YEARS == 2

    def test_dry_run_max_actors_per_year(self):
        assert DRY_RUN_MAX_ACTORS_PER_YEAR == 50

    def test_dry_run_max_combinations(self):
        assert DRY_RUN_MAX_COMBINATIONS == 100

    def test_dry_run_limits_consistent(self):
        assert DRY_RUN_MAX_COMBINATIONS == DRY_RUN_MAX_YEARS * DRY_RUN_MAX_ACTORS_PER_YEAR


# ============================================================================
# Test FolderCache
# ============================================================================

class TestFolderCache:
    def test_cache_add_and_get(self):
        cache = FolderCache()
        try:
            folders = [
                create_folder_info("ABC-123", "有码", "中字"),
                create_folder_info("DEF-456", "无码", "无字"),
            ]
            cache.add_folders("2024", "Actor A", folders)
            retrieved = cache.get_folders("2024", "Actor A")
            assert len(retrieved) == 2
            assert retrieved[0].movie_code == "ABC-123"
            assert retrieved[1].movie_code == "DEF-456"
        finally:
            cache.clear()

    def test_cache_empty_get(self):
        cache = FolderCache()
        try:
            result = cache.get_folders("2024", "NonExistent")
            assert result == []
        finally:
            cache.clear()

    def test_cache_folder_count(self):
        cache = FolderCache()
        try:
            folders1 = [create_folder_info("ABC-123", "有码", "中字")]
            folders2 = [
                create_folder_info("DEF-456", "无码", "中字"),
                create_folder_info("GHI-789", "无码", "无字"),
            ]
            cache.add_folders("2024", "Actor A", folders1)
            cache.add_folders("2024", "Actor B", folders2)
            assert cache.folder_count == 3
        finally:
            cache.clear()

    def test_cache_context_manager(self):
        with FolderCache() as cache:
            folders = [create_folder_info("ABC-123", "有码", "中字")]
            cache.add_folders("2024", "Actor", folders)
            assert cache.folder_count == 1

    def test_cache_persists_json_payload(self):
        cache = FolderCache()
        try:
            folders = [create_folder_info("ABC-123", "有码", "中字")]
            cache.add_folders("2024", "Actor A", folders)
            cache_file = next(iter(cache._year_actor_index.values()))
            assert cache_file.endswith(".json")
            with open(cache_file, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            assert payload[0]["movie_code"] == "ABC-123"
            assert payload[0]["sensor_category"] == "有码"
        finally:
            cache.clear()


# ============================================================================
# Test group_folders_by_movie_code
# ============================================================================

class TestGroupFoldersByMovieCode:
    def test_empty_structure(self):
        assert group_folders_by_movie_code({}) == {}

    def test_single_folder(self):
        folder = create_folder_info("ABC-123", "有码", "中字")
        structure = {"2024": {"Actor": [folder]}}
        result = group_folders_by_movie_code(structure)
        assert "ABC-123" in result
        assert len(result["ABC-123"]) == 1

    def test_multiple_folders_same_code(self):
        folder1 = create_folder_info("ABC-123", "有码", "中字")
        folder2 = create_folder_info("ABC-123", "有码", "无字")
        structure = {"2024": {"Actor": [folder1, folder2]}}
        result = group_folders_by_movie_code(structure)
        assert len(result["ABC-123"]) == 2

    def test_different_codes_separate(self):
        folder1 = create_folder_info("ABC-123", "有码", "中字")
        folder2 = create_folder_info("DEF-456", "有码", "中字")
        structure = {"2024": {"Actor": [folder1, folder2]}}
        result = group_folders_by_movie_code(structure)
        assert "ABC-123" in result
        assert "DEF-456" in result
        assert len(result["ABC-123"]) == 1
        assert len(result["DEF-456"]) == 1


# ============================================================================
# Test Health Checks (Mocked)
# ============================================================================

class TestHealthChecks:
    @patch('utils.rclone_helper.subprocess.run')
    def test_check_rclone_installed_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rclone v1.60.0\n- os/version: darwin/arm64",
        )
        success, message = check_rclone_installed()
        assert success is True
        assert "rclone installed" in message

    @patch('utils.rclone_helper.subprocess.run')
    def test_check_rclone_installed_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        success, message = check_rclone_installed()
        assert success is False
        assert "not installed" in message

    @patch('utils.rclone_helper.subprocess.run')
    def test_check_remote_exists_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gdrive:\nmydrive:\n")
        success, message = check_remote_exists("gdrive")
        assert success is True
        assert "found" in message

    @patch('utils.rclone_helper.subprocess.run')
    def test_check_remote_exists_not_found(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="otherdrive:\n")
        success, message = check_remote_exists("gdrive")
        assert success is False
        assert "not found" in message


# ============================================================================
# Test Folder Structure Parsing (Mocked)
# ============================================================================

class TestFolderStructureParsing:
    @patch('utils.rclone_helper.subprocess.run')
    def test_get_year_folders(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="-1 2024-01-01 00:00:00 -1 2024\n-1 2024-01-01 00:00:00 -1 2025\n-1 2024-01-01 00:00:00 -1 未知\n",
        )
        years = get_year_folders("gdrive", "Movies")
        assert "2024" in years
        assert "2025" in years
        assert "未知" in years
        assert len(years) == 3

    @patch('utils.rclone_helper.subprocess.run')
    def test_get_actor_folders(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="-1 2024-01-01 00:00:00 -1 Actor One\n-1 2024-01-01 00:00:00 -1 Actor Two\n",
        )
        actors = get_actor_folders("gdrive", "Movies", "2024")
        assert "Actor One" in actors
        assert "Actor Two" in actors
        assert len(actors) == 2

    @patch('utils.rclone_helper.subprocess.run')
    def test_get_movie_folders(self, mock_run):
        # New layout (post rclone_group_jav.py):
        #   <root>/<year>/<actor>/<movie_code>/<sensor-subtitle>
        # ``get_movie_folders`` now uses ``lsjson -R --dirs-only`` and only
        # picks entries at exactly depth 2 (``code/leaf``).
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"Path": "ABC-123", "IsDir": True},
                {"Path": "ABC-123/有码-中字", "IsDir": True},
                {"Path": "DEF-456", "IsDir": True},
                {"Path": "DEF-456/无码-无字", "IsDir": True},
                {"Path": "Invalid Folder", "IsDir": True},
                {"Path": "Invalid Folder/garbage", "IsDir": True},
            ]),
        )
        folders = get_movie_folders("gdrive", "Movies", "2024", "Actor")
        by_code = {f.movie_code: f for f in folders}
        assert set(by_code) == {"ABC-123", "DEF-456"}
        assert by_code["ABC-123"].sensor_category == "有码"
        assert by_code["ABC-123"].subtitle_category == "中字"
        assert by_code["ABC-123"].folder_name == "有码-中字"
        assert by_code["ABC-123"].full_path.endswith("Movies/2024/Actor/ABC-123/有码-中字")
        assert by_code["DEF-456"].sensor_category == "无码"


# ============================================================================
# Test setup_rclone_config_from_base64
# ============================================================================

class TestSetupRcloneConfigFromBase64:
    def test_empty_config(self):
        assert setup_rclone_config_from_base64('') is False

    def test_valid_base64(self, tmp_path, monkeypatch):
        config_content = b'[gdrive]\ntype = drive\n'
        b64 = base64.b64encode(config_content).decode()
        monkeypatch.setattr(
            'utils.rclone_helper.os.path.expanduser',
            lambda path: path.replace('~', str(tmp_path)),
        )
        result = setup_rclone_config_from_base64(b64)
        assert result is True
        config_path = os.path.join(str(tmp_path), '.config', 'rclone', 'rclone.conf')
        with open(config_path, 'rb') as f:
            written = f.read()
        assert written == config_content

    def test_invalid_base64(self):
        assert setup_rclone_config_from_base64('not-valid-base64!!!') is False


# ============================================================================
# Test rclone_purge
# ============================================================================

class TestRclonePurge:
    def test_dry_run_always_succeeds(self):
        assert rclone_purge('gdrive:/some/path', dry_run=True) is True

    @patch('utils.rclone_helper.subprocess.run')
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert rclone_purge('gdrive:/path') is True
        mock_run.assert_called_once_with(
            ['rclone', 'purge', 'gdrive:/path'],
            capture_output=True, text=True, timeout=120,
        )

    @patch('utils.rclone_helper.subprocess.run')
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr='permission denied')
        assert rclone_purge('gdrive:/path') is False

    @patch('utils.rclone_helper.subprocess.run')
    def test_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='rclone', timeout=120)
        assert rclone_purge('gdrive:/path') is False

    @patch('utils.rclone_helper.subprocess.run')
    def test_exception(self, mock_run):
        mock_run.side_effect = OSError('rclone not found')
        assert rclone_purge('gdrive:/path') is False


# ============================================================================
# Integration-style Tests
# ============================================================================

class TestIntegration:
    def test_full_dedup_workflow(self):
        folders = [
            create_folder_info("ABC-123", "有码", "中字", "2024", "Actor A"),
            create_folder_info("ABC-123", "有码", "无字", "2024", "Actor A"),
            create_folder_info("DEF-456", "无码流出", "中字", "2025", "Actor B"),
            create_folder_info("DEF-456", "无码", "无字", "2025", "Actor B"),
            create_folder_info("DEF-456", "无码破解", "无字", "2025", "Actor B"),
            create_folder_info("GHI-789", "有码", "无字", "2024", "Actor C"),
        ]
        structure = {
            "2024": {
                "Actor A": [folders[0], folders[1]],
                "Actor C": [folders[5]],
            },
            "2025": {
                "Actor B": [folders[2], folders[3], folders[4]],
            },
        }
        code_map = group_folders_by_movie_code(structure)
        results = []
        for code, code_folders in code_map.items():
            result = analyze_duplicates_for_code(code, code_folders)
            if result.folders_to_delete:
                results.append(result)

        assert len(results) == 2

        abc_result = next(r for r in results if r.movie_code == "ABC-123")
        assert len(abc_result.folders_to_delete) == 1
        assert abc_result.folders_to_delete[0][0].subtitle_category == "无字"

        def_result = next(r for r in results if r.movie_code == "DEF-456")
        assert len(def_result.folders_to_delete) == 2
