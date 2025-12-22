"""
Unit tests for scripts/qb_file_filter.py functions.
"""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from types import ModuleType

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Create a proper mock config module with actual values
mock_config = ModuleType('config')
mock_config.QB_HOST = 'localhost'
mock_config.QB_PORT = '8080'
mock_config.QB_USERNAME = 'admin'
mock_config.QB_PASSWORD = 'adminadmin'
mock_config.REQUEST_TIMEOUT = 30
mock_config.LOG_LEVEL = 'INFO'
mock_config.PROXY_HTTP = None
mock_config.PROXY_HTTPS = None
mock_config.PROXY_MODULES = ['all']
mock_config.PROXY_MODE = 'single'
mock_config.PROXY_POOL = []
mock_config.PROXY_POOL_COOLDOWN_SECONDS = 691200
mock_config.PROXY_POOL_MAX_FAILURES = 3
mock_config.QB_FILE_FILTER_MIN_SIZE_MB = 50
mock_config.QB_FILE_FILTER_LOG_FILE = 'logs/qb_file_filter.log'
sys.modules['config'] = mock_config

# Import the functions to test
from scripts.qb_file_filter import (
    format_size,
    filter_small_files,
    get_recent_torrents,
    get_torrent_files,
    set_file_priority,
    get_torrent_properties,
    delete_local_file,
)


class TestFormatSize:
    """Test cases for format_size function."""

    def test_format_bytes(self):
        """Test formatting bytes."""
        assert format_size(500) == "500.00 B"
        assert format_size(0) == "0.00 B"

    def test_format_kilobytes(self):
        """Test formatting kilobytes."""
        assert format_size(1024) == "1.00 KB"
        assert format_size(1536) == "1.50 KB"

    def test_format_megabytes(self):
        """Test formatting megabytes."""
        assert format_size(1024 * 1024) == "1.00 MB"
        assert format_size(50 * 1024 * 1024) == "50.00 MB"

    def test_format_gigabytes(self):
        """Test formatting gigabytes."""
        assert format_size(1024 * 1024 * 1024) == "1.00 GB"
        assert format_size(2.5 * 1024 * 1024 * 1024) == "2.50 GB"

    def test_format_terabytes(self):
        """Test formatting terabytes."""
        assert format_size(1024 * 1024 * 1024 * 1024) == "1.00 TB"

    def test_format_negative_size(self):
        """Test formatting negative size."""
        result = format_size(-1024)
        assert "KB" in result


class TestGetRecentTorrents:
    """Test cases for get_recent_torrents function."""

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_recent_torrents_success(self, mock_proxies):
        """Test successful retrieval of recent torrents."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Use a very old timestamp so filtering includes these
        import time
        current_time = int(time.time())
        mock_response.json.return_value = [
            {'hash': 'abc123', 'name': 'Torrent 1', 'added_on': current_time - 3600},
            {'hash': 'def456', 'name': 'Torrent 2', 'added_on': current_time - 7200},
        ]
        mock_session.get.return_value = mock_response

        result = get_recent_torrents(mock_session, days=2, use_proxy=False)

        assert len(result) == 2

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_recent_torrents_filters_old(self, mock_proxies):
        """Test that old torrents are filtered out."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        import time
        current_time = int(time.time())
        # One recent, one old (10 days ago)
        mock_response.json.return_value = [
            {'hash': 'abc123', 'name': 'Recent', 'added_on': current_time - 3600},
            {'hash': 'def456', 'name': 'Old', 'added_on': current_time - (10 * 24 * 3600)},
        ]
        mock_session.get.return_value = mock_response

        result = get_recent_torrents(mock_session, days=2, use_proxy=False)

        # Only recent torrent should be returned
        assert len(result) == 1
        assert result[0]['name'] == 'Recent'

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_recent_torrents_api_failure(self, mock_proxies):
        """Test handling of API failure."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_session.get.return_value = mock_response

        result = get_recent_torrents(mock_session, days=2, use_proxy=False)

        assert result == []

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_recent_torrents_network_error(self, mock_proxies):
        """Test handling of network error."""
        import requests
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("Network error")

        result = get_recent_torrents(mock_session, days=2, use_proxy=False)

        assert result == []

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_recent_torrents_with_category(self, mock_proxies):
        """Test filtering by category."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        import time
        current_time = int(time.time())
        mock_response.json.return_value = [
            {'hash': 'abc123', 'name': 'Torrent 1', 'added_on': current_time - 3600},
        ]
        mock_session.get.return_value = mock_response

        result = get_recent_torrents(mock_session, days=2, category='JavDB', use_proxy=False)

        # Verify category parameter was passed
        mock_session.get.assert_called_once()
        call_args = mock_session.get.call_args
        assert call_args[1]['params']['category'] == 'JavDB'


