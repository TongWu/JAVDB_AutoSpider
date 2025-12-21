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
mock_config.DAILY_REPORT_DIR = 'Daily Report'
mock_config.AD_HOC_DIR = 'Ad Hoc'
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

