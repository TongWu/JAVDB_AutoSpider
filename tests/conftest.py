"""
Pytest configuration and fixtures for JAVDB AutoSpider tests.
"""
import os
import sys

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Mock pikpakapi before any other imports to avoid Python 3.9 compatibility issues
# The pikpakapi library uses `from types import NoneType` which is only available in Python 3.10+
from unittest.mock import MagicMock

# Create mock pikpakapi module
mock_pikpakapi = MagicMock()
mock_pikpakapi.PikPakApi = MagicMock()
sys.modules['pikpakapi'] = mock_pikpakapi

import pytest
import tempfile
import shutil
from unittest.mock import patch

import utils.db as _db_mod
import utils.config_helper as _cfg_mod
import scripts.spider.dedup_checker as _dedup_mod


@pytest.fixture(autouse=True)
def _isolate_sqlite(tmp_path):
    """Give every test a fresh, empty SQLite database.

    This prevents SQLite state from leaking between tests and protects the
    real ``reports/javdb_autospider.db`` from being modified by the test suite.

    ``STORAGE_MODE`` defaults to ``'db'`` so that ``init_db`` actually
    creates the schema.  Individual tests can override the mode via
    the ``storage_mode`` fixture.
    """
    test_db = str(tmp_path / "test.db")
    original = _db_mod.DB_PATH
    _db_mod.DB_PATH = test_db

    # Reset dedup_checker module-level state
    _dedup_mod._db_initialised = False
    _dedup_mod._pending_paths_cache = None

    with patch.object(_cfg_mod, 'storage_mode', return_value='db'):
        _db_mod.init_db(test_db)

    yield test_db

    _db_mod.close_db()
    _db_mod.DB_PATH = original


@pytest.fixture
def storage_mode_db(monkeypatch):
    """Force STORAGE_MODE='db' for the test."""
    monkeypatch.setattr(_cfg_mod, 'storage_mode', lambda: 'db')
    monkeypatch.setattr(_cfg_mod, 'use_sqlite', lambda: True)
    monkeypatch.setattr(_cfg_mod, 'use_csv', lambda: False)


@pytest.fixture
def storage_mode_csv(monkeypatch):
    """Force STORAGE_MODE='csv' for the test."""
    monkeypatch.setattr(_cfg_mod, 'storage_mode', lambda: 'csv')
    monkeypatch.setattr(_cfg_mod, 'use_sqlite', lambda: False)
    monkeypatch.setattr(_cfg_mod, 'use_csv', lambda: True)


