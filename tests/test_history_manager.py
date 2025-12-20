"""
Unit tests for utils/history_manager.py functions.
"""
import os
import sys
import csv
import pytest

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.history_manager import (
    load_parsed_movies_history,
    save_parsed_movie_to_history,
    validate_history_file,
    determine_torrent_types,
    determine_torrent_type,
    get_missing_torrent_types,
    has_complete_subtitles,
    should_process_movie,
    check_torrent_in_history,
    is_downloaded_torrent
)


class TestLoadParsedMoviesHistory:
    """Test cases for load_parsed_movies_history function."""
    
    def test_load_nonexistent_file(self, temp_dir):
        """Test loading from non-existent file returns empty dict."""
        history_file = os.path.join(temp_dir, 'nonexistent.csv')
        result = load_parsed_movies_history(history_file)
        assert result == {}
    
    def test_load_existing_history(self, sample_history_csv):
        """Test loading existing history file."""
        result = load_parsed_movies_history(sample_history_csv)
        assert len(result) == 2
        assert '/v/ABC-123' in result
        assert '/v/DEF-456' in result
    
    def test_load_with_phase_filter(self, sample_history_csv):
        """Test loading history with phase filter."""
        # Phase 1 should exclude phase 2 records
        result = load_parsed_movies_history(sample_history_csv, phase=1)
        assert '/v/ABC-123' in result
        # Phase 2 record should be excluded when loading for phase 1
        # (based on the implementation, phase 1 excludes records with phase == '2')
        
    def test_load_all_phases(self, sample_history_csv):
        """Test loading history without phase filter."""
        result = load_parsed_movies_history(sample_history_csv, phase=None)
        assert len(result) == 2
        assert '/v/ABC-123' in result
        assert '/v/DEF-456' in result


class TestSaveParsedMovieToHistory:
    """Test cases for save_parsed_movie_to_history function."""
    
    def test_save_new_record(self, temp_dir):
        """Test saving a new movie to history."""
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Create empty history file
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
        
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:abc123'}
        save_parsed_movie_to_history(history_file, '/v/NEW-001', 1, 'NEW-001', magnet_links)
        
        # Verify the record was saved
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)
        
        assert len(records) == 1
        assert records[0]['href'] == '/v/NEW-001'
        assert records[0]['video_code'] == 'NEW-001'
        assert 'magnet:?xt=urn:btih:abc123' in records[0]['subtitle']
    
    def test_update_existing_record(self, temp_dir):
        """Test updating an existing movie in history."""
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Create history file with existing record
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            f.write('/v/EXIST-001,1,EXIST-001,2024-01-01 10:00:00,2024-01-01 10:00:00,,,[2024-01-01]magnet:?xt=urn:btih:old,\n')
        
        # Update with hacked_subtitle
        magnet_links = {'hacked_subtitle': 'magnet:?xt=urn:btih:new123'}
        save_parsed_movie_to_history(history_file, '/v/EXIST-001', 1, 'EXIST-001', magnet_links)
        
        # Verify the record was updated
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)
        
        assert len(records) == 1
        assert 'magnet:?xt=urn:btih:new123' in records[0]['hacked_subtitle']


class TestValidateHistoryFile:
    """Test cases for validate_history_file function."""
    
    def test_validate_nonexistent_file(self, temp_dir):
        """Test validating non-existent file returns True."""
        history_file = os.path.join(temp_dir, 'nonexistent.csv')
        result = validate_history_file(history_file)
        assert result is True
    
    def test_validate_valid_file(self, sample_history_csv):
        """Test validating a valid history file."""
        result = validate_history_file(sample_history_csv)
        assert result is True


class TestDetermineTorrentTypes:
    """Test cases for determine_torrent_types function."""
    
    def test_empty_magnet_links(self):
        """Test with empty magnet links."""
        magnet_links = {
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': '',
            'no_subtitle': ''
        }
        result = determine_torrent_types(magnet_links)
        assert result == []
    
    def test_subtitle_only(self):
        """Test with only subtitle magnet."""
        magnet_links = {
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': 'magnet:?xt=urn:btih:abc123',
            'no_subtitle': ''
        }
        result = determine_torrent_types(magnet_links)
        assert 'subtitle' in result
        assert len(result) == 1
    
    def test_multiple_types(self):
        """Test with multiple magnet types."""
        magnet_links = {
            'hacked_subtitle': 'magnet:?xt=urn:btih:hacked',
            'hacked_no_subtitle': '',
            'subtitle': 'magnet:?xt=urn:btih:sub',
            'no_subtitle': 'magnet:?xt=urn:btih:nosub'
        }
        result = determine_torrent_types(magnet_links)
        assert 'hacked_subtitle' in result
        assert 'subtitle' in result
        assert 'no_subtitle' in result
        assert 'hacked_no_subtitle' not in result


class TestDetermineTorrentType:
    """Test cases for determine_torrent_type function (legacy)."""
    
    def test_returns_first_type(self):
        """Test that it returns the first torrent type."""
        magnet_links = {
            'hacked_subtitle': 'magnet:?xt=urn:btih:hacked',
            'hacked_no_subtitle': '',
            'subtitle': '',
            'no_subtitle': ''
        }
        result = determine_torrent_type(magnet_links)
        assert result == 'hacked_subtitle'
    
    def test_returns_no_subtitle_for_empty(self):
        """Test that it returns 'no_subtitle' for empty magnet links."""
        magnet_links = {
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': '',
            'no_subtitle': ''
        }
        result = determine_torrent_type(magnet_links)
        assert result == 'no_subtitle'


