"""
Pytest configuration and shared fixtures for all tests
"""
import os
import sys
import pytest
import tempfile
import shutil

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Create a mock config module to avoid ImportError
# This allows tests to run without a real config.py
@pytest.fixture(autouse=True)
def mock_config(monkeypatch):
    """Mock config module for all tests"""
    # Create mock config values
    mock_values = {
        'GIT_USERNAME': 'test_user',
        'GIT_PASSWORD': 'test_password',
        'GIT_REPO_URL': 'https://github.com/test/repo.git',
        'GIT_BRANCH': 'main',
        'SMTP_SERVER': 'smtp.test.com',
        'SMTP_PORT': 587,
        'SMTP_USER': 'test@test.com',
        'SMTP_PASSWORD': 'test_smtp_password',
        'EMAIL_FROM': 'test@test.com',
        'EMAIL_TO': 'test@test.com',
        'PIPELINE_LOG_FILE': 'logs/test_pipeline.log',
        'SPIDER_LOG_FILE': 'logs/test_spider.log',
        'UPLOADER_LOG_FILE': 'logs/test_uploader.log',
        'PIKPAK_LOG_FILE': 'logs/test_pikpak.log',
        'DAILY_REPORT_DIR': 'Daily Report',
        'AD_HOC_DIR': 'Ad Hoc',
        'LOG_LEVEL': 'DEBUG',
        'BASE_URL': 'https://javdb.com',
        'START_PAGE': 1,
        'END_PAGE': 5,
        'PARSED_MOVIES_CSV': 'parsed_movies_history.csv',
        'DETAIL_PAGE_SLEEP': 0,  # No sleep in tests
        'PAGE_SLEEP': 0,
        'MOVIE_SLEEP': 0,
        'JAVDB_SESSION_COOKIE': None,
        'PHASE2_MIN_RATE': 4.0,
        'PHASE2_MIN_COMMENTS': 100,
        'PROXY_HTTP': None,
        'PROXY_HTTPS': None,
        'PROXY_MODULES': ['all'],
        'CF_TURNSTILE_COOLDOWN': 0,
        'PHASE_TRANSITION_COOLDOWN': 0,
        'FALLBACK_COOLDOWN': 0,
        'CF_BYPASS_SERVICE_PORT': 8000,
        'PROXY_MODE': 'single',
        'PROXY_POOL': [],
        'PROXY_POOL_COOLDOWN_SECONDS': 300,
        'PROXY_POOL_MAX_FAILURES': 3,
        'QB_HOST': 'localhost',
        'QB_PORT': '8080',
        'QB_USERNAME': 'admin',
        'QB_PASSWORD': 'admin',
        'TORRENT_CATEGORY': 'JavDB',
        'TORRENT_CATEGORY_ADHOC': 'Ad Hoc',
        'TORRENT_SAVE_PATH': '/downloads',
        'AUTO_START': True,
        'SKIP_CHECKING': False,
        'REQUEST_TIMEOUT': 30,
        'DELAY_BETWEEN_ADDITIONS': 0,
        'IGNORE_RELEASE_DATE_FILTER': False,
        # PikPak configuration
        'PIKPAK_EMAIL': 'test@test.com',
        'PIKPAK_PASSWORD': 'test_password',
        'PIKPAK_DOWNLOAD_FOLDER': '/test/downloads',
        # Additional config values that may be needed
        'PARSED_MOVIES_CSV': 'parsed_movies_history.csv',
    }
    
    # Create a mock config module
    class MockConfig:
        pass
    
    mock_config_obj = MockConfig()
    for key, value in mock_values.items():
        setattr(mock_config_obj, key, value)
    
    # Patch sys.modules to include our mock config
    monkeypatch.setitem(sys.modules, 'config', mock_config_obj)
    
    return mock_values


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files"""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    # Cleanup after test
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def sample_history_csv(temp_dir):
    """Create a sample history CSV file for testing"""
    import csv
    history_file = os.path.join(temp_dir, 'parsed_movies_history.csv')
    
    fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date', 
                  'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
    
    sample_data = [
        {
            'href': '/v/abc123',
            'phase': '1',
            'video_code': 'ABC-123',
            'create_date': '2025-01-01 10:00:00',
            'update_date': '2025-01-01 10:00:00',
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': '[2025-01-01]magnet:?xt=urn:btih:abc123',
            'no_subtitle': ''
        },
        {
            'href': '/v/def456',
            'phase': '2',
            'video_code': 'DEF-456',
            'create_date': '2025-01-02 10:00:00',
            'update_date': '2025-01-02 10:00:00',
            'hacked_subtitle': '',
            'hacked_no_subtitle': '[2025-01-02]magnet:?xt=urn:btih:def456',
            'subtitle': '',
            'no_subtitle': ''
        }
    ]
    
    with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sample_data:
            writer.writerow(row)
    
    return history_file


@pytest.fixture
def sample_daily_csv(temp_dir):
    """Create a sample daily CSV file for testing"""
    import csv
    csv_file = os.path.join(temp_dir, 'Javdb_TodayTitle_20250101.csv')
    
    fieldnames = ['href', 'video_code', 'page', 'actor', 'rate', 'comment_number',
                  'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle',
                  'size_hacked_subtitle', 'size_hacked_no_subtitle', 'size_subtitle', 'size_no_subtitle']
    
    sample_data = [
        {
            'href': '/v/test001',
            'video_code': 'TEST-001',
            'page': '1',
            'actor': 'Test Actor',
            'rate': '4.5',
            'comment_number': '150',
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': 'magnet:?xt=urn:btih:test001',
            'no_subtitle': '',
            'size_hacked_subtitle': '',
            'size_hacked_no_subtitle': '',
            'size_subtitle': '2.5GB',
            'size_no_subtitle': ''
        }
    ]
    
    with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sample_data:
            writer.writerow(row)
    
    return csv_file


@pytest.fixture
def sample_spider_log(temp_dir):
    """Create a sample spider log file for testing"""
    log_file = os.path.join(temp_dir, 'Javdb_Spider.log')
    
    log_content = """2025-01-01 10:00:00 - Starting JavDB spider...