@pytest.fixture
def storage_mode_duo(monkeypatch):
    """Force STORAGE_MODE='duo' for the test."""
    monkeypatch.setattr(_cfg_mod, 'storage_mode', lambda: 'duo')
    monkeypatch.setattr(_cfg_mod, 'use_sqlite', lambda: True)
    monkeypatch.setattr(_cfg_mod, 'use_csv', lambda: True)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    # Cleanup after test
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def sample_history_csv(temp_dir):
    """Create a sample history CSV file for testing."""
    history_file = os.path.join(temp_dir, 'parsed_movies_history.csv')
    with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('href,phase,video_code,create_datetime,update_datetime,last_visited_datetime,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
        f.write('/v/ABC-123,1,ABC-123,2024-01-01 10:00:00,2024-01-01 10:00:00,2024-01-01 10:00:00,[2024-01-01]magnet:?xt=urn:btih:abc123,,,\n')
        f.write('/v/DEF-456,2,DEF-456,2024-01-02 10:00:00,2024-01-02 10:00:00,2024-01-02 10:00:00,,,[2024-01-02]magnet:?xt=urn:btih:def456,\n')
    return history_file


@pytest.fixture
def sample_index_html():
    """Return sample index page HTML for testing."""
    return '''
    <html>
    <head><title>JavDB</title></head>
    <body>
        <div class="movie-list h cols-4 vcols-8">
            <div class="item">
                <a class="box" href="/v/ABC-123">
                    <div class="video-title"><strong>ABC-123</strong></div>
                    <div class="tags has-addons">
                        <span class="tag">含中字磁鏈</span>
                        <span class="tag">今日新種</span>
                    </div>
                    <div class="score">
                        <span class="value">4.47分, 由595人評價</span>
                    </div>
                </a>
            </div>
            <div class="item">
                <a class="box" href="/v/DEF-456">
                    <div class="video-title"><strong>DEF-456</strong></div>
                    <div class="tags has-addons">
                        <span class="tag">含磁鏈</span>
                        <span class="tag">今日新種</span>
                    </div>
                    <div class="score">
                        <span class="value">4.52分, 由120人評價</span>
                    </div>
                </a>
            </div>
            <div class="item">
                <a class="box" href="/v/GHI-789">
                    <div class="video-title"><strong>GHI-789</strong></div>
                    <div class="tags has-addons">
                        <span class="tag">含中字磁鏈</span>
                        <span class="tag">昨日新種</span>
                    </div>
                    <div class="score">
                        <span class="value">3.85分, 由50人評價</span>
                    </div>
                </a>
            </div>
        </div>
    </body>
    </html>
    '''


@pytest.fixture
def sample_index_html_with_magnet_tags():
    """Return sample index page HTML with different magnet tag scenarios for testing adhoc magnet filtering."""
    return '''
    <html>
    <head><title>JavDB</title></head>
    <body>
        <div class="movie-list h cols-4 vcols-8">
            <!-- Entry with subtitle magnet tag -->
            <div class="item">
                <a class="box" href="/v/ABC-123">
                    <div class="video-title"><strong>ABC-123</strong></div>
                    <div class="tags has-addons">
                        <span class="tag is-warning">含中字磁鏈</span>
                    </div>
                    <div class="score">
                        <span class="value">4.47分, 由595人評價</span>
                    </div>
                </a>
            </div>
            <!-- Entry with regular magnet tag (no subtitle) -->
            <div class="item">
                <a class="box" href="/v/DEF-456">
                    <div class="video-title"><strong>DEF-456</strong></div>
                    <div class="tags has-addons">
                        <span class="tag is-success">含磁鏈</span>
                    </div>
                    <div class="score">
                        <span class="value">4.52分, 由120人評價</span>
                    </div>
                </a>
            </div>
            <!-- Entry WITHOUT any magnet tag (should be filtered out) -->
            <div class="item">
                <a class="box" href="/v/GHI-789">
                    <div class="video-title"><strong>GHI-789</strong></div>
                    <div class="tags has-addons">
                    </div>
                    <div class="score">
                        <span class="value">4.85分, 由200人評價</span>
                    </div>
                </a>
            </div>
            <!-- Entry with empty tags div (should be filtered out) -->
            <div class="item">
                <a class="box" href="/v/JKL-012">
                    <div class="video-title"><strong>JKL-012</strong></div>
                    <div class="tags has-addons">
                    </div>
                    <div class="score">
                        <span class="value">4.10分, 由80人評價</span>
                    </div>
                </a>
            </div>
        </div>
    </body>
    </html>
    '''


@pytest.fixture
def sample_detail_html():
    """Return sample detail page HTML for testing."""
    return '''
    <html>
    <head><title>ABC-123 Detail</title></head>
    <body>
        <div class="video-meta-panel">
            <div class="panel-block">
                <strong>演員:</strong>
                <span class="value">
                    <a href="/actors/xyz">Sample Actor</a>
                </span>
            </div>
        </div>
        <div id="magnets-content">
            <div class="item columns is-desktop">
                <div class="magnet-name">
                    <a href="magnet:?xt=urn:btih:abc123subtitle">
                        <span class="name">ABC-123-subtitle.torrent</span>
                        <span class="meta">4.94GB, 1個文件</span>
                        <div class="tags">
                            <span class="tag">字幕</span>
                        </div>
                    </a>
                </div>
                <span class="time">2024-01-15</span>
            </div>
            <div class="item columns is-desktop">
                <div class="magnet-name">
                    <a href="magnet:?xt=urn:btih:abc123hacked">
                        <span class="name">ABC-123-UC.torrent</span>
                        <span class="meta">5.2GB, 1個文件</span>
                        <div class="tags">
                            <span class="tag">HD</span>
                        </div>
                    </a>
                </div>
                <span class="time">2024-01-14</span>
            </div>
            <div class="item columns is-desktop">
                <div class="magnet-name">
                    <a href="magnet:?xt=urn:btih:abc123normal">
                        <span class="name">ABC-123.torrent</span>
                        <span class="meta">2.1GB, 1個文件</span>
                        <div class="tags">
                            <span class="tag">HD</span>
                        </div>
                    </a>
                </div>
                <span class="time">2024-01-13</span>
            </div>
        </div>
    </body>
    </html>
    '''


@pytest.fixture
def sample_magnets():
    """Return sample magnet data for testing."""
    return [
        {
            'href': 'magnet:?xt=urn:btih:abc123subtitle',
            'name': 'ABC-123-subtitle.torrent',
            'tags': ['字幕'],
            'size': '4.94GB',
            'timestamp': '2024-01-15'
        },
        {
            'href': 'magnet:?xt=urn:btih:abc123uc',
            'name': 'ABC-123-UC.torrent',
            'tags': ['HD'],
            'size': '5.2GB',
            'timestamp': '2024-01-14'
        },
        {
            'href': 'magnet:?xt=urn:btih:abc123u',
            'name': 'ABC-123-U.torrent',
            'tags': ['HD'],
            'size': '4.8GB',
            'timestamp': '2024-01-13'
        },
        {
            'href': 'magnet:?xt=urn:btih:abc123normal',
            'name': 'ABC-123.torrent',
            'tags': ['HD'],
            'size': '2.1GB',
            'timestamp': '2024-01-12'
        },
        {
            'href': 'magnet:?xt=urn:btih:abc1234k',
            'name': 'ABC-123-4K.torrent',
            'tags': ['4K', 'HD'],
            'size': '8.5GB',
            'timestamp': '2024-01-11'
        }
    ]

