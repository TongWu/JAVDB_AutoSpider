"""
Unit tests for utils/history_manager.py
"""
import pytest
import os
import csv
import tempfile
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

from utils.history_manager import (
    load_parsed_movies_history,
    cleanup_history_file,
    save_parsed_movie_to_history,
    determine_torrent_types,
    determine_torrent_type,
    get_missing_torrent_types,
    should_process_movie,
    check_torrent_in_history,
    add_downloaded_indicator_to_csv,
    is_downloaded_torrent,
    mark_torrent_as_downloaded,
    validate_history_file
)


@pytest.fixture
def temp_history_file():
    """Create a temporary history file for testing"""
    fd, path = tempfile.mkstemp(suffix='.csv')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def sample_history_data():
    """Sample history data for testing"""
    return [
        {
            'href': '/v/abc123',
            'phase': '1',
            'video_code': 'TEST-001',
            'create_date': '2024-01-01 10:00:00',
            'update_date': '2024-01-01 10:00:00',
            'hacked_subtitle': '[2024-01-01]magnet:?xt=urn:btih:test1',
            'hacked_no_subtitle': '',
            'subtitle': '',
            'no_subtitle': ''
        },
        {
            'href': '/v/def456',
            'phase': '2',
            'video_code': 'TEST-002',
            'create_date': '2024-01-02 10:00:00',
            'update_date': '2024-01-02 10:00:00',
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': '[2024-01-02]magnet:?xt=urn:btih:test2',
            'no_subtitle': ''
        }
    ]