2025-01-01 10:00:01 - PHASE 1: Processing entries with both subtitle and today/yesterday tags
2025-01-01 10:00:02 - [Page 1] Found 5 entries for phase 1
2025-01-01 10:00:03 - Successfully fetched URL: https://javdb.com
2025-01-01 10:00:04 - Phase 1 completed: 3 entries processed
2025-01-01 10:00:05 - PHASE 2: Processing entries with only today/yesterday tag
2025-01-01 10:00:06 - [Page 1] Found 10 entries for phase 2
2025-01-01 10:00:07 - Phase 2 completed: 5 entries processed
========================================
SUMMARY REPORT
========================================
Total entries found: 8
Successfully processed: 8
Skipped already parsed in this session: 2
Skipped already parsed in previous runs: 5
========================================
PROXY POOL STATISTICS
========================================
Total proxies: 2
Available proxies: 2
"""
    
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(log_content)
    
    return log_file


@pytest.fixture
def sample_uploader_log(temp_dir):
    """Create a sample uploader log file for testing"""
    log_file = os.path.join(temp_dir, 'qbtorrent_uploader.log')
    
    log_content = """2025-01-01 10:00:00 - Starting qBittorrent uploader...
2025-01-01 10:00:01 - Successfully logged in to qBittorrent
2025-01-01 10:00:02 - Total torrents found: 10
2025-01-01 10:00:03 - Successfully added: 8
2025-01-01 10:00:04 - Failed to add: 2
2025-01-01 10:00:05 - Hacked subtitle torrents: 3
2025-01-01 10:00:06 - Hacked no subtitle torrents: 2
2025-01-01 10:00:07 - Subtitle torrents: 2
2025-01-01 10:00:08 - No subtitle torrents: 1
2025-01-01 10:00:09 - Success rate: 80.0%
"""
    
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(log_content)
    
    return log_file


@pytest.fixture
def sample_pikpak_log(temp_dir):
    """Create a sample PikPak log file for testing"""
    log_file = os.path.join(temp_dir, 'pikpak_bridge.log')
    
    log_content = """2025-01-01 10:00:00 - Running PikPak Bridge with 3 days threshold
2025-01-01 10:00:01 - Found 20 torrents in qBittorrent
2025-01-01 10:00:02 - Filtered 5 torrents older than 3 days
2025-01-01 10:00:03 - Successfully added to PikPak
2025-01-01 10:00:04 - Removed from qBittorrent
2025-01-01 10:00:05 - Successfully added to PikPak
2025-01-01 10:00:06 - Removed from qBittorrent
"""
    
    with open(log_file, 'w', encoding='utf-8') as f:
        f.write(log_content)
    
    return log_file


@pytest.fixture
def sample_index_html():
    """Sample JavDB index page HTML for testing"""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>JavDB</title></head>
    <body>
        <div class="movie-list h cols-4 vcols-8">
            <div class="item">
                <a class="box" href="/v/test001">
                    <div class="video-title"><strong>TEST-001</strong> Title Here</div>
                    <div class="tags has-addons">
                        <span class="tag">含中字磁鏈</span>
                        <span class="tag">今日新種</span>
                    </div>
                    <div class="score">
                        <span class="value">4.5分, 由150人評價</span>
                    </div>
                </a>
            </div>
            <div class="item">
                <a class="box" href="/v/test002">
                    <div class="video-title"><strong>TEST-002</strong> Another Title</div>
                    <div class="tags has-addons">
                        <span class="tag">今日新種</span>
                    </div>
                    <div class="score">
                        <span class="value">4.8分, 由200人評價</span>
                    </div>
                </a>
            </div>
        </div>
    </body>
    </html>
    """


@pytest.fixture
def sample_detail_html():
    """Sample JavDB detail page HTML for testing"""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>TEST-001 - JavDB</title></head>
    <body>
        <div class="video-meta-panel">
            <div class="panel-block">
                <strong>演員:</strong>
                <span class="value">
                    <a href="/actors/abc">Test Actress</a>
                </span>
            </div>
        </div>
        <div id="magnets-content">
            <div class="item columns is-desktop">
                <div class="magnet-name">
                    <a href="magnet:?xt=urn:btih:abc123">
                        <span class="name">TEST-001-UC</span>
                        <span class="meta">2.5GB, 1個文件</span>
                        <div class="tags">
                            <span class="tag">字幕</span>
                        </div>
                    </a>
                </div>
                <span class="time">2025-01-01</span>
            </div>
            <div class="item columns is-desktop">
                <div class="magnet-name">
                    <a href="magnet:?xt=urn:btih:def456">
                        <span class="name">TEST-001-U</span>
                        <span class="meta">2.0GB, 1個文件</span>
                        <div class="tags"></div>
                    </a>
                </div>
                <span class="time">2025-01-01</span>
            </div>
            <div class="item columns is-desktop">
                <div class="magnet-name">
                    <a href="magnet:?xt=urn:btih:ghi789">
                        <span class="name">TEST-001</span>
                        <span class="meta">1.5GB, 1個文件</span>
                        <div class="tags">
                            <span class="tag">字幕</span>
                        </div>
                    </a>
                </div>
                <span class="time">2025-01-01</span>
            </div>
        </div>
    </body>
    </html>
    """
