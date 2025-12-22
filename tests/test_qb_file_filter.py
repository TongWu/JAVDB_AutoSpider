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

