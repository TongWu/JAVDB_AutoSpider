"""
Unit tests for scripts/qb_uploader.py functions.
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
mock_config.TORRENT_CATEGORY = 'JavDB'
mock_config.TORRENT_CATEGORY_ADHOC = 'Ad Hoc'
mock_config.TORRENT_SAVE_PATH = ''
mock_config.AUTO_START = True
mock_config.SKIP_CHECKING = False
mock_config.REQUEST_TIMEOUT = 30
mock_config.DELAY_BETWEEN_ADDITIONS = 1
mock_config.UPLOADER_LOG_FILE = 'logs/qb_uploader.log'
mock_config.REPORTS_DIR = 'reports'
mock_config.DAILY_REPORT_DIR = 'reports/DailyReport'
mock_config.AD_HOC_DIR = 'reports/AdHoc'
mock_config.LOG_LEVEL = 'INFO'
mock_config.PROXY_HTTP = None
mock_config.PROXY_HTTPS = None
mock_config.PROXY_MODULES = ['all']
mock_config.PROXY_MODE = 'single'
mock_config.PROXY_POOL = []
mock_config.PROXY_POOL_COOLDOWN_SECONDS = 691200
mock_config.PROXY_POOL_MAX_FAILURES = 3
mock_config.GIT_USERNAME = 'test'
mock_config.GIT_PASSWORD = ''
mock_config.GIT_REPO_URL = ''
mock_config.GIT_BRANCH = 'main'
sys.modules['config'] = mock_config

# Import the functions to test
from scripts.qb_uploader import (
    extract_hash_from_magnet,
    is_torrent_exists,
    get_existing_torrents
)


class TestExtractHashFromMagnet:
    """Test cases for extract_hash_from_magnet function."""
    
    def test_extract_hex_hash_40_chars(self):
        """Test extracting 40-character hex hash from magnet link."""
        magnet = "magnet:?xt=urn:btih:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2&dn=test"
        result = extract_hash_from_magnet(magnet)
        
        assert result == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    
    def test_extract_uppercase_hex_hash(self):
        """Test that uppercase hex hash is converted to lowercase."""
        magnet = "magnet:?xt=urn:btih:A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2&dn=test"
        result = extract_hash_from_magnet(magnet)
        
        assert result == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    
    def test_extract_base32_hash_32_chars(self):
        """Test extracting 32-character base32 hash and converting to hex."""
        # Base32 encoded hash (32 chars)
        # "JBSWY3DPEHPK3PXP" is base32 for "Hello!" padded
        # Using a valid 32-char base32 string
        magnet = "magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA2&dn=test"
        result = extract_hash_from_magnet(magnet)
        
        # Should be converted to hex (40 chars lowercase)
        assert result is not None
        assert len(result) == 40
        assert result == result.lower()
    
    def test_invalid_magnet_no_hash(self):
        """Test that invalid magnet link returns None."""
        magnet = "magnet:?dn=test&tr=http://tracker.example.com"
        result = extract_hash_from_magnet(magnet)
        
        assert result is None
    
    def test_empty_string(self):
        """Test that empty string returns None."""
        result = extract_hash_from_magnet("")
        
        assert result is None
    
    def test_complex_magnet_link(self):
        """Test extracting hash from complex magnet link with many parameters."""
        magnet = (
            "magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12"
            "&dn=Test%20Movie"
            "&tr=udp://tracker1.example.com:6969"
            "&tr=udp://tracker2.example.com:6969"
            "&xl=1234567890"
        )
        result = extract_hash_from_magnet(magnet)
        
        assert result == "abcdef1234567890abcdef1234567890abcdef12"
    
    def test_hash_at_end_of_magnet(self):
        """Test extracting hash when it appears at the end without trailing params."""
        magnet = "magnet:?dn=test&xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12"
        result = extract_hash_from_magnet(magnet)
        
        assert result == "abcdef1234567890abcdef1234567890abcdef12"


class TestIsTorrentExists:
    """Test cases for is_torrent_exists function."""
    
    def test_torrent_exists_in_set(self):
        """Test that existing torrent is detected."""
        existing_hashes = {
            "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
            "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
        }
        magnet = "magnet:?xt=urn:btih:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2&dn=test"
        
        result = is_torrent_exists(magnet, existing_hashes)
        
        assert result is True
    
    def test_torrent_not_in_set(self):
        """Test that non-existing torrent is not detected."""
        existing_hashes = {
            "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        }
        magnet = "magnet:?xt=urn:btih:c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4&dn=test"
        
        result = is_torrent_exists(magnet, existing_hashes)
        
        assert result is False
    
    def test_empty_existing_hashes(self):
        """Test with empty existing hashes set."""
        existing_hashes = set()
        magnet = "magnet:?xt=urn:btih:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2&dn=test"
        
        result = is_torrent_exists(magnet, existing_hashes)
        
        assert result is False
    
    def test_invalid_magnet_link(self):
        """Test with invalid magnet link."""
        existing_hashes = {"a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"}
        magnet = "not-a-valid-magnet-link"
        
        result = is_torrent_exists(magnet, existing_hashes)
        
        assert result is False
    
    def test_case_insensitive_matching(self):
        """Test that hash matching is case-insensitive."""
        # Existing hash in lowercase
        existing_hashes = {"a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"}
        # Magnet link with uppercase hash
        magnet = "magnet:?xt=urn:btih:A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2&dn=test"
        
        result = is_torrent_exists(magnet, existing_hashes)
        
        assert result is True


class TestGetExistingTorrents:
    """Test cases for get_existing_torrents function."""
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_get_existing_torrents_success(self, mock_proxies):
        """Test successful retrieval of existing torrents."""
        mock_proxies.return_value = None
        
        # Create mock session
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {'hash': 'abc123def456abc123def456abc123def456abc1', 'state': 'downloading'},
            {'hash': 'def456abc123def456abc123def456abc123def4', 'state': 'seeding'},
            {'hash': 'ghi789jkl012ghi789jkl012ghi789jkl012ghi7', 'state': 'pausedUP'},
        ]
        mock_session.get.return_value = mock_response
        
        result = get_existing_torrents(mock_session, use_proxy=False)
        
        assert len(result) == 3
        assert 'abc123def456abc123def456abc123def456abc1' in result
        assert 'def456abc123def456abc123def456abc123def4' in result
        assert 'ghi789jkl012ghi789jkl012ghi789jkl012ghi7' in result
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_exclude_error_state_torrents(self, mock_proxies):
        """Test that torrents in error state are excluded."""
        mock_proxies.return_value = None
        
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {'hash': 'abc123def456abc123def456abc123def456abc1', 'state': 'downloading'},
            {'hash': 'error123def456abc123def456abc123def456ab', 'state': 'error'},
            {'hash': 'missing123456abc123def456abc123def456abc', 'state': 'missingFiles'},
            {'hash': 'def456abc123def456abc123def456abc123def4', 'state': 'seeding'},
        ]
        mock_session.get.return_value = mock_response
        
        result = get_existing_torrents(mock_session, use_proxy=False)
        
        assert len(result) == 2
        assert 'abc123def456abc123def456abc123def456abc1' in result
        assert 'def456abc123def456abc123def456abc123def4' in result
        # Error state torrents should be excluded
        assert 'error123def456abc123def456abc123def456ab' not in result
        assert 'missing123456abc123def456abc123def456abc' not in result
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_api_failure_returns_empty_set(self, mock_proxies):
        """Test that API failure returns empty set."""
        mock_proxies.return_value = None
        
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_session.get.return_value = mock_response
        
        result = get_existing_torrents(mock_session, use_proxy=False)
        
        assert result == set()
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_network_error_returns_empty_set(self, mock_proxies):
        """Test that network error returns empty set."""
        import requests
        mock_proxies.return_value = None
        
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("Network error")
        
        result = get_existing_torrents(mock_session, use_proxy=False)
        
        assert result == set()
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_hashes_are_lowercase(self, mock_proxies):
        """Test that all returned hashes are lowercase."""
        mock_proxies.return_value = None
        
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {'hash': 'ABC123DEF456ABC123DEF456ABC123DEF456ABC1', 'state': 'downloading'},
            {'hash': 'DEF456ABC123def456abc123def456abc123def4', 'state': 'seeding'},
        ]
        mock_session.get.return_value = mock_response
        
        result = get_existing_torrents(mock_session, use_proxy=False)
        
        for h in result:
            assert h == h.lower(), f"Hash {h} is not lowercase"
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_include_various_valid_states(self, mock_proxies):
        """Test that various valid torrent states are included."""
        mock_proxies.return_value = None
        
        valid_states = [
            'downloading', 'seeding', 'pausedUP', 'pausedDL',
            'stalledUP', 'stalledDL', 'queuedUP', 'queuedDL',
            'checkingUP', 'checkingDL', 'checkingResumeData',
            'allocating', 'metaDL', 'forcedUP', 'forcedDL',
            'moving', 'uploading'
        ]
        
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {'hash': f'hash{i:039d}', 'state': state}
            for i, state in enumerate(valid_states)
        ]
        mock_session.get.return_value = mock_response
        
        result = get_existing_torrents(mock_session, use_proxy=False)
        
        assert len(result) == len(valid_states)


class TestDuplicateDetectionIntegration:
    """Integration tests for duplicate detection workflow."""
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_full_duplicate_detection_workflow(self, mock_proxies):
        """Test complete workflow of detecting duplicates."""
        mock_proxies.return_value = None
        
        # Simulate existing torrents in qBittorrent
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {'hash': 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2', 'state': 'seeding'},
            {'hash': 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3', 'state': 'downloading'},
        ]
        mock_session.get.return_value = mock_response
        
        # Get existing torrents
        existing_hashes = get_existing_torrents(mock_session, use_proxy=False)
        
        # Test magnets
        existing_magnet = "magnet:?xt=urn:btih:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2&dn=existing"
        new_magnet = "magnet:?xt=urn:btih:c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4&dn=new"
        
        # Check duplicates
        assert is_torrent_exists(existing_magnet, existing_hashes) is True
        assert is_torrent_exists(new_magnet, existing_hashes) is False
    
    def test_add_new_hash_to_existing_set(self):
        """Test adding new hash to existing set prevents re-detection."""
        existing_hashes = {"a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"}
        new_magnet = "magnet:?xt=urn:btih:c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4&dn=new"
        
        # Initially not exists
        assert is_torrent_exists(new_magnet, existing_hashes) is False
        
        # Simulate adding the torrent
        new_hash = extract_hash_from_magnet(new_magnet)
        existing_hashes.add(new_hash)
        
        # Now it should exist
        assert is_torrent_exists(new_magnet, existing_hashes) is True


class TestErrorHandlingExitCodes:
    """Test cases for error handling and exit codes in main()."""

    @patch('scripts.qb_uploader.test_qbittorrent_connection')
    @patch('scripts.qb_uploader.initialize_proxy_helper')
    @patch('scripts.qb_uploader.parse_arguments')
    def test_exit_on_connection_failure(self, mock_args, mock_init_proxy, mock_test_conn):
        """Test that script exits with code 1 when connection test fails."""
        mock_args.return_value = MagicMock(
            mode='daily',
            use_proxy=False,
            input_file=None,
            from_pipeline=False
        )
        mock_init_proxy.return_value = None
        mock_test_conn.return_value = False  # Connection fails
        
        from scripts.qb_uploader import main
        
        with pytest.raises(SystemExit) as exc_info:
            main()
        
        assert exc_info.value.code == 1

    @patch('scripts.qb_uploader.login_to_qbittorrent')
    @patch('scripts.qb_uploader.test_qbittorrent_connection')
    @patch('scripts.qb_uploader.initialize_proxy_helper')
    @patch('scripts.qb_uploader.parse_arguments')
    def test_exit_on_login_failure(self, mock_args, mock_init_proxy, mock_test_conn, mock_login):
        """Test that script exits with code 1 when login fails."""
        mock_args.return_value = MagicMock(
            mode='daily',
            use_proxy=False,
            input_file=None,
            from_pipeline=False
        )
        mock_init_proxy.return_value = None
        mock_test_conn.return_value = True
        mock_login.return_value = False  # Login fails
        
        from scripts.qb_uploader import main
        
        with pytest.raises(SystemExit) as exc_info:
            main()
        
        assert exc_info.value.code == 1

    @patch('scripts.qb_uploader.read_csv_file')
    @patch('scripts.qb_uploader.test_qbittorrent_connection')
    @patch('scripts.qb_uploader.initialize_proxy_helper')
    @patch('scripts.qb_uploader.parse_arguments')
    def test_exit_on_csv_file_not_found(self, mock_args, mock_init_proxy, mock_test_conn, mock_read_csv):
        """Test that script exits with code 1 when CSV file is not found."""
        mock_args.return_value = MagicMock(
            mode='adhoc',
            use_proxy=False,
            input_file=None,
            from_pipeline=False
        )
        mock_init_proxy.return_value = None
        mock_test_conn.return_value = True
        mock_read_csv.return_value = ([], False)  # File not found
        
        from scripts.qb_uploader import main
        
        with pytest.raises(SystemExit) as exc_info:
            main()
        
        assert exc_info.value.code == 1

    @patch('scripts.qb_uploader.read_csv_file')
    @patch('scripts.qb_uploader.test_qbittorrent_connection')
    @patch('scripts.qb_uploader.initialize_proxy_helper')
    @patch('scripts.qb_uploader.parse_arguments')
    def test_no_exit_when_csv_empty_but_exists(self, mock_args, mock_init_proxy, mock_test_conn, mock_read_csv):
        """Test that script does NOT exit with error when CSV file exists but is empty."""
        mock_args.return_value = MagicMock(
            mode='daily',
            use_proxy=False,
            input_file=None,
            from_pipeline=False
        )
        mock_init_proxy.return_value = None
        mock_test_conn.return_value = True
        mock_read_csv.return_value = ([], True)  # File exists but no torrents
        
        from scripts.qb_uploader import main
        
        # Should NOT raise SystemExit - just returns normally
        result = main()
        assert result is None


class TestQbUploaderAdvanced:
    """Advanced test cases for qb_uploader functions."""
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_login_to_qbittorrent_success(self, mock_proxies):
        """Test successful login to qBittorrent."""
        from scripts.qb_uploader import login_to_qbittorrent
        mock_proxies.return_value = None
        
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = 'Ok.'
        mock_session.post.return_value = mock_response
        
        result = login_to_qbittorrent(mock_session, use_proxy=False)
        
        assert result is True
    
    @patch('scripts.qb_uploader.get_proxies_dict')
    def test_login_to_qbittorrent_failure(self, mock_proxies):
        """Test failed login to qBittorrent."""
        from scripts.qb_uploader import login_to_qbittorrent
        mock_proxies.return_value = None
        
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = 'Fails.'
        mock_session.post.return_value = mock_response
        
        result = login_to_qbittorrent(mock_session, use_proxy=False)
        
        assert result is False


class TestFindCsvFileLogic:
    """Test cases for find_csv_file logic with dated subdirectories."""
    
    def test_find_csv_in_dated_subdirectory(self, temp_dir):
        """Test finding CSV file in dated subdirectory (YYYY/MM)."""
        import glob
        from datetime import datetime
        
        # Create dated subdirectory structure
        now = datetime.now()
        year = now.strftime('%Y')
        month = now.strftime('%m')
        dated_dir = os.path.join(temp_dir, 'Daily Report', year, month)
        os.makedirs(dated_dir, exist_ok=True)
        
        # Create a test CSV file in dated subdirectory
        today = now.strftime('%Y%m%d')
        csv_file = os.path.join(dated_dir, f'Javdb_TodayTitle_{today}.csv')
        with open(csv_file, 'w') as f:
            f.write('test')
        
        # Find the file using pattern that includes subdirectories
        pattern = os.path.join(temp_dir, 'Daily Report', '*', '*', f'Javdb_TodayTitle_{today}*.csv')
        found_files = glob.glob(pattern)
        
        assert len(found_files) == 1
        assert csv_file in found_files
    
    def test_find_csv_no_file_in_dated_dir(self, temp_dir):
        """Test finding CSV file when none exists in dated subdirectory."""
        import glob
        from datetime import datetime
        
        # Create empty dated subdirectory
        now = datetime.now()
        year = now.strftime('%Y')
        month = now.strftime('%m')
        dated_dir = os.path.join(temp_dir, 'Daily Report', year, month)
        os.makedirs(dated_dir, exist_ok=True)
        
        pattern = os.path.join(dated_dir, '*.csv')
        found_files = glob.glob(pattern)
        
        assert len(found_files) == 0
    
    def test_csv_path_format(self, temp_dir):
        """Test that CSV path follows YYYY/MM format."""
        from datetime import datetime
        
        now = datetime.now()
        year = now.strftime('%Y')
        month = now.strftime('%m')
        filename = f'Javdb_TodayTitle_{now.strftime("%Y%m%d")}.csv'
        
        expected_path = os.path.join(temp_dir, 'Daily Report', year, month, filename)
        
        # Verify path structure
        parts = expected_path.split(os.sep)
        assert 'Daily Report' in parts
        assert year in parts
        assert month in parts
        assert filename in parts


class TestReadCsvMagnets:
    """Test cases for reading magnets from CSV."""
    
    def test_read_csv_magnets(self, temp_dir):
        """Test reading magnets from CSV file."""
        import csv
        
        csv_file = os.path.join(temp_dir, 'test.csv')
        
        # Create a test CSV file
        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=['href', 'video_code', 'hacked_subtitle', 'subtitle', 'no_subtitle'])
            writer.writeheader()
            writer.writerow({
                'href': '/v/TEST-001',
                'video_code': 'TEST-001',
                'hacked_subtitle': 'magnet:?xt=urn:btih:abc123',
                'subtitle': '',
                'no_subtitle': ''
            })
        
        # Read the file
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        assert len(rows) == 1
        assert rows[0]['video_code'] == 'TEST-001'
        assert 'abc123' in rows[0]['hacked_subtitle']


class TestReadCsvFile:
    """Test cases for read_csv_file function return values."""
    
    def test_read_csv_file_not_found(self, temp_dir):
        """Test that read_csv_file returns ([], False) when file not found."""
        from scripts.qb_uploader import read_csv_file
        
        non_existent_file = os.path.join(temp_dir, 'nonexistent.csv')
        torrents, file_exists = read_csv_file(non_existent_file)
        
        assert torrents == []
        assert file_exists is False
    
    def test_read_csv_file_exists_with_data(self, temp_dir):
        """Test that read_csv_file returns (data, True) when file exists with data."""
        import csv
        from scripts.qb_uploader import read_csv_file
        
        csv_file = os.path.join(temp_dir, 'test_torrents.csv')
        
        # Create a test CSV file with proper columns
        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'page', 'href', 'video_code', 'hacked_subtitle', 
                'hacked_no_subtitle', 'subtitle', 'no_subtitle'
            ])
            writer.writeheader()
            writer.writerow({
                'page': '1',
                'href': '/v/TEST-001',
                'video_code': 'TEST-001',
                'hacked_subtitle': 'magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1',
                'hacked_no_subtitle': '',
                'subtitle': '',
                'no_subtitle': ''
            })
        
        torrents, file_exists = read_csv_file(csv_file)
        
        assert file_exists is True
        assert len(torrents) == 1
        assert torrents[0]['video_code'] == 'TEST-001'
    
    def test_read_csv_file_exists_but_empty(self, temp_dir):
        """Test that read_csv_file returns ([], True) when file exists but is empty."""
        import csv
        from scripts.qb_uploader import read_csv_file
        
        csv_file = os.path.join(temp_dir, 'empty_torrents.csv')
        
        # Create an empty CSV file with just headers
        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'page', 'href', 'video_code', 'hacked_subtitle', 
                'hacked_no_subtitle', 'subtitle', 'no_subtitle'
            ])
            writer.writeheader()
        
        torrents, file_exists = read_csv_file(csv_file)
        
        assert file_exists is True
        assert torrents == []


class TestFindLatestAdhocCsv:
    """Test cases for find_latest_adhoc_csv_today function."""
    
    def test_find_latest_adhoc_csv_no_files(self, temp_dir):
        """Test that function returns None when no adhoc CSV files exist."""
        from unittest.mock import patch
        from scripts.qb_uploader import find_latest_adhoc_csv_today
        
        with patch('scripts.qb_uploader.AD_HOC_DIR', temp_dir):
            result = find_latest_adhoc_csv_today()
            assert result is None
    
    def test_find_latest_adhoc_csv_with_custom_name(self, temp_dir):
        """Test that function finds a custom-named adhoc CSV file."""
        from unittest.mock import patch
        from datetime import datetime
        from scripts.qb_uploader import find_latest_adhoc_csv_today
        
        # Create dated directory structure
        current_date = datetime.now().strftime("%Y%m%d")
        year = datetime.now().strftime('%Y')
        month = datetime.now().strftime('%m')
        dated_dir = os.path.join(temp_dir, year, month)
        os.makedirs(dated_dir, exist_ok=True)
        
        # Create a custom-named adhoc CSV file
        custom_csv = os.path.join(dated_dir, f'Javdb_AdHoc_actors_ActorName_{current_date}.csv')
        with open(custom_csv, 'w') as f:
            f.write('test')
        
        with patch('scripts.qb_uploader.AD_HOC_DIR', temp_dir):
            result = find_latest_adhoc_csv_today()
            assert result is not None
            assert 'Javdb_AdHoc_actors_ActorName' in result
    
    def test_find_latest_adhoc_csv_returns_most_recent(self, temp_dir):
        """Test that function returns the most recently modified file."""
        import time
        from unittest.mock import patch
        from datetime import datetime
        from scripts.qb_uploader import find_latest_adhoc_csv_today
        
        # Create dated directory structure
        current_date = datetime.now().strftime("%Y%m%d")
        year = datetime.now().strftime('%Y')
        month = datetime.now().strftime('%m')
        dated_dir = os.path.join(temp_dir, year, month)
        os.makedirs(dated_dir, exist_ok=True)
        
        # Create first CSV file
        first_csv = os.path.join(dated_dir, f'Javdb_AdHoc_actors_FirstActor_{current_date}.csv')
        with open(first_csv, 'w') as f:
            f.write('first')
        
        time.sleep(0.1)  # Ensure different mtime
        
        # Create second CSV file (should be returned as most recent)
        second_csv = os.path.join(dated_dir, f'Javdb_AdHoc_makers_SecondMaker_{current_date}.csv')
        with open(second_csv, 'w') as f:
            f.write('second')
        
        with patch('scripts.qb_uploader.AD_HOC_DIR', temp_dir):
            result = find_latest_adhoc_csv_today()
            assert result is not None
            assert 'SecondMaker' in result


class TestGetCsvFilenameAdhocAutoDiscovery:
    """Test cases for get_csv_filename adhoc auto-discovery."""
    
    def test_get_csv_filename_adhoc_auto_discovers(self, temp_dir):
        """Test that get_csv_filename auto-discovers adhoc CSV in adhoc mode."""
        from unittest.mock import patch
        from datetime import datetime
        from scripts.qb_uploader import get_csv_filename
        
        # Create dated directory structure
        current_date = datetime.now().strftime("%Y%m%d")
        year = datetime.now().strftime('%Y')
        month = datetime.now().strftime('%m')
        dated_dir = os.path.join(temp_dir, year, month)
        os.makedirs(dated_dir, exist_ok=True)
        
        # Create a custom-named adhoc CSV file
        custom_csv = os.path.join(dated_dir, f'Javdb_AdHoc_video_codes_TEST_{current_date}.csv')
        with open(custom_csv, 'w') as f:
            f.write('test')
        
        with patch('scripts.qb_uploader.AD_HOC_DIR', temp_dir):
            result = get_csv_filename(mode='adhoc')
            assert result is not None
            assert 'video_codes_TEST' in result
    
    def test_get_csv_filename_adhoc_fallback_when_no_file(self, temp_dir):
        """Test that get_csv_filename falls back to default naming when no file found."""
        from unittest.mock import patch
        from datetime import datetime
        from scripts.qb_uploader import get_csv_filename
        
        current_date = datetime.now().strftime("%Y%m%d")
        
        with patch('scripts.qb_uploader.AD_HOC_DIR', temp_dir):
            result = get_csv_filename(mode='adhoc')
            # Should fall back to default naming
            assert 'Javdb_TodayTitle' in result or 'AdHoc' in result
    
    def test_get_csv_filename_daily_mode_unchanged(self, temp_dir):
        """Test that daily mode still works with standard naming."""
        from unittest.mock import patch
        from datetime import datetime
        from scripts.qb_uploader import get_csv_filename
        
        current_date = datetime.now().strftime("%Y%m%d")
        
        with patch('scripts.qb_uploader.DAILY_REPORT_DIR', temp_dir):
            result = get_csv_filename(mode='daily')
            assert f'Javdb_TodayTitle_{current_date}.csv' in result


class TestInitializeProxyHelper:
    """Test cases for initialize_proxy_helper function."""
    
    def test_initialize_no_proxy(self):
        """Test initialization without proxy."""
        from scripts.qb_uploader import initialize_proxy_helper
        
        result = initialize_proxy_helper(use_proxy=False)
        
        assert result is None

