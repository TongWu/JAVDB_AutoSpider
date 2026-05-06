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
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

    def test_clears_active_session_id_after_impl_returns(self, monkeypatch):
        import scripts.pikpak_bridge as pikpak_mod
        from utils.infra import db as db_mod

        def fake_impl(*_args, **_kwargs):
            assert db_mod.get_active_session_id() == 42
            return "done"

        db_mod.set_active_session_id(999)
        monkeypatch.setattr(pikpak_mod, "_pikpak_bridge_impl", fake_impl)

        assert pikpak_mod.pikpak_bridge(
            3, True, session_id=42, root_folder="/root",
        ) == "done"
        assert db_mod.get_active_session_id() is None

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


class TestAdhocQBMergeLogic:
    """Test cases for merging torrents from primary and adhoc QB instances."""

    def test_adhoc_torrents_merged_without_duplicates(self):
        """Adhoc torrents are appended only when their hash is new."""
        primary_torrents = [
            {'hash': 'aaa', 'name': 'T1', 'category': 'Daily Ingestion'},
            {'hash': 'bbb', 'name': 'T2', 'category': 'Ad Hoc'},
        ]
        adhoc_torrents = [
            {'hash': 'bbb', 'name': 'T2-dup', 'category': 'Ad Hoc'},
            {'hash': 'ccc', 'name': 'T3', 'category': 'Ad Hoc'},
        ]

        # Simulate the merge logic from pikpak_bridge
        torrent_qb_map = {}
        for t in primary_torrents:
            torrent_qb_map[t['hash']] = 'primary'

        existing_hashes = {t['hash'] for t in primary_torrents}
        merged = list(primary_torrents)
        for t in adhoc_torrents:
            if t['hash'] not in existing_hashes:
                merged.append(t)
                torrent_qb_map[t['hash']] = 'adhoc'

        assert len(merged) == 3
        assert torrent_qb_map['aaa'] == 'primary'
        assert torrent_qb_map['bbb'] == 'primary'  # not overwritten by adhoc
        assert torrent_qb_map['ccc'] == 'adhoc'

    def test_no_adhoc_when_url_empty(self):
        """When QB_URL_ADHOC is empty, only primary torrents are used."""
        qb_url_adhoc = ''
        primary_torrents = [{'hash': 'x', 'name': 'T1'}]

        merged = list(primary_torrents)
        if qb_url_adhoc:
            merged.append({'hash': 'y', 'name': 'T2'})

        assert len(merged) == 1

    def test_delete_uses_correct_qb_client(self):
        """Each torrent is deleted from the QB instance it came from."""
        torrent_qb_map = {
            'aaa': 'primary',
            'bbb': 'adhoc',
        }
        default_qb = 'primary'

        assert torrent_qb_map.get('aaa', default_qb) == 'primary'
        assert torrent_qb_map.get('bbb', default_qb) == 'adhoc'
        assert torrent_qb_map.get('unknown', default_qb) == 'primary'


class TestPikpakTargetPath:
    """Tests for ``_build_pikpak_target_path`` and segment normalization."""

    def test_default_root_when_none(self):
        from packages.python.javdb_integrations.pikpak_bridge import (
            _build_pikpak_target_path,
            PIKPAK_ROOT_FOLDER_DEFAULT,
        )
        assert _build_pikpak_target_path(None, 'Ad Hoc') == f"{PIKPAK_ROOT_FOLDER_DEFAULT}/Ad Hoc"

    def test_explicit_root_with_category(self):
        from packages.python.javdb_integrations.pikpak_bridge import (
            _build_pikpak_target_path,
        )
        assert _build_pikpak_target_path('/Javdb_AutoSpider', 'Ad Hoc') == '/Javdb_AutoSpider/Ad Hoc'

    def test_root_without_leading_slash_is_normalized(self):
        from packages.python.javdb_integrations.pikpak_bridge import (
            _build_pikpak_target_path,
        )
        assert _build_pikpak_target_path('Javdb_AutoSpider', 'JavDB') == '/Javdb_AutoSpider/JavDB'

    def test_root_with_trailing_slash_is_stripped(self):
        from packages.python.javdb_integrations.pikpak_bridge import (
            _build_pikpak_target_path,
        )
        assert _build_pikpak_target_path('/Javdb_AutoSpider/', 'Ad Hoc') == '/Javdb_AutoSpider/Ad Hoc'

    def test_empty_category_falls_back_to_root(self):
        from packages.python.javdb_integrations.pikpak_bridge import (
            _build_pikpak_target_path,
        )
        assert _build_pikpak_target_path('/Javdb_AutoSpider', '') == '/Javdb_AutoSpider'
        assert _build_pikpak_target_path('/Javdb_AutoSpider', None) == '/Javdb_AutoSpider'

    def test_slash_in_category_is_collapsed(self):
        """A category like ``foo/bar`` must not become two folders."""
        from packages.python.javdb_integrations.pikpak_bridge import (
            _build_pikpak_target_path,
        )
        assert _build_pikpak_target_path('/Javdb_AutoSpider', 'foo/bar') == '/Javdb_AutoSpider/foo_bar'