class TestGetTorrentFiles:
    """Test cases for get_torrent_files function."""

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_torrent_files_success(self, mock_proxies):
        """Test successful retrieval of torrent files."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {'name': 'video.mp4', 'size': 1024 * 1024 * 100, 'priority': 1},
            {'name': 'sample.mp4', 'size': 1024 * 1024 * 5, 'priority': 1},
            {'name': 'info.nfo', 'size': 1024, 'priority': 1},
        ]
        mock_session.get.return_value = mock_response

        result = get_torrent_files(mock_session, 'abc123', use_proxy=False)

        assert len(result) == 3
        assert result[0]['name'] == 'video.mp4'

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_torrent_files_api_failure(self, mock_proxies):
        """Test handling of API failure."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_session.get.return_value = mock_response

        result = get_torrent_files(mock_session, 'abc123', use_proxy=False)

        assert result == []

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_torrent_files_network_error(self, mock_proxies):
        """Test handling of network error."""
        import requests
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("Network error")

        result = get_torrent_files(mock_session, 'abc123', use_proxy=False)

        assert result == []


class TestSetFilePriority:
    """Test cases for set_file_priority function."""

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_set_file_priority_success(self, mock_proxies):
        """Test successful priority setting."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response

        result = set_file_priority(mock_session, 'abc123', [0, 1, 2], priority=0, use_proxy=False)

        assert result is True
        # Verify correct data was sent
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert call_args[1]['data']['hash'] == 'abc123'
        assert call_args[1]['data']['id'] == '0|1|2'
        assert call_args[1]['data']['priority'] == 0

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_set_file_priority_failure(self, mock_proxies):
        """Test handling of API failure."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_session.post.return_value = mock_response

        result = set_file_priority(mock_session, 'abc123', [0, 1], priority=0, use_proxy=False)

        assert result is False

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_set_file_priority_network_error(self, mock_proxies):
        """Test handling of network error."""
        import requests
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_session.post.side_effect = requests.RequestException("Network error")

        result = set_file_priority(mock_session, 'abc123', [0], priority=0, use_proxy=False)

        assert result is False