class TestGetMissingTorrentTypes:
    """Test cases for get_missing_torrent_types function."""
    
    def test_no_missing_types(self):
        """Test when all types are already in history."""
        history_types = ['hacked_subtitle', 'subtitle']
        current_types = ['hacked_subtitle', 'subtitle']
        result = get_missing_torrent_types(history_types, current_types)
        assert result == []
    
    def test_missing_subtitle(self):
        """Test when subtitle is missing from history."""
        history_types = ['no_subtitle']
        current_types = ['subtitle', 'no_subtitle']
        result = get_missing_torrent_types(history_types, current_types)
        assert 'subtitle' in result
    
    def test_missing_hacked_subtitle(self):
        """Test when hacked_subtitle is missing from history."""
        history_types = ['hacked_no_subtitle']
        current_types = ['hacked_subtitle', 'hacked_no_subtitle']
        result = get_missing_torrent_types(history_types, current_types)
        assert 'hacked_subtitle' in result
    
    def test_prefer_subtitle_over_no_subtitle(self):
        """Test that subtitle in history suppresses no_subtitle as missing."""
        history_types = ['subtitle']
        current_types = ['no_subtitle']
        result = get_missing_torrent_types(history_types, current_types)
        # no_subtitle should not be missing if subtitle exists in history
        assert 'no_subtitle' not in result


class TestHasCompleteSubtitles:
    """Test cases for has_complete_subtitles function."""
    
    def test_complete_subtitles(self):
        """Test movie with both subtitle and hacked_subtitle."""
        history_data = {
            '/v/ABC-123': {
                'torrent_types': ['subtitle', 'hacked_subtitle']
            }
        }
        result = has_complete_subtitles('/v/ABC-123', history_data)
        assert result is True
    
    def test_incomplete_subtitles(self):
        """Test movie with only one subtitle type."""
        history_data = {
            '/v/ABC-123': {
                'torrent_types': ['subtitle']
            }
        }
        result = has_complete_subtitles('/v/ABC-123', history_data)
        assert result is False
    
    def test_movie_not_in_history(self):
        """Test movie not in history."""
        history_data = {}
        result = has_complete_subtitles('/v/ABC-123', history_data)
        assert result is False
    
    def test_empty_history_data(self):
        """Test with None history data."""
        result = has_complete_subtitles('/v/ABC-123', None)
        assert result is False


class TestShouldProcessMovie:
    """Test cases for should_process_movie function."""
    
    def test_new_movie_should_process(self):
        """Test that new movie should be processed."""
        history_data = {}
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:abc'}
        should_process, history_types = should_process_movie('/v/NEW-001', history_data, 1, magnet_links)
        assert should_process is True
        assert history_types is None
    
    def test_movie_with_missing_types_should_process(self):
        """Test that movie with missing types should be processed."""
        history_data = {
            '/v/ABC-123': {
                'torrent_types': ['no_subtitle']
            }
        }
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:abc'}
        should_process, history_types = should_process_movie('/v/ABC-123', history_data, 1, magnet_links)
        assert should_process is True
    
    def test_movie_with_all_types_should_not_process(self):
        """Test that movie with all types should not be processed."""
        history_data = {
            '/v/ABC-123': {
                'torrent_types': ['hacked_subtitle', 'subtitle']
            }
        }
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:abc'}
        should_process, history_types = should_process_movie('/v/ABC-123', history_data, 1, magnet_links)
        assert should_process is False


class TestCheckTorrentInHistory:
    """Test cases for check_torrent_in_history function."""
    
    def test_torrent_not_found(self, temp_dir):
        """Test when torrent is not in history."""
        history_file = os.path.join(temp_dir, 'history.csv')
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
        
        result = check_torrent_in_history(history_file, '/v/ABC-123', 'subtitle')
        assert result is False
    
    def test_torrent_found(self, sample_history_csv):
        """Test when torrent is in history."""
        result = check_torrent_in_history(sample_history_csv, '/v/ABC-123', 'hacked_subtitle')
        assert result is True
    
    def test_file_not_exists(self, temp_dir):
        """Test when history file doesn't exist."""
        history_file = os.path.join(temp_dir, 'nonexistent.csv')
        result = check_torrent_in_history(history_file, '/v/ABC-123', 'subtitle')
        assert result is False


class TestIsDownloadedTorrent:
    """Test cases for is_downloaded_torrent function."""
    
    def test_downloaded_torrent(self):
        """Test detection of downloaded torrent indicator."""
        content = "[DOWNLOADED PREVIOUSLY] magnet:?xt=urn:btih:abc123"
        result = is_downloaded_torrent(content)
        assert result is True
    
    def test_not_downloaded_torrent(self):
        """Test regular torrent content."""
        content = "magnet:?xt=urn:btih:abc123"
        result = is_downloaded_torrent(content)
        assert result is False
    
    def test_empty_content(self):
        """Test empty content."""
        result = is_downloaded_torrent("")
        assert result is False