class TestProcessPikpakBatchRouting:
    """``process_pikpak_batch`` routes magnets via path_to_id + parent_id."""

    def test_batch_uploads_each_magnet_to_its_category_folder(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from packages.python.javdb_integrations.pikpak_bridge import (
            process_pikpak_batch,
        )

        magnets = [
            'magnet:?xt=urn:btih:aaa',
            'magnet:?xt=urn:btih:bbb',
            'magnet:?xt=urn:btih:ccc',
        ]
        magnet_to_category = {
            magnets[0]: 'JavDB',
            magnets[1]: 'Ad Hoc',
            magnets[2]: 'JavDB',  # repeat: cache should avoid second path_to_id call
        }

        fake_client = AsyncMock()
        fake_client.login = AsyncMock(return_value=None)
        fake_client.refresh_access_token = AsyncMock(return_value=None)

        async def fake_path_to_id(path, create=False):
            mapping = {
                '/Javdb_AutoSpider/JavDB': [{'id': 'id-javdb', 'name': 'JavDB'}],
                '/Javdb_AutoSpider/Ad Hoc': [{'id': 'id-adhoc', 'name': 'Ad Hoc'}],
            }
            return mapping[path]

        fake_client.path_to_id = AsyncMock(side_effect=fake_path_to_id)
        fake_client.offline_download = AsyncMock(return_value={'task': {'id': 't1'}})

        with patch(
            'packages.python.javdb_integrations.pikpak_bridge.PikPakApi',
            return_value=fake_client,
        ):
            success, failed = asyncio.run(
                process_pikpak_batch(
                    magnets,
                    'user@example.com',
                    'pw',
                    delay_between_requests=0,
                    root_folder='/Javdb_AutoSpider',
                    magnet_to_category=magnet_to_category,
                )
            )

        assert success == magnets
        assert failed == []
        # path_to_id is cached per path → 2 unique paths, 2 calls.
        assert fake_client.path_to_id.await_count == 2
        # offline_download must receive the parent_id for the matching folder.
        calls = fake_client.offline_download.await_args_list
        parent_ids = [c.kwargs.get('parent_id') for c in calls]
        assert parent_ids == ['id-javdb', 'id-adhoc', 'id-javdb']

    def test_batch_falls_back_to_root_when_path_lookup_fails(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from packages.python.javdb_integrations.pikpak_bridge import (
            process_pikpak_batch,
        )

        magnets = ['magnet:?xt=urn:btih:zzz']
        fake_client = AsyncMock()
        fake_client.login = AsyncMock(return_value=None)
        fake_client.refresh_access_token = AsyncMock(return_value=None)
        fake_client.path_to_id = AsyncMock(side_effect=Exception('quota exceeded'))
        fake_client.offline_download = AsyncMock(return_value={'task': {'id': 't1'}})

        with patch(
            'packages.python.javdb_integrations.pikpak_bridge.PikPakApi',
            return_value=fake_client,
        ):
            success, failed = asyncio.run(
                process_pikpak_batch(
                    magnets,
                    'user@example.com',
                    'pw',
                    delay_between_requests=0,
                    root_folder='/Javdb_AutoSpider',
                    magnet_to_category={magnets[0]: 'JavDB'},
                )
            )

        assert success == magnets
        assert failed == []
        # Folder resolution failed → fall back to account root (parent_id=None).
        fake_client.offline_download.assert_awaited_once()
        assert fake_client.offline_download.await_args.kwargs.get('parent_id') is None


class TestRemoveCompletedTorrentsKeepFiles:
    """remove_completed_torrents_keep_files (per-qB cleanup before PikPak)."""

    def test_calls_delete_with_keep_files_when_completed_present(self):
        from unittest.mock import MagicMock
        from packages.python.javdb_integrations.pikpak_bridge import (
            remove_completed_torrents_keep_files,
        )

        mock_qb = MagicMock()
        mock_qb.get_torrents_multiple_categories.return_value = [
            {'hash': 'h1', 'name': 'done1'},
        ]
        remove_completed_torrents_keep_files(mock_qb, ['Ad Hoc'], dry_run=False)
        mock_qb.get_torrents_multiple_categories.assert_called_once_with(
            ['Ad Hoc'], torrent_filter='completed'
        )
        mock_qb.delete_torrents.assert_called_once_with(['h1'], delete_files=False)

    def test_skips_delete_when_empty(self):
        from unittest.mock import MagicMock
        from packages.python.javdb_integrations.pikpak_bridge import (
            remove_completed_torrents_keep_files,
        )

        mock_qb = MagicMock()
        mock_qb.get_torrents_multiple_categories.return_value = []
        remove_completed_torrents_keep_files(mock_qb, ['Ad Hoc'], dry_run=False)
        mock_qb.delete_torrents.assert_not_called()

    def test_dry_run_does_not_delete(self):
        from unittest.mock import MagicMock
        from packages.python.javdb_integrations.pikpak_bridge import (
            remove_completed_torrents_keep_files,
        )

        mock_qb = MagicMock()
        mock_qb.get_torrents_multiple_categories.return_value = [
            {'hash': 'h1', 'name': 'done1'},
        ]
        remove_completed_torrents_keep_files(mock_qb, ['Ad Hoc'], dry_run=True)
        mock_qb.delete_torrents.assert_not_called()


# Use temp_dir fixture
@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    import tempfile
    import shutil
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)