class TestFilterSmallFiles:
    """Test cases for filter_small_files function."""

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_filter_small_files_basic(self, mock_set_priority, mock_get_files):
        """Test basic small file filtering."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1},  # 100MB - keep
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1},   # 5MB - filter
            {'name': 'info.nfo', 'size': 1024, 'priority': 1},                 # 1KB - filter
        ]
        mock_set_priority.return_value = True

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0}]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=False)

        assert stats['torrents_processed'] == 1
        assert stats['files_filtered'] == 2  # sample.mp4 and info.nfo
        assert stats['files_kept'] == 1      # video.mp4
        assert stats['torrents_with_filtered_files'] == 1

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_filter_small_files_dry_run(self, mock_set_priority, mock_get_files):
        """Test dry run mode doesn't make changes."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1},
        ]

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0}]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=True)

        # set_file_priority should not be called in dry run
        mock_set_priority.assert_not_called()
        assert stats['files_filtered'] == 1

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_filter_small_files_no_small_files(self, mock_set_priority, mock_get_files):
        """Test when no files are below threshold."""
        mock_get_files.return_value = [
            {'name': 'video1.mp4', 'size': 100 * 1024 * 1024, 'priority': 1},
            {'name': 'video2.mp4', 'size': 200 * 1024 * 1024, 'priority': 1},
        ]

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0}]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=False)

        assert stats['files_filtered'] == 0
        assert stats['files_kept'] == 2
        assert stats['torrents_with_filtered_files'] == 0
        mock_set_priority.assert_not_called()

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_filter_small_files_skips_already_filtered(self, mock_set_priority, mock_get_files):
        """Test that already filtered files (priority=0) are skipped."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 0},  # Already filtered
        ]

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0}]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=False)

        # sample.mp4 should not be counted as it's already filtered
        assert stats['files_filtered'] == 0
        assert stats['files_kept'] == 1

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_filter_small_files_multiple_torrents(self, mock_set_priority, mock_get_files):
        """Test filtering across multiple torrents."""
        def get_files_side_effect(session, torrent_hash, use_proxy=False):
            if torrent_hash == 'torrent1':
                return [
                    {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1},
                    {'name': 'small.txt', 'size': 1024, 'priority': 1},
                ]
            else:
                return [
                    {'name': 'video.mp4', 'size': 200 * 1024 * 1024, 'priority': 1},
                ]

        mock_get_files.side_effect = get_files_side_effect
        mock_set_priority.return_value = True

        mock_session = MagicMock()
        torrents = [
            {'hash': 'torrent1', 'name': 'Torrent 1', 'added_on': 0},
            {'hash': 'torrent2', 'name': 'Torrent 2', 'added_on': 0},
        ]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=False)

        assert stats['torrents_processed'] == 2
        assert stats['torrents_with_filtered_files'] == 1
        assert stats['files_filtered'] == 1
        assert stats['files_kept'] == 2

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_filter_small_files_error_handling(self, mock_set_priority, mock_get_files):
        """Test error handling when getting files fails."""
        mock_get_files.return_value = []  # Empty list simulates failure

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0}]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=False)

        assert stats['errors'] == 1
        assert stats['torrents_processed'] == 0

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_filter_small_files_set_priority_failure(self, mock_set_priority, mock_get_files):
        """Test handling when set_file_priority fails."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1},
            {'name': 'small.txt', 'size': 1024, 'priority': 1},
        ]
        mock_set_priority.return_value = False  # Simulate failure

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0}]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=False)

        assert stats['errors'] == 1

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_filter_small_files_size_saved_calculation(self, mock_set_priority, mock_get_files):
        """Test that size_saved is calculated correctly."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1},
            {'name': 'sample.mp4', 'size': 10 * 1024 * 1024, 'priority': 1},
            {'name': 'info.nfo', 'size': 2 * 1024, 'priority': 1},
        ]
        mock_set_priority.return_value = True

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0}]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=False)

        # sample.mp4 (10MB) + info.nfo (2KB) should be saved
        expected_saved = (10 * 1024 * 1024) + (2 * 1024)
        assert stats['size_saved'] == expected_saved


class TestGetTorrentProperties:
    """Test cases for get_torrent_properties function."""

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_torrent_properties_success(self, mock_proxies):
        """Test successful retrieval of torrent properties."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'save_path': '/downloads/torrents',
            'total_size': 1024 * 1024 * 100,
            'piece_size': 1024 * 1024,
        }
        mock_session.get.return_value = mock_response

        result = get_torrent_properties(mock_session, 'abc123', use_proxy=False)

        assert result['save_path'] == '/downloads/torrents'
        assert result['total_size'] == 1024 * 1024 * 100

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_torrent_properties_api_failure(self, mock_proxies):
        """Test handling of API failure."""
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_session.get.return_value = mock_response

        result = get_torrent_properties(mock_session, 'abc123', use_proxy=False)

        assert result == {}

    @patch('scripts.qb_file_filter.get_proxies_dict')
    def test_get_torrent_properties_network_error(self, mock_proxies):
        """Test handling of network error."""
        import requests
        mock_proxies.return_value = None

        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("Network error")

        result = get_torrent_properties(mock_session, 'abc123', use_proxy=False)

        assert result == {}


