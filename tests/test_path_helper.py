"""
Unit tests for utils/path_helper.py functions.
"""
import os
import sys
import pytest
import tempfile
import shutil
from datetime import datetime
from unittest.mock import patch

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.path_helper import (
    get_dated_subdir,
    get_dated_report_path,
    ensure_dated_dir,
    find_latest_report_in_dated_dirs
)


class TestGetDatedSubdir:
    """Test cases for get_dated_subdir function."""
    
    def test_returns_correct_format(self):
        """Test that the function returns correct YYYY/MM format."""
        date = datetime(2025, 12, 23)
        result = get_dated_subdir('Daily Report', date)
        
        # Should use os.path.join for cross-platform compatibility
        expected = os.path.join('Daily Report', '2025', '12')
        assert result == expected
    
    def test_pads_month_with_zero(self):
        """Test that months are zero-padded (e.g., 06 not 6)."""
        date = datetime(2025, 6, 15)
        result = get_dated_subdir('Ad Hoc', date)
        
        expected = os.path.join('Ad Hoc', '2025', '06')
        assert result == expected
    
    def test_uses_current_date_when_none(self):
        """Test that current date is used when date is None."""
        result = get_dated_subdir('Daily Report')
        
        now = datetime.now()
        expected = os.path.join('Daily Report', now.strftime('%Y'), now.strftime('%m'))
        assert result == expected
    
    def test_works_with_different_base_dirs(self):
        """Test with various base directory names."""
        date = datetime(2024, 1, 1)
        
        result1 = get_dated_subdir('Reports', date)
        result2 = get_dated_subdir('output/data', date)
        
        assert result1 == os.path.join('Reports', '2024', '01')
        assert result2 == os.path.join('output/data', '2024', '01')


class TestGetDatedReportPath:
    """Test cases for get_dated_report_path function."""
    
    def test_returns_full_path_with_filename(self):
        """Test that full path includes filename."""
        date = datetime(2025, 12, 23)
        result = get_dated_report_path('Daily Report', 'report.csv', date)
        
        expected = os.path.join('Daily Report', '2025', '12', 'report.csv')
        assert result == expected
    
    def test_works_with_complex_filenames(self):
        """Test with typical JavDB filenames."""
        date = datetime(2025, 12, 23)
        result = get_dated_report_path('Daily Report', 'Javdb_TodayTitle_20251223.csv', date)
        
        expected = os.path.join('Daily Report', '2025', '12', 'Javdb_TodayTitle_20251223.csv')
        assert result == expected
    
    def test_adhoc_path(self):
        """Test with Ad Hoc directory."""
        date = datetime(2025, 7, 4)
        result = get_dated_report_path('Ad Hoc', 'Javdb_AdHoc_actors_abc_20250704.csv', date)
        
        expected = os.path.join('Ad Hoc', '2025', '07', 'Javdb_AdHoc_actors_abc_20250704.csv')
        assert result == expected
    
    def test_uses_current_date_when_none(self):
        """Test that current date is used when date is None."""
        result = get_dated_report_path('Daily Report', 'test.csv')
        
        now = datetime.now()
        expected = os.path.join('Daily Report', now.strftime('%Y'), now.strftime('%m'), 'test.csv')
        assert result == expected