class TestLoadParsedMoviesHistory:
    """Tests for load_parsed_movies_history function"""
    
    def test_load_empty_history(self, temp_history_file):
        """Test loading when history file doesn't exist"""
        os.remove(temp_history_file)
        history = load_parsed_movies_history(temp_history_file)
        assert history == {}
    
    def test_load_history_with_data(self, temp_history_file, sample_history_data):
        """Test loading history with existing data"""
        # Write sample data to file
        with open(temp_history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date',
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in sample_history_data:
                writer.writerow(row)
        
        # Load and verify
        history = load_parsed_movies_history(temp_history_file)
        assert len(history) == 2
        assert '/v/abc123' in history
        assert history['/v/abc123']['video_code'] == 'TEST-001'
        assert 'hacked_subtitle' in history['/v/abc123']['torrent_types']
    
    def test_load_history_with_phase_filter(self, temp_history_file, sample_history_data):
        """Test loading history with phase filter"""
        # Write sample data
        with open(temp_history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date',
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in sample_history_data:
                writer.writerow(row)
        
        # Load with phase 1 filter
        history = load_parsed_movies_history(temp_history_file, phase=1)
        assert len(history) == 2  # Phase 1 loads all records except pure phase 2
    
    def test_load_history_with_duplicates(self, temp_history_file):
        """Test that duplicates are handled correctly"""
        # Write duplicate data
        with open(temp_history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date',
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            # Same href with different dates
            writer.writerow({
                'href': '/v/abc123',
                'phase': '1',
                'video_code': 'TEST-001',
                'create_date': '2024-01-01 10:00:00',
                'update_date': '2024-01-01 10:00:00',
                'hacked_subtitle': '',
                'hacked_no_subtitle': '',
                'subtitle': '',
                'no_subtitle': ''
            })
            writer.writerow({
                'href': '/v/abc123',
                'phase': '1',
                'video_code': 'TEST-001',
                'create_date': '2024-01-01 10:00:00',
                'update_date': '2024-01-02 10:00:00',
                'hacked_subtitle': '[2024-01-02]magnet:?xt=urn:btih:test1',
                'hacked_no_subtitle': '',
                'subtitle': '',
                'no_subtitle': ''
            })
        
        history = load_parsed_movies_history(temp_history_file)
        assert len(history) == 1  # Duplicate removed
        assert history['/v/abc123']['update_date'] == '2024-01-02 10:00:00'


class TestDetermineTorrentTypes:
    """Tests for torrent type determination functions"""
    
    def test_determine_torrent_types_hacked_subtitle(self):
        """Test identifying hacked_subtitle torrent type"""
        magnet_links = {
            'hacked_subtitle': 'magnet:?xt=urn:btih:test',
            'hacked_no_subtitle': '',
            'subtitle': '',
            'no_subtitle': ''
        }
        types = determine_torrent_types(magnet_links)
        assert types == ['hacked_subtitle']
    
    def test_determine_torrent_types_multiple(self):
        """Test identifying multiple torrent types"""
        magnet_links = {
            'hacked_subtitle': 'magnet:?xt=urn:btih:test1',
            'hacked_no_subtitle': '',
            'subtitle': 'magnet:?xt=urn:btih:test2',
            'no_subtitle': ''
        }
        types = determine_torrent_types(magnet_links)
        assert 'hacked_subtitle' in types
        assert 'subtitle' in types
    
    def test_determine_torrent_type_legacy(self):
        """Test legacy function returns first type"""
        magnet_links = {
            'hacked_subtitle': 'magnet:?xt=urn:btih:test1',
            'subtitle': 'magnet:?xt=urn:btih:test2'
        }
        result = determine_torrent_type(magnet_links)
        assert result == 'hacked_subtitle'


class TestGetMissingTorrentTypes:
    """Tests for get_missing_torrent_types function"""
    
    def test_missing_hacked_subtitle(self):
        """Test detecting missing hacked_subtitle"""
        history_types = ['subtitle']
        current_types = ['hacked_subtitle', 'subtitle']
        missing = get_missing_torrent_types(history_types, current_types)
        assert 'hacked_subtitle' in missing
    
    def test_no_missing_types(self):
        """Test when no types are missing"""
        history_types = ['hacked_subtitle', 'subtitle']
        current_types = ['hacked_subtitle', 'subtitle']
        missing = get_missing_torrent_types(history_types, current_types)
        assert len(missing) == 0
    
    def test_missing_subtitle_when_only_no_subtitle(self):
        """Test detecting missing subtitle when only no_subtitle exists"""
        history_types = ['no_subtitle']
        current_types = ['subtitle']
        missing = get_missing_torrent_types(history_types, current_types)
        assert 'subtitle' in missing


class TestShouldProcessMovie:
    """Tests for should_process_movie function"""
    
    def test_process_new_movie(self):
        """Test that new movies should be processed"""
        history_data = {}
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:test'}
        should_process, _ = should_process_movie('/v/new123', history_data, 1, magnet_links)
        assert should_process is True
    
    def test_skip_existing_movie_no_missing_types(self):
        """Test that existing movies with no missing types should be skipped"""
        history_data = {
            '/v/abc123': {
                'phase': '1',
                'video_code': 'TEST-001',
                'torrent_types': ['hacked_subtitle']
            }
        }
        magnet_links = {'hacked_subtitle': 'magnet:?xt=urn:btih:test'}
        should_process, _ = should_process_movie('/v/abc123', history_data, 1, magnet_links)
        assert should_process is False
    
    def test_process_existing_movie_with_missing_types(self):
        """Test that existing movies with missing types should be processed"""
        history_data = {
            '/v/abc123': {
                'phase': '1',
                'video_code': 'TEST-001',
                'torrent_types': ['subtitle']
            }
        }
        magnet_links = {
            'hacked_subtitle': 'magnet:?xt=urn:btih:test1',
            'subtitle': 'magnet:?xt=urn:btih:test2'
        }
        should_process, _ = should_process_movie('/v/abc123', history_data, 1, magnet_links)
        assert should_process is True


class TestSaveParsedMovieToHistory:
    """Tests for save_parsed_movie_to_history function"""
    
    def test_save_new_movie(self, temp_history_file):
        """Test saving a new movie to history"""
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:test'}
        save_parsed_movie_to_history(
            temp_history_file,
            '/v/new123',
            '1',
            'TEST-001',
            magnet_links
        )
        
        # Verify saved
        with open(temp_history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0]['href'] == '/v/new123'
            assert rows[0]['video_code'] == 'TEST-001'
    
    def test_update_existing_movie(self, temp_history_file):
        """Test updating an existing movie in history"""
        # First save
        magnet_links1 = {'subtitle': 'magnet:?xt=urn:btih:test1'}
        save_parsed_movie_to_history(
            temp_history_file,
            '/v/abc123',
            '1',
            'TEST-001',
            magnet_links1
        )
        
        # Update with new torrent type
        magnet_links2 = {'hacked_subtitle': 'magnet:?xt=urn:btih:test2'}
        save_parsed_movie_to_history(
            temp_history_file,
            '/v/abc123',
            '1',
            'TEST-001',
            magnet_links2
        )
        
        # Verify both types are saved
        with open(temp_history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0]['href'] == '/v/abc123'
            # Check that both magnet types are present
            assert 'magnet:' in rows[0]['subtitle']
            assert 'magnet:' in rows[0]['hacked_subtitle']


class TestCheckTorrentInHistory:
    """Tests for check_torrent_in_history function"""
    
    def test_torrent_not_in_history(self, temp_history_file):
        """Test checking torrent not in history"""
        result = check_torrent_in_history(temp_history_file, '/v/abc123', 'subtitle')
        assert result is False
    
    def test_torrent_in_history(self, temp_history_file, sample_history_data):
        """Test checking torrent that exists in history"""
        # Write sample data
        with open(temp_history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'create_date', 'update_date',
                         'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(sample_history_data[0])
        
        result = check_torrent_in_history(temp_history_file, '/v/abc123', 'hacked_subtitle')
        assert result is True


class TestIsDownloadedTorrent:
    """Tests for is_downloaded_torrent function"""
    
    def test_downloaded_torrent(self):
        """Test identifying downloaded torrent"""
        content = "[DOWNLOADED PREVIOUSLY]"
        assert is_downloaded_torrent(content) is True
    
    def test_not_downloaded_torrent(self):
        """Test identifying non-downloaded torrent"""
        content = "magnet:?xt=urn:btih:test"
        assert is_downloaded_torrent(content) is False


class TestValidateHistoryFile:
    """Tests for validate_history_file function"""
    
    def test_validate_nonexistent_file(self, temp_history_file):
        """Test validating non-existent file"""
        os.remove(temp_history_file)
        result = validate_history_file(temp_history_file)
        assert result is True
    
    def test_validate_old_format_conversion(self, temp_history_file):
        """Test converting old format to new format"""
        # Write old format data
        with open(temp_history_file, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ['href', 'phase', 'video_code', 'parsed_date', 'torrent_type']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow({
                'href': '/v/abc123',
                'phase': '1',
                'video_code': 'TEST-001',
                'parsed_date': '2024-01-01 10:00:00',
                'torrent_type': 'subtitle'
            })
        
        result = validate_history_file(temp_history_file)
        assert result is True
        
        # Verify converted to new format
        with open(temp_history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert 'create_date' in rows[0]
            assert 'update_date' in rows[0]
            assert 'subtitle' in rows[0]