class TestDeleteLocalFile:
    """Test cases for delete_local_file function."""

    def test_delete_local_file_success(self, tmp_path):
        """Test successful file deletion."""
        # Create a temporary file
        test_file = tmp_path / "test_file.mp4"
        test_file.write_bytes(b"x" * 1024)  # 1KB file

        success, size = delete_local_file(str(test_file))

        assert success is True
        assert size == 1024
        assert not test_file.exists()

    def test_delete_local_file_not_found(self, tmp_path):
        """Test deletion of non-existent file."""
        non_existent = tmp_path / "non_existent.mp4"

        success, size = delete_local_file(str(non_existent))

        assert success is False
        assert size == 0

    def test_delete_local_file_in_subdirectory(self, tmp_path):
        """Test deletion of file in subdirectory."""
        subdir = tmp_path / "torrent_folder" / "subfolder"
        subdir.mkdir(parents=True)
        test_file = subdir / "sample.mp4"
        test_file.write_bytes(b"y" * 2048)

        success, size = delete_local_file(str(test_file))

        assert success is True
        assert size == 2048
        assert not test_file.exists()
        # Parent directories should still exist
        assert subdir.exists()

    @patch('os.path.exists')
    @patch('os.path.getsize')
    @patch('os.remove')
    def test_delete_local_file_permission_error(self, mock_remove, mock_getsize, mock_exists):
        """Test handling of permission error during deletion."""
        mock_exists.return_value = True
        mock_getsize.return_value = 1024
        mock_remove.side_effect = OSError("Permission denied")

        success, size = delete_local_file("/some/protected/file.mp4")

        assert success is False
        assert size == 0


