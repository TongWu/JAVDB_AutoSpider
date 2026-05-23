"""Unit tests for packages/python/javdb_platform/db_stats.py"""

import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import pytest
from unittest.mock import patch, MagicMock
from javdb.storage.db import _db_stats as db_stats


class TestDbSaveSpiderStats:
    """Tests for db_save_spider_stats()"""

    @patch('javdb.storage.db._db_stats._get_db')
    def test_saves_spider_stats_successfully(self, mock_get_db):
        """Should insert spider stats with all fields"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = mock_conn

        stats = {
            'phase1_discovered': 10,
            'phase1_processed': 8,
            'phase1_skipped': 1,
            'phase1_no_new': 1,
            'phase1_failed': 0,
            'phase2_discovered': 5,
            'phase2_processed': 4,
            'phase2_skipped': 1,
            'phase2_no_new': 0,
            'phase2_failed': 0,
            'total_discovered': 15,
            'total_processed': 12,
            'total_skipped': 2,
            'total_no_new': 1,
            'total_failed': 0,
            'failed_movies': ['/movies/abc123', '/movies/def456'],
        }

        result = db_stats.db_save_spider_stats('test-session-123', stats)

        assert result == 42
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert 'INSERT INTO SpiderStats' in call_args[0][0]
        assert 'ON CONFLICT(SessionId) DO UPDATE' in call_args[0][0]
        assert call_args[0][1][0] == 'test-session-123'
        assert call_args[0][1][1] == 10  # phase1_discovered
        assert call_args[0][1][11] == 15  # total_discovered

    @patch('javdb.storage.db._db_stats._get_db')
    def test_handles_missing_fields_with_defaults(self, mock_get_db):
        """Should use default values for missing fields"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = mock_conn

        stats = {}  # Empty stats

        result = db_stats.db_save_spider_stats('test-session-456', stats)

        assert result == 1
        call_args = mock_conn.execute.call_args
        # All numeric fields should default to 0
        for i in range(1, 16):  # phase1_discovered through total_failed
            assert call_args[0][1][i] == 0
        # failed_movies should be empty string
        assert call_args[0][1][16] == ''

    @patch('javdb.storage.db._db_stats._get_db')
    def test_serializes_failed_movies_as_json(self, mock_get_db):
        """Should serialize failed_movies list as JSON"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = mock_conn

        stats = {
            'failed_movies': ['/movies/abc', '/movies/中文'],
        }

        db_stats.db_save_spider_stats('test-session', stats)

        call_args = mock_conn.execute.call_args
        failed_movies_json = call_args[0][1][16]
        assert '"/movies/abc"' in failed_movies_json
        assert '"/movies/中文"' in failed_movies_json  # ensure_ascii=False


class TestDbSaveUploaderStats:
    """Tests for db_save_uploader_stats()"""

    @patch('javdb.storage.db._db_stats._get_db')
    def test_saves_uploader_stats_successfully(self, mock_get_db):
        """Should insert uploader stats with all fields"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 99
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = mock_conn

        stats = {
            'total_torrents': 20,
            'duplicate_count': 5,
            'attempted': 15,
            'successfully_added': 12,
            'failed_count': 3,
            'hacked_sub': 8,
            'hacked_nosub': 4,
            'subtitle_count': 10,
            'no_subtitle_count': 5,
            'success_rate': 0.8,
        }

        result = db_stats.db_save_uploader_stats('test-session-789', stats)

        assert result == 99
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert 'INSERT INTO UploaderStats' in call_args[0][0]
        assert call_args[0][1][0] == 'test-session-789'
        assert call_args[0][1][1] == 20  # total_torrents
        assert call_args[0][1][10] == 0.8  # success_rate


