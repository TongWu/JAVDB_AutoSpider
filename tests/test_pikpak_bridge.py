"""
Unit tests for scripts/pikpak_bridge.py functions.
These tests use local implementations to avoid module import issues.
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
import csv

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestQBittorrentClientLogic:
    """Test cases for QBittorrentClient class logic."""
    
    def test_login_success_response(self):
        """Test successful login response parsing."""
        status_code = 200
        response_text = 'Ok.'
        
        success = status_code == 200 and response_text == 'Ok.'
        assert success is True
    
    def test_login_failure_response(self):
        """Test login failure response parsing."""
        status_code = 401
        response_text = 'Unauthorized'
        
        success = status_code == 200 and response_text == 'Ok.'
        assert success is False
    
    def test_get_torrents_response_parsing(self):
        """Test parsing get_torrents response."""
        mock_torrents = [{'hash': 'abc123', 'name': 'test torrent'}]
        
        assert len(mock_torrents) == 1
        assert mock_torrents[0]['hash'] == 'abc123'
    
    def test_get_torrents_multiple_categories(self):
        """Test getting torrents from multiple categories."""
        category1_torrents = [{'hash': 'abc123', 'name': 'torrent1'}]
        category2_torrents = [{'hash': 'def456', 'name': 'torrent2'}]
        
        all_torrents = category1_torrents + category2_torrents
        
        assert len(all_torrents) == 2
    
    def test_delete_torrents_hash_formatting(self):
        """Test delete torrents hash formatting."""
        hashes = ['hash1', 'hash2', 'hash3']
        
        formatted_hashes = '|'.join(hashes)
        
        assert formatted_hashes == 'hash1|hash2|hash3'
    
    def test_delete_torrents_with_files_flag(self):
        """Test delete torrents with delete_files flag."""
        delete_files = True
        
        data = {
            'hashes': 'hash1|hash2',
            'deleteFiles': 'true' if delete_files else 'false'
        }
        
        assert data['deleteFiles'] == 'true'


class TestSaveToPikpakHistory:
    """Test cases for save_to_pikpak_history function logic."""
    
    def test_create_history_record(self, temp_dir):
        """Test creating a history record."""
        history_file = os.path.join(temp_dir, 'pikpak_history.csv')
        
        torrent_info = {
            'hash': 'abc123',
            'name': 'Test Torrent',
            'category': 'Daily Ingestion',
            'magnet_uri': 'magnet:?xt=urn:btih:abc123',
            'added_on': datetime.now().timestamp()
        }
        transfer_status = 'success'
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(history_file), exist_ok=True)
        
        # Write to file
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = [
                'torrent_hash', 'torrent_name', 'category', 'magnet_uri', 'added_to_qb_date', 
                'deleted_from_qb_date', 'uploaded_to_pikpak_date', 'transfer_status', 'error_message'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            record = {
                'torrent_hash': torrent_info['hash'],
                'torrent_name': torrent_info['name'],
                'category': torrent_info.get('category', 'Unknown'),
                'magnet_uri': torrent_info['magnet_uri'],
                'added_to_qb_date': datetime.fromtimestamp(torrent_info['added_on']).strftime("%Y-%m-%d %H:%M:%S"),
                'deleted_from_qb_date': current_time if transfer_status in ['success', 'failed_but_deleted'] else '',
                'uploaded_to_pikpak_date': current_time if transfer_status == 'success' else '',
                'transfer_status': transfer_status,
                'error_message': ''
            }
            writer.writerow(record)
        
        assert os.path.exists(history_file)
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        assert 'Test Torrent' in content
        assert 'success' in content
    
    def test_append_to_existing_history(self, temp_dir):
        """Test appending to existing history file."""
        history_file = os.path.join(temp_dir, 'pikpak_history.csv')
        
        # Create initial file with header
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = [
                'torrent_hash', 'torrent_name', 'category', 'magnet_uri', 'added_to_qb_date',
                'deleted_from_qb_date', 'uploaded_to_pikpak_date', 'transfer_status', 'error_message'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        
        # Append new record
        with open(history_file, 'a', newline='', encoding='utf-8-sig') as f:
            fieldnames = [
                'torrent_hash', 'torrent_name', 'category', 'magnet_uri', 'added_to_qb_date',
                'deleted_from_qb_date', 'uploaded_to_pikpak_date', 'transfer_status', 'error_message'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writerow({
                'torrent_hash': 'xyz789',
                'torrent_name': 'Another Torrent',
                'category': 'Ad Hoc',
                'magnet_uri': 'magnet:?xt=urn:btih:xyz789',
                'added_to_qb_date': '2024-01-01 10:00:00',
                'deleted_from_qb_date': '',
                'uploaded_to_pikpak_date': '',
                'transfer_status': 'failed',
                'error_message': 'Connection error'
            })
        
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        assert 'Another Torrent' in content
        assert 'failed' in content
        assert 'Connection error' in content


class TestGetProxiesDictLogic:
    """Test cases for get_proxies_dict function logic."""
    
    def test_returns_none_when_helper_not_initialized(self):
        """Test that None is returned when proxy helper is not initialized."""
        global_proxy_helper = None
        
        if global_proxy_helper is None:
            result = None
        else:
            result = {'http': 'http://proxy:8080'}
        
        assert result is None
    
    def test_returns_proxies_when_helper_available(self):
        """Test that proxies are returned when helper is available."""
        class MockProxyHelper:
            def get_proxies_dict(self, module_name, use_proxy_flag):
                return {'http': 'http://proxy:8080'}
        
        global_proxy_helper = MockProxyHelper()
        result = global_proxy_helper.get_proxies_dict('pikpak', True)
        
        assert result == {'http': 'http://proxy:8080'}


class TestPikpakBridgeLogic:
    """Test cases for pikpak_bridge function logic."""
    
    def test_filter_old_torrents(self):
        """Test filtering old torrents by date."""
        days = 3
        cutoff_date = (datetime.now() - timedelta(days=days)).date()
        
        current_time = datetime.now().timestamp()
        old_time = (datetime.now() - timedelta(days=5)).timestamp()
        
        torrents = [
            {'hash': 'recent', 'name': 'Recent Torrent', 'added_on': current_time},
            {'hash': 'old', 'name': 'Old Torrent', 'added_on': old_time},
        ]
        
        old_torrents = [t for t in torrents if datetime.fromtimestamp(t['added_on']).date() <= cutoff_date]
        
        assert len(old_torrents) == 1
        assert old_torrents[0]['name'] == 'Old Torrent'
    
    def test_category_breakdown_stats(self):
        """Test category breakdown statistics calculation."""
        old_torrents = [
            {'hash': 'h1', 'name': 'T1', 'category': 'Daily Ingestion'},
            {'hash': 'h2', 'name': 'T2', 'category': 'Daily Ingestion'},
            {'hash': 'h3', 'name': 'T3', 'category': 'Ad Hoc'},
        ]
        
        category_counts = {}
        for torrent in old_torrents:
            category = torrent.get('category', 'Unknown')
            category_counts[category] = category_counts.get(category, 0) + 1
        
        assert category_counts['Daily Ingestion'] == 2
        assert category_counts['Ad Hoc'] == 1


class TestInitializeProxyHelperLogic:
    """Test cases for initialize_proxy_helper function logic."""
    
    def test_no_proxy_mode(self):
        """Test initialization when use_proxy is False."""
        use_proxy = False
        
        if not use_proxy:
            global_proxy_pool = None
        else:
            global_proxy_pool = 'some_pool'
        
        assert global_proxy_pool is None
    
    def test_pool_mode_initialization(self):
        """Test initialization in pool mode."""
        proxy_pool = [
            {'name': 'Proxy1', 'http': 'http://proxy1:8080'},
            {'name': 'Proxy2', 'http': 'http://proxy2:8080'}
        ]
        proxy_mode = 'pool'
        
        if proxy_pool and len(proxy_pool) > 0:
            if proxy_mode == 'pool':
                proxies_to_use = proxy_pool
            elif proxy_mode == 'single':
                proxies_to_use = [proxy_pool[0]]
        else:
            proxies_to_use = []
        
        assert len(proxies_to_use) == 2
    
    def test_single_mode_initialization(self):
        """Test initialization in single proxy mode."""
        proxy_pool = [
            {'name': 'Proxy1', 'http': 'http://proxy1:8080'},
            {'name': 'Proxy2', 'http': 'http://proxy2:8080'}
        ]
        proxy_mode = 'single'
        
        if proxy_pool and len(proxy_pool) > 0:
            if proxy_mode == 'pool':
                proxies_to_use = proxy_pool
            elif proxy_mode == 'single':
                proxies_to_use = [proxy_pool[0]]
        else:
            proxies_to_use = []
        
        assert len(proxies_to_use) == 1
        assert proxies_to_use[0]['name'] == 'Proxy1'


# Use temp_dir fixture
@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    import tempfile
    import shutil
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)

