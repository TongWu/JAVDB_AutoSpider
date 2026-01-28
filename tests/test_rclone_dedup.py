"""
Tests for the rclone_dedup script.

This module tests the deduplication logic, folder parsing, and related functions
without requiring actual rclone access.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from typing import List

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.rclone_dedup import (
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
    analyze_duplicates_for_code,
    group_folders_by_movie_code,
    format_size,
    _process_wuma_dedup,
    _process_subtitle_dedup,
    _apply_sensor_priority,
)


# ============================================================================
# Test SensorCategory Class
# ============================================================================

class TestSensorCategory:
    """Tests for SensorCategory class methods."""
    
    def test_is_wuma_category_wuma(self):
        """Test 无码 is recognized as wuma category."""
        assert SensorCategory.is_wuma_category("无码") is True
    
    def test_is_wuma_category_wuma_liuchu(self):
        """Test 无码流出 is recognized as wuma category."""
        assert SensorCategory.is_wuma_category("无码流出") is True
    
    def test_is_wuma_category_wuma_pojie(self):
        """Test 无码破解 is recognized as wuma category."""
        assert SensorCategory.is_wuma_category("无码破解") is True
    
    def test_is_wuma_category_youma(self):
        """Test 有码 is NOT recognized as wuma category."""
        assert SensorCategory.is_wuma_category("有码") is False
    
    def test_is_wuma_category_unknown(self):
        """Test unknown category is not recognized as wuma."""
        assert SensorCategory.is_wuma_category("unknown") is False
    
    def test_get_priority_wuma_liuchu(self):
        """Test 无码流出 has highest priority (3)."""
        assert SensorCategory.get_priority("无码流出") == 3
    
    def test_get_priority_wuma(self):
        """Test 无码 has medium priority (2)."""
        assert SensorCategory.get_priority("无码") == 2
    
    def test_get_priority_wuma_pojie(self):
        """Test 无码破解 has lowest priority (1)."""
        assert SensorCategory.get_priority("无码破解") == 1
    
    def test_get_priority_youma(self):
        """Test 有码 returns 0 priority (not in wuma family)."""
        assert SensorCategory.get_priority("有码") == 0
    
    def test_priority_order(self):
        """Test priority order: 无码流出 > 无码 > 无码破解."""
        liuchu = SensorCategory.get_priority("无码流出")
        wuma = SensorCategory.get_priority("无码")
        pojie = SensorCategory.get_priority("无码破解")
        
        assert liuchu > wuma > pojie


# ============================================================================
# Test parse_folder_name Function
# ============================================================================

class TestParseFolderName:
    """Tests for folder name parsing."""
    
    def test_parse_standard_youma_zhongzi(self):
        """Test parsing standard folder name with 有码-中字."""
        result = parse_folder_name("ABC-123 [有码-中字]")
        assert result == ("ABC-123", "有码", "中字")
    
    def test_parse_standard_youma_wuzi(self):
        """Test parsing standard folder name with 有码-无字."""
        result = parse_folder_name("DEF-456 [有码-无字]")
        assert result == ("DEF-456", "有码", "无字")
    
    def test_parse_wuma_liuchu_zhongzi(self):
        """Test parsing folder name with 无码流出-中字."""
        result = parse_folder_name("GHI-789 [无码流出-中字]")
        assert result == ("GHI-789", "无码流出", "中字")
    
    def test_parse_wuma_pojie_wuzi(self):
        """Test parsing folder name with 无码破解-无字."""
        result = parse_folder_name("JKL-012 [无码破解-无字]")
        assert result == ("JKL-012", "无码破解", "无字")
    
    def test_parse_movie_code_with_spaces(self):
        """Test parsing folder with movie code containing spaces."""
        result = parse_folder_name("ABC 123 [无码-中字]")
        assert result == ("ABC 123", "无码", "中字")
    
    def test_parse_with_extra_spaces(self):
        """Test parsing folder name with extra spaces."""
        result = parse_folder_name("  ABC-123  [有码-中字]  ")
        assert result == ("ABC-123", "有码", "中字")
    
    def test_parse_invalid_no_brackets(self):
        """Test parsing folder without brackets returns None."""
        result = parse_folder_name("ABC-123")
        assert result is None
    
    def test_parse_invalid_wrong_format(self):
        """Test parsing folder with wrong format returns None."""
        result = parse_folder_name("ABC-123 [有码]")
        assert result is None
    
    def test_parse_invalid_sensor_category(self):
        """Test parsing folder with invalid sensor category returns None."""
        result = parse_folder_name("ABC-123 [未知-中字]")
        assert result is None
    
    def test_parse_invalid_subtitle_category(self):
        """Test parsing folder with invalid subtitle category returns None."""
        result = parse_folder_name("ABC-123 [有码-未知]")
        assert result is None


# ============================================================================
# Test format_size Function
# ============================================================================

class TestFormatSize:
    """Tests for size formatting."""
    
    def test_format_bytes(self):
        """Test formatting bytes."""
        assert format_size(500) == "500.00 B"
    
    def test_format_kilobytes(self):
        """Test formatting kilobytes."""
        assert format_size(1024) == "1.00 KB"
        assert format_size(2048) == "2.00 KB"
    
    def test_format_megabytes(self):
        """Test formatting megabytes."""
        assert format_size(1024 * 1024) == "1.00 MB"
        assert format_size(5 * 1024 * 1024) == "5.00 MB"
    
    def test_format_gigabytes(self):
        """Test formatting gigabytes."""
        assert format_size(1024 * 1024 * 1024) == "1.00 GB"
    
    def test_format_terabytes(self):
        """Test formatting terabytes."""
        assert format_size(1024 * 1024 * 1024 * 1024) == "1.00 TB"
    
    def test_format_zero(self):
        """Test formatting zero bytes."""
        assert format_size(0) == "0.00 B"


# ============================================================================
# Test Deduplication Logic
# ============================================================================

def create_folder_info(
    movie_code: str,
    sensor: str,
    subtitle: str,
    year: str = "2024",
    actor: str = "Test Actor",
    size: int = 1024 * 1024 * 100,  # 100 MB default
    file_count: int = 5
) -> FolderInfo:
    """Helper to create FolderInfo for testing."""
    folder_name = f"{movie_code} [{sensor}-{subtitle}]"
    return FolderInfo(
        full_path=f"gdrive:Movies/{year}/{actor}/{folder_name}",
        year=year,
        actor=actor,
        movie_code=movie_code,
        sensor_category=sensor,
        subtitle_category=subtitle,
        folder_name=folder_name,
        size=size,
        file_count=file_count
    )


class TestAnalyzeDuplicates:
    """Tests for duplicate analysis logic."""
    
    def test_single_folder_no_dedup(self):
        """Test single folder returns no deletions."""
        folders = [create_folder_info("ABC-123", "有码", "中字")]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        assert len(result.folders_to_keep) == 1
        assert len(result.folders_to_delete) == 0
    
    def test_youma_zhongzi_over_wuzi(self):
        """Test 有码 with 中字 beats 无字."""
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
        """Test 无码流出 beats 无码 (both 无字)."""
        folders = [
            create_folder_info("ABC-123", "无码流出", "无字"),
            create_folder_info("ABC-123", "无码", "无字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # Both have same subtitle, so priority applies
        # After priority, only one kept for each subtitle type
        keep_sensors = [f.sensor_category for f in result.folders_to_keep]
        delete_sensors = [f.sensor_category for f, _ in result.folders_to_delete]
        
        assert "无码流出" in keep_sensors
        assert "无码" in delete_sensors
    
    def test_wuma_over_wuma_pojie(self):
        """Test 无码 beats 无码破解 (both 无字)."""
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
        """Test 无码 中字 beats all 无字 versions."""
        folders = [
            create_folder_info("ABC-123", "无码流出", "无字"),  # Higher priority but no subtitle
            create_folder_info("ABC-123", "无码", "中字"),      # Lower priority but has subtitle
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # 中字 should be kept, 无字 deleted
        keep_subtitles = [f.subtitle_category for f in result.folders_to_keep]
        delete_subtitles = [f.subtitle_category for f, _ in result.folders_to_delete]
        
        assert "中字" in keep_subtitles
        assert "无字" in delete_subtitles
    
    def test_youma_and_wuma_separate(self):
        """Test 有码 and 无码 are processed separately."""
        folders = [
            create_folder_info("ABC-123", "有码", "中字"),
            create_folder_info("ABC-123", "有码", "无字"),
            create_folder_info("ABC-123", "无码", "中字"),
            create_folder_info("ABC-123", "无码", "无字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # Should keep: 有码-中字, 无码-中字
        # Should delete: 有码-无字, 无码-无字
        keep_combos = [(f.sensor_category, f.subtitle_category) for f in result.folders_to_keep]
        
        assert ("有码", "中字") in keep_combos
        assert ("无码", "中字") in keep_combos
        assert len(result.folders_to_delete) == 2
    
    def test_complex_wuma_scenario(self):
        """Test complex scenario with multiple 无码 variants."""
        folders = [
            create_folder_info("ABC-123", "无码流出", "中字"),
            create_folder_info("ABC-123", "无码流出", "无字"),
            create_folder_info("ABC-123", "无码", "中字"),
            create_folder_info("ABC-123", "无码破解", "无字"),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # 中字 group: 无码流出 wins over 无码
        # 无字 group: 无码流出 wins over 无码破解
        # Then: 中字 wins over 无字
        # Final: keep 无码流出-中字, delete all others
        
        assert len(result.folders_to_keep) == 1
        kept = result.folders_to_keep[0]
        assert kept.sensor_category == "无码流出"
        assert kept.subtitle_category == "中字"
        assert len(result.folders_to_delete) == 3


class TestSizeException:
    """Tests for the 30% size exception rule."""
    
    def test_size_exception_wuzi_larger_than_30_percent(self):
        """Test that 无字 is kept when 30%+ larger than 中字."""
        # 中字: 100 MB, 无字: 150 MB (50% larger)
        zhongzi_size = 100 * 1024 * 1024  # 100 MB
        wuzi_size = 150 * 1024 * 1024     # 150 MB (50% larger)
        
        folders = [
            create_folder_info("ABC-123", "有码", "中字", size=zhongzi_size),
            create_folder_info("ABC-123", "有码", "无字", size=wuzi_size),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # Both should be kept because 无字 is 50% larger (exceeds 30% threshold)
        assert len(result.folders_to_keep) == 2
        assert len(result.folders_to_delete) == 0
    
    def test_size_exception_wuzi_exactly_30_percent(self):
        """Test that 无字 is deleted when exactly at 30% threshold."""
        # 中字: 100 MB, 无字: 130 MB (exactly 30% larger)
        zhongzi_size = 100 * 1024 * 1024  # 100 MB
        wuzi_size = 130 * 1024 * 1024     # 130 MB (exactly 30%)
        
        folders = [
            create_folder_info("ABC-123", "有码", "中字", size=zhongzi_size),
            create_folder_info("ABC-123", "有码", "无字", size=wuzi_size),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # 无字 is exactly at threshold (not greater), so should be deleted
        assert len(result.folders_to_keep) == 1
        assert result.folders_to_keep[0].subtitle_category == "中字"
        assert len(result.folders_to_delete) == 1
    
    def test_size_exception_wuzi_smaller(self):
        """Test that 无字 is deleted when smaller than 中字."""
        # 中字: 100 MB, 无字: 80 MB (smaller)
        zhongzi_size = 100 * 1024 * 1024  # 100 MB
        wuzi_size = 80 * 1024 * 1024      # 80 MB
        
        folders = [
            create_folder_info("ABC-123", "有码", "中字", size=zhongzi_size),
            create_folder_info("ABC-123", "有码", "无字", size=wuzi_size),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # 无字 is smaller, so should be deleted
        assert len(result.folders_to_keep) == 1
        assert result.folders_to_keep[0].subtitle_category == "中字"
        assert len(result.folders_to_delete) == 1
    
    def test_size_exception_wuma_category(self):
        """Test size exception works for 无码 category too."""
        # 中字: 100 MB, 无字: 140 MB (40% larger)
        zhongzi_size = 100 * 1024 * 1024
        wuzi_size = 140 * 1024 * 1024
        
        folders = [
            create_folder_info("ABC-123", "无码流出", "中字", size=zhongzi_size),
            create_folder_info("ABC-123", "无码流出", "无字", size=wuzi_size),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # Both should be kept (无字 is 40% larger)
        assert len(result.folders_to_keep) == 2
        keep_subtitles = [f.subtitle_category for f in result.folders_to_keep]
        assert "中字" in keep_subtitles
        assert "无字" in keep_subtitles
    
    def test_size_exception_zero_zhongzi_size(self):
        """Test behavior when 中字 size is 0 (stats not retrieved)."""
        folders = [
            create_folder_info("ABC-123", "有码", "中字", size=0),
            create_folder_info("ABC-123", "有码", "无字", size=100 * 1024 * 1024),
        ]
        result = analyze_duplicates_for_code("ABC-123", folders)
        
        # When 中字 size is 0, can't compare, so follow normal rule (delete 无字)
        assert len(result.folders_to_keep) == 1
        assert result.folders_to_keep[0].subtitle_category == "中字"
        assert len(result.folders_to_delete) == 1
    
    def test_size_threshold_constant(self):
        """Test that SIZE_THRESHOLD_RATIO is set to 1.30 (30%)."""
        assert SIZE_THRESHOLD_RATIO == 1.30


class TestDryRunConstants:
    """Tests for dry-run limit constants."""
    
    def test_dry_run_max_years(self):
        """Test DRY_RUN_MAX_YEARS is set to 2."""
        assert DRY_RUN_MAX_YEARS == 2
    
    def test_dry_run_max_actors_per_year(self):
        """Test DRY_RUN_MAX_ACTORS_PER_YEAR is set to 50."""
        assert DRY_RUN_MAX_ACTORS_PER_YEAR == 50
    
    def test_dry_run_max_combinations(self):
        """Test DRY_RUN_MAX_COMBINATIONS is set to 100."""
        assert DRY_RUN_MAX_COMBINATIONS == 100
    
    def test_dry_run_limits_consistent(self):
        """Test that max combinations equals max years * max actors per year."""
        assert DRY_RUN_MAX_COMBINATIONS == DRY_RUN_MAX_YEARS * DRY_RUN_MAX_ACTORS_PER_YEAR


class TestFolderCache:
    """Tests for the FolderCache class."""
    
    def test_cache_add_and_get(self):
        """Test adding and retrieving folders from cache."""
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
        """Test getting from cache returns empty list when not found."""
        cache = FolderCache()
        try:
            result = cache.get_folders("2024", "NonExistent")
            assert result == []
        finally:
            cache.clear()
    
    def test_cache_folder_count(self):
        """Test folder count is tracked correctly."""
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
        """Test cache can be used as context manager."""
        with FolderCache() as cache:
            folders = [create_folder_info("ABC-123", "有码", "中字")]
            cache.add_folders("2024", "Actor", folders)
            assert cache.folder_count == 1
        # Cache should be cleared after exit


class TestGroupFoldersByMovieCode:
    """Tests for folder grouping."""
    
    def test_empty_structure(self):
        """Test empty structure returns empty dict."""
        result = group_folders_by_movie_code({})
        assert result == {}
    
    def test_single_folder(self):
        """Test single folder groups correctly."""
        folder = create_folder_info("ABC-123", "有码", "中字")
        structure = {"2024": {"Actor": [folder]}}
        
        result = group_folders_by_movie_code(structure)
        
        assert "ABC-123" in result
        assert len(result["ABC-123"]) == 1
    
    def test_multiple_folders_same_code(self):
        """Test multiple folders with same code group together."""
        folder1 = create_folder_info("ABC-123", "有码", "中字")
        folder2 = create_folder_info("ABC-123", "有码", "无字")
        structure = {"2024": {"Actor": [folder1, folder2]}}
        
        result = group_folders_by_movie_code(structure)
        
        assert len(result["ABC-123"]) == 2
    
    def test_different_codes_separate(self):
        """Test different movie codes are separated."""
        folder1 = create_folder_info("ABC-123", "有码", "中字")
        folder2 = create_folder_info("DEF-456", "有码", "中字")
        structure = {"2024": {"Actor": [folder1, folder2]}}
        
        result = group_folders_by_movie_code(structure)
        
        assert "ABC-123" in result
        assert "DEF-456" in result
        assert len(result["ABC-123"]) == 1
        assert len(result["DEF-456"]) == 1


# ============================================================================
# Test Health Check Functions (Mocked)
# ============================================================================

class TestHealthChecks:
    """Tests for health check functions with mocked subprocess."""
    
    @patch('scripts.rclone_dedup.subprocess.run')
    def test_check_rclone_installed_success(self, mock_run):
        """Test rclone installation check passes."""
        from scripts.rclone_dedup import check_rclone_installed
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="rclone v1.60.0\n- os/version: darwin/arm64"
        )
        
        success, message = check_rclone_installed()
        assert success is True
        assert "rclone installed" in message
    
    @patch('scripts.rclone_dedup.subprocess.run')
    def test_check_rclone_installed_not_found(self, mock_run):
        """Test rclone installation check fails when not installed."""
        from scripts.rclone_dedup import check_rclone_installed
        
        mock_run.side_effect = FileNotFoundError()
        
        success, message = check_rclone_installed()
        assert success is False
        assert "not installed" in message
    
    @patch('scripts.rclone_dedup.subprocess.run')
    def test_check_remote_exists_success(self, mock_run):
        """Test remote exists check passes."""
        from scripts.rclone_dedup import check_remote_exists
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gdrive:\nmydrive:\n"
        )
        
        success, message = check_remote_exists("gdrive")
        assert success is True
        assert "found" in message
    
    @patch('scripts.rclone_dedup.subprocess.run')
    def test_check_remote_exists_not_found(self, mock_run):
        """Test remote exists check fails when not found."""
        from scripts.rclone_dedup import check_remote_exists
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="otherdrive:\n"
        )
        
        success, message = check_remote_exists("gdrive")
        assert success is False
        assert "not found" in message


# ============================================================================
# Test Folder Structure Parsing (Mocked)
# ============================================================================

class TestFolderStructureParsing:
    """Tests for folder structure parsing with mocked rclone."""
    
    @patch('scripts.rclone_dedup.subprocess.run')
    def test_get_year_folders(self, mock_run):
        """Test year folder parsing from rclone lsd output."""
        from scripts.rclone_dedup import get_year_folders
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="-1 2024-01-01 00:00:00 -1 2024\n-1 2024-01-01 00:00:00 -1 2025\n-1 2024-01-01 00:00:00 -1 未知\n"
        )
        
        years = get_year_folders("gdrive", "Movies")
        
        assert "2024" in years
        assert "2025" in years
        assert "未知" in years
        assert len(years) == 3
    
    @patch('scripts.rclone_dedup.subprocess.run')
    def test_get_actor_folders(self, mock_run):
        """Test actor folder parsing from rclone lsd output."""
        from scripts.rclone_dedup import get_actor_folders
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="-1 2024-01-01 00:00:00 -1 Actor One\n-1 2024-01-01 00:00:00 -1 Actor Two\n"
        )
        
        actors = get_actor_folders("gdrive", "Movies", "2024")
        
        assert "Actor One" in actors
        assert "Actor Two" in actors
        assert len(actors) == 2
    
    @patch('scripts.rclone_dedup.subprocess.run')
    def test_get_movie_folders(self, mock_run):
        """Test movie folder parsing from rclone lsd output."""
        from scripts.rclone_dedup import get_movie_folders
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "-1 2024-01-01 00:00:00 -1 ABC-123 [有码-中字]\n"
                "-1 2024-01-01 00:00:00 -1 DEF-456 [无码-无字]\n"
                "-1 2024-01-01 00:00:00 -1 Invalid Folder\n"  # Should be skipped
            )
        )
        
        folders = get_movie_folders("gdrive", "Movies", "2024", "Actor")
        
        assert len(folders) == 2
        assert folders[0].movie_code == "ABC-123"
        assert folders[0].sensor_category == "有码"
        assert folders[1].movie_code == "DEF-456"
        assert folders[1].sensor_category == "无码"


# ============================================================================
# Integration-style Tests
# ============================================================================

class TestIntegration:
    """Integration-style tests for full dedup workflow."""
    
    def test_full_dedup_workflow(self):
        """Test complete deduplication workflow with mock data."""
        # Create mock folder structure
        folders = [
            # Same movie with multiple versions
            create_folder_info("ABC-123", "有码", "中字", "2024", "Actor A"),
            create_folder_info("ABC-123", "有码", "无字", "2024", "Actor A"),
            # Another movie in different year
            create_folder_info("DEF-456", "无码流出", "中字", "2025", "Actor B"),
            create_folder_info("DEF-456", "无码", "无字", "2025", "Actor B"),
            create_folder_info("DEF-456", "无码破解", "无字", "2025", "Actor B"),
            # Single movie (no duplicates)
            create_folder_info("GHI-789", "有码", "无字", "2024", "Actor C"),
        ]
        
        # Create structure
        structure = {
            "2024": {
                "Actor A": [folders[0], folders[1]],
                "Actor C": [folders[5]],
            },
            "2025": {
                "Actor B": [folders[2], folders[3], folders[4]],
            },
        }
        
        # Group and analyze
        code_map = group_folders_by_movie_code(structure)
        
        results = []
        for code, code_folders in code_map.items():
            result = analyze_duplicates_for_code(code, code_folders)
            if result.folders_to_delete:
                results.append(result)
        
        # Verify results
        # ABC-123: should delete 无字 version
        # DEF-456: should keep 无码流出-中字, delete others (including all 无字)
        # GHI-789: no duplicates, no deletion
        
        assert len(results) == 2  # Two movies have deletions
        
        abc_result = next(r for r in results if r.movie_code == "ABC-123")
        assert len(abc_result.folders_to_delete) == 1
        assert abc_result.folders_to_delete[0][0].subtitle_category == "无字"
        
        def_result = next(r for r in results if r.movie_code == "DEF-456")
        # Should delete: 无码-无字, 无码破解-无字 (both 无字 since 中字 exists)
        assert len(def_result.folders_to_delete) == 2