class TestEnsureDatedDir:
    """Test cases for ensure_dated_dir function."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)
    
    def test_creates_nested_directories(self, temp_dir):
        """Test that nested YYYY/MM directories are created."""
        base_dir = os.path.join(temp_dir, 'Daily Report')
        date = datetime(2025, 12, 23)
        
        result = ensure_dated_dir(base_dir, date)
        
        expected = os.path.join(base_dir, '2025', '12')
        assert result == expected
        assert os.path.exists(result)
        assert os.path.isdir(result)
    
    def test_returns_existing_directory(self, temp_dir):
        """Test that existing directories are not recreated."""
        base_dir = os.path.join(temp_dir, 'Ad Hoc')
        date = datetime(2025, 6, 15)
        
        # Create directory first
        expected_path = os.path.join(base_dir, '2025', '06')
        os.makedirs(expected_path)
        
        # Create a marker file
        marker = os.path.join(expected_path, 'marker.txt')
        with open(marker, 'w') as f:
            f.write('test')
        
        # Call ensure_dated_dir
        result = ensure_dated_dir(base_dir, date)
        
        assert result == expected_path
        # Marker file should still exist
        assert os.path.exists(marker)
    
    def test_creates_year_directory_if_missing(self, temp_dir):
        """Test that year directory is created even if base exists."""
        base_dir = os.path.join(temp_dir, 'Reports')
        os.makedirs(base_dir)  # Create only base
        
        date = datetime(2024, 3, 15)
        result = ensure_dated_dir(base_dir, date)
        
        expected = os.path.join(base_dir, '2024', '03')
        assert os.path.exists(result)


class TestFindLatestReportInDatedDirs:
    """Test cases for find_latest_report_in_dated_dirs function."""
    
    @pytest.fixture
    def temp_report_dir(self):
        """Create a temporary directory structure with test files."""
        temp_path = tempfile.mkdtemp()
        
        # Create some dated subdirectories with files
        dirs = [
            os.path.join(temp_path, '2025', '11'),
            os.path.join(temp_path, '2025', '12'),
            os.path.join(temp_path, '2024', '06'),
        ]
        
        for d in dirs:
            os.makedirs(d)
        
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)
    
    def test_finds_file_in_current_month(self, temp_report_dir):
        """Test finding file in current month's directory."""
        now = datetime.now()
        current_dir = os.path.join(temp_report_dir, now.strftime('%Y'), now.strftime('%m'))
        os.makedirs(current_dir, exist_ok=True)
        
        # Create a test file
        test_file = os.path.join(current_dir, 'Javdb_TodayTitle_test.csv')
        with open(test_file, 'w') as f:
            f.write('test')
        
        result = find_latest_report_in_dated_dirs(temp_report_dir, 'Javdb_TodayTitle_*.csv')
        
        assert result == test_file
    
    def test_finds_file_in_other_months(self, temp_report_dir):
        """Test finding file when not in current month."""
        # Create file in November 2025
        nov_file = os.path.join(temp_report_dir, '2025', '11', 'Javdb_TodayTitle_20251115.csv')
        with open(nov_file, 'w') as f:
            f.write('test')
        
        result = find_latest_report_in_dated_dirs(temp_report_dir, 'Javdb_TodayTitle_*.csv')
        
        assert result == nov_file
    
    def test_returns_most_recent_file(self, temp_report_dir):
        """Test that most recent file is returned when multiple exist."""
        import time
        
        # Create files in different directories
        file1 = os.path.join(temp_report_dir, '2024', '06', 'Javdb_TodayTitle_20240615.csv')
        with open(file1, 'w') as f:
            f.write('older')
        
        time.sleep(0.1)  # Ensure different modification times
        
        file2 = os.path.join(temp_report_dir, '2025', '11', 'Javdb_TodayTitle_20251115.csv')
        with open(file2, 'w') as f:
            f.write('newer')
        
        result = find_latest_report_in_dated_dirs(temp_report_dir, 'Javdb_TodayTitle_*.csv')
        
        assert result == file2
    
    def test_returns_none_when_not_found(self, temp_report_dir):
        """Test returning None when no matching files exist."""
        result = find_latest_report_in_dated_dirs(temp_report_dir, 'nonexistent_*.csv')
        
        assert result is None
    
    def test_returns_none_for_missing_directory(self):
        """Test returning None when base directory doesn't exist."""
        result = find_latest_report_in_dated_dirs('/nonexistent/path', '*.csv')
        
        assert result is None
    
    def test_finds_legacy_files_at_root(self, temp_report_dir):
        """Test backwards compatibility with files at root level."""
        # Create a file at root level (legacy location)
        legacy_file = os.path.join(temp_report_dir, 'Javdb_TodayTitle_20251223.csv')
        with open(legacy_file, 'w') as f:
            f.write('legacy')
        
        result = find_latest_report_in_dated_dirs(temp_report_dir, 'Javdb_TodayTitle_*.csv')
        
        assert result == legacy_file


class TestPathHelperIntegration:
    """Integration tests for path helper functions."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)
    
    def test_full_workflow(self, temp_dir):
        """Test complete workflow of creating and finding reports."""
        base_dir = os.path.join(temp_dir, 'Daily Report')
        date = datetime(2025, 12, 23)
        filename = 'Javdb_TodayTitle_20251223.csv'
        
        # Ensure directory exists
        dated_dir = ensure_dated_dir(base_dir, date)
        assert os.path.exists(dated_dir)
        
        # Get full path
        full_path = get_dated_report_path(base_dir, filename, date)
        expected = os.path.join(base_dir, '2025', '12', filename)
        assert full_path == expected
        
        # Create the file
        with open(full_path, 'w') as f:
            f.write('test data')
        
        # Find the file
        found = find_latest_report_in_dated_dirs(base_dir, 'Javdb_TodayTitle_*.csv')
        assert found == full_path