class TestFilterSmallFilesWithDeletion:
    """Test cases for filter_small_files with delete_local_files_flag enabled."""

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_with_delete_downloaded_files(self, mock_delete, mock_set_priority, mock_get_files):
        """Test filtering and deleting files that have been downloaded."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1, 'progress': 1.0},  # Downloaded
            {'name': 'info.nfo', 'size': 1024, 'priority': 1, 'progress': 0.5},  # Partially downloaded
        ]
        mock_set_priority.return_value = True
        mock_delete.return_value = (True, 5 * 1024 * 1024)  # sample.mp4 deleted

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': '/downloads'}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=True
        )

        assert stats['files_filtered'] == 2
        assert stats['local_files_deleted'] == 2  # Both small files were downloaded
        # delete_local_file should be called for both small files with progress > 0
        assert mock_delete.call_count == 2

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_with_delete_not_downloaded_files(self, mock_delete, mock_set_priority, mock_get_files):
        """Test filtering files that haven't been downloaded yet (progress=0)."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 0},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1, 'progress': 0},  # Not downloaded
        ]
        mock_set_priority.return_value = True

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': '/downloads'}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=True
        )

        assert stats['files_filtered'] == 1
        assert stats['local_files_deleted'] == 0  # No files to delete (progress=0)
        mock_delete.assert_not_called()

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_with_delete_dry_run(self, mock_delete, mock_set_priority, mock_get_files):
        """Test dry run mode doesn't delete files."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
        ]

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': '/downloads'}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=True, delete_local_files_flag=True
        )

        # In dry run, neither set_file_priority nor delete_local_file should be called
        mock_set_priority.assert_not_called()
        mock_delete.assert_not_called()
        assert stats['files_filtered'] == 1

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_delete_flag_disabled(self, mock_delete, mock_set_priority, mock_get_files):
        """Test that files are not deleted when delete_local_files_flag is False."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
        ]
        mock_set_priority.return_value = True

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': '/downloads'}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=False
        )

        # Priority should be set, but files should not be deleted
        mock_set_priority.assert_called_once()
        mock_delete.assert_not_called()
        assert stats['local_files_deleted'] == 0

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_delete_tracks_size_correctly(self, mock_delete, mock_set_priority, mock_get_files):
        """Test that local_size_deleted is tracked correctly."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'sample.mp4', 'size': 10 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'info.nfo', 'size': 2048, 'priority': 1, 'progress': 1.0},
        ]
        mock_set_priority.return_value = True
        # Mock delete to return actual file sizes
        mock_delete.side_effect = [
            (True, 10 * 1024 * 1024),  # sample.mp4
            (True, 2048),              # info.nfo
        ]

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': '/downloads'}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=True
        )

        assert stats['local_files_deleted'] == 2
        assert stats['local_size_deleted'] == (10 * 1024 * 1024) + 2048

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_delete_partial_failure(self, mock_delete, mock_set_priority, mock_get_files):
        """Test handling when some files fail to delete."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'info.nfo', 'size': 1024, 'priority': 1, 'progress': 1.0},
        ]
        mock_set_priority.return_value = True
        # First file deletes successfully, second fails
        mock_delete.side_effect = [
            (True, 5 * 1024 * 1024),
            (False, 0),
        ]

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': '/downloads'}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=True
        )

        assert stats['local_files_deleted'] == 1
        assert stats['local_size_deleted'] == 5 * 1024 * 1024

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_delete_skipped_when_save_path_empty(self, mock_delete, mock_set_priority, mock_get_files):
        """Test that deletion is skipped when save_path is empty to prevent accidental deletion."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
        ]
        mock_set_priority.return_value = True

        mock_session = MagicMock()
        # Empty save_path should prevent deletion
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': ''}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=True
        )

        # Files should be filtered but not deleted due to empty save_path
        assert stats['files_filtered'] == 1
        assert stats['local_files_deleted'] == 0
        mock_delete.assert_not_called()

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_delete_skipped_when_save_path_relative(self, mock_delete, mock_set_priority, mock_get_files):
        """Test that deletion is skipped when save_path is not absolute to prevent accidental deletion."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
        ]
        mock_set_priority.return_value = True

        mock_session = MagicMock()
        # Relative save_path should prevent deletion
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': 'downloads/torrents'}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=True
        )

        # Files should be filtered but not deleted due to relative save_path
        assert stats['files_filtered'] == 1
        assert stats['local_files_deleted'] == 0
        mock_delete.assert_not_called()

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_filter_delete_skipped_when_priority_fails(self, mock_delete, mock_set_priority, mock_get_files):
        """Test that deletion is skipped when set_file_priority fails to prevent data loss."""
        mock_get_files.return_value = [
            {'name': 'video.mp4', 'size': 100 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
            {'name': 'sample.mp4', 'size': 5 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
        ]
        # Simulate priority setting failure
        mock_set_priority.return_value = False

        mock_session = MagicMock()
        torrents = [{'hash': 'abc123', 'name': 'Test Torrent', 'added_on': 0, 'save_path': '/downloads'}]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=True
        )

        # Files should not be deleted because priority setting failed
        # qBittorrent may still download these files, so deleting them would cause data loss
        assert stats['files_filtered'] == 1
        assert stats['local_files_deleted'] == 0
        assert stats['errors'] == 1
        mock_delete.assert_not_called()


class TestFilterIntegration:
    """Integration tests for the file filter workflow."""

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    def test_complete_filter_workflow(self, mock_set_priority, mock_get_files):
        """Test complete workflow with multiple scenarios."""
        # Setup: 3 torrents with different file scenarios
        def get_files_side_effect(session, torrent_hash, use_proxy=False):
            files_map = {
                'hash1': [
                    {'name': 'main.mp4', 'size': 500 * 1024 * 1024, 'priority': 1},
                    {'name': 'sample.mp4', 'size': 30 * 1024 * 1024, 'priority': 1},
                ],
                'hash2': [
                    {'name': 'main.mp4', 'size': 1024 * 1024 * 1024, 'priority': 1},
                ],
                'hash3': [
                    {'name': 'small1.nfo', 'size': 1024, 'priority': 1},
                    {'name': 'small2.txt', 'size': 2048, 'priority': 1},
                ],
            }
            return files_map.get(torrent_hash, [])

        mock_get_files.side_effect = get_files_side_effect
        mock_set_priority.return_value = True

        mock_session = MagicMock()
        torrents = [
            {'hash': 'hash1', 'name': 'Torrent 1', 'added_on': 0},
            {'hash': 'hash2', 'name': 'Torrent 2', 'added_on': 0},
            {'hash': 'hash3', 'name': 'Torrent 3', 'added_on': 0},
        ]

        stats = filter_small_files(mock_session, torrents, min_size_mb=50, dry_run=False)

        # Verify results
        assert stats['torrents_processed'] == 3
        assert stats['torrents_with_filtered_files'] == 2  # hash1 and hash3
        assert stats['files_filtered'] == 3  # sample.mp4, small1.nfo, small2.txt
        assert stats['files_kept'] == 2  # main.mp4 from hash1 and hash2
        assert len(stats['details']) == 2  # Only torrents with filtered files

    @patch('scripts.qb_file_filter.get_torrent_files')
    @patch('scripts.qb_file_filter.set_file_priority')
    @patch('scripts.qb_file_filter.delete_local_file')
    def test_complete_filter_workflow_with_deletion(self, mock_delete, mock_set_priority, mock_get_files):
        """Test complete workflow with file deletion enabled."""
        def get_files_side_effect(session, torrent_hash, use_proxy=False):
            files_map = {
                'hash1': [
                    {'name': 'main.mp4', 'size': 500 * 1024 * 1024, 'priority': 1, 'progress': 0.5},
                    {'name': 'sample.mp4', 'size': 30 * 1024 * 1024, 'priority': 1, 'progress': 1.0},
                ],
                'hash2': [
                    {'name': 'main.mp4', 'size': 1024 * 1024 * 1024, 'priority': 1, 'progress': 0.1},
                ],
                'hash3': [
                    {'name': 'small1.nfo', 'size': 1024, 'priority': 1, 'progress': 0},  # Not downloaded
                    {'name': 'small2.txt', 'size': 2048, 'priority': 1, 'progress': 1.0},  # Downloaded
                ],
            }
            return files_map.get(torrent_hash, [])

        mock_get_files.side_effect = get_files_side_effect
        mock_set_priority.return_value = True
        mock_delete.return_value = (True, 1024)  # Simplified return

        mock_session = MagicMock()
        torrents = [
            {'hash': 'hash1', 'name': 'Torrent 1', 'added_on': 0, 'save_path': '/downloads/1'},
            {'hash': 'hash2', 'name': 'Torrent 2', 'added_on': 0, 'save_path': '/downloads/2'},
            {'hash': 'hash3', 'name': 'Torrent 3', 'added_on': 0, 'save_path': '/downloads/3'},
        ]

        stats = filter_small_files(
            mock_session, torrents, min_size_mb=50, dry_run=False, delete_local_files_flag=True
        )

        # Verify filtering
        assert stats['torrents_processed'] == 3
        assert stats['files_filtered'] == 3  # sample.mp4, small1.nfo, small2.txt
        # Only files with progress > 0 should be deleted: sample.mp4 and small2.txt
        assert stats['local_files_deleted'] == 2
        assert mock_delete.call_count == 2