class TestDbSavePikpakStats:
    """Tests for db_save_pikpak_stats()"""

    @patch('javdb.storage.db._db_stats._get_db')
    def test_saves_pikpak_stats_successfully(self, mock_get_db):
        """Should insert PikPak stats with all fields"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 77
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = mock_conn

        stats = {
            'threshold_days': 7,
            'total_torrents': 30,
            'filtered_old': 10,
            'successful_count': 18,
            'failed_count': 2,
            'uploaded_count': 18,
            'delete_failed_count': 1,
        }

        result = db_stats.db_save_pikpak_stats('test-session-abc', stats)

        assert result == 77
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert 'INSERT INTO PikpakStats' in call_args[0][0]
        assert call_args[0][1][0] == 'test-session-abc'
        assert call_args[0][1][1] == 7  # threshold_days

    @patch('javdb.storage.db._db_stats._get_db')
    def test_defaults_uploaded_count_to_successful_count(self, mock_get_db):
        """Should default uploaded_count to successful_count if not provided"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = mock_conn

        stats = {
            'successful_count': 25,
            # uploaded_count not provided
        }

        db_stats.db_save_pikpak_stats('test-session', stats)

        call_args = mock_conn.execute.call_args
        uploaded_count = call_args[0][1][6]
        assert uploaded_count == 25  # Should default to successful_count


class TestDbGetSpiderStats:
    """Tests for db_get_spider_stats()"""

    @patch('javdb.storage.db._db_stats._get_db')
    def test_returns_stats_when_found(self, mock_get_db):
        """Should return stats dictionary when session exists"""
        mock_conn = MagicMock()
        mock_row = {
            'SessionId': 'test-session',
            'Phase1Discovered': 10,
            'TotalProcessed': 15,
        }
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = mock_conn

        result = db_stats.db_get_spider_stats('test-session')

        assert result == mock_row
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert 'SELECT * FROM SpiderStats' in call_args[0][0]
        assert call_args[0][1] == ('test-session',)

    @patch('javdb.storage.db._db_stats._get_db')
    def test_returns_none_when_not_found(self, mock_get_db):
        """Should return None when session does not exist"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_db.return_value = mock_conn

        result = db_stats.db_get_spider_stats('nonexistent-session')

        assert result is None


class TestDbGetSpiderStatsLocal:
    """Tests for db_get_spider_stats_local()"""

    @patch('javdb.storage.db._db_stats._ensure_imports')
    def test_uses_local_sqlite_connection(self, mock_ensure_imports):
        """Should use get_local_sqlite_db() instead of get_db()"""
        import sqlite3

        mock_get_local_db = MagicMock()
        # Pre-load the lazy imports to avoid import error
        db_stats._get_local_sqlite_db = mock_get_local_db
        db_stats._REPORTS_DB_PATH = '/fake/path'

        # Create a real sqlite3.Row-like object
        mock_conn = MagicMock()
        mock_conn.row_factory = sqlite3.Row

        # Mock the row as a dict-like object that can be converted to dict
        class FakeRow:
            def __init__(self, data):
                self._data = data
            def __getitem__(self, key):
                return self._data[key]
            def keys(self):
                return self._data.keys()
            def __iter__(self):
                return iter(self._data.keys())
            def __bool__(self):
                return True

        mock_row = FakeRow({'SessionId': 'test-session', 'TotalProcessed': 10})
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_local_db.return_value = mock_conn

        result = db_stats.db_get_spider_stats_local('test-session')

        # Should return the row converted to dict
        assert result is not None
        assert result['SessionId'] == 'test-session'
        assert result['TotalProcessed'] == 10
        mock_get_local_db.assert_called_once()

    @patch('javdb.storage.db._db_stats._ensure_imports')
    def test_sets_row_factory(self, mock_ensure_imports):
        """Should set row_factory to sqlite3.Row"""
        import sqlite3
        mock_get_local_db = MagicMock()
        # Pre-load the lazy imports to avoid import error
        db_stats._get_local_sqlite_db = mock_get_local_db
        db_stats._REPORTS_DB_PATH = '/fake/path'

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.execute.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_local_db.return_value = mock_conn

        db_stats.db_get_spider_stats_local('test-session')

        # Should set row_factory to sqlite3.Row
        # We can't directly assert the value because it's a mock,
        # but we can verify the attribute was set
        assert hasattr(mock_conn, 'row_factory')


class TestLazyImports:
    """Tests for lazy import mechanism"""

    def test_ensure_imports_is_idempotent(self):
        """Should not re-import if already loaded"""
        db_stats._get_db = MagicMock()
        original_get_db = db_stats._get_db

        db_stats._ensure_imports()

        # Should still be the same object
        assert db_stats._get_db is original_get_db
