"""
Unit tests for utils/history_manager.py
Tests for history file management, torrent type detection, and processing rules
"""
import pytest
import os
import sys
import csv

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestLoadParsedMoviesHistory:
    """Tests for load_parsed_movies_history function"""
    
    def test_load_history_existing_file(self, sample_history_csv):
        """Test loading history from existing file"""
        from utils.history_manager import load_parsed_movies_history
        
        history = load_parsed_movies_history(sample_history_csv)
        
        assert len(history) == 2
        assert '/v/abc123' in history
        assert '/v/def456' in history
    
    def test_load_history_nonexistent_file(self, temp_dir):
        """Test loading history from non-existent file"""
        from utils.history_manager import load_parsed_movies_history
        
        missing_file = os.path.join(temp_dir, 'nonexistent.csv')
        history = load_parsed_movies_history(missing_file)
        
        assert history == {}
    
    def test_load_history_phase_filter_phase1(self, sample_history_csv):
        """Test loading history with phase 1 filter"""
        from utils.history_manager import load_parsed_movies_history
        
        history = load_parsed_movies_history(sample_history_csv, phase=1)
        
        # Phase 1 should exclude phase 2 records
        assert '/v/abc123' in history  # This is phase 1
        assert '/v/def456' not in history  # This is phase 2
    
    def test_load_history_phase_filter_phase2(self, sample_history_csv):
        """Test loading history with phase 2 filter (loads all)"""
        from utils.history_manager import load_parsed_movies_history
        
        # Phase 2 loads all history
        history = load_parsed_movies_history(sample_history_csv, phase=2)
        
        assert '/v/abc123' in history
        assert '/v/def456' in history
    
    def test_load_history_extracts_torrent_types(self, sample_history_csv):
        """Test that torrent types are correctly extracted"""
        from utils.history_manager import load_parsed_movies_history
        
        history = load_parsed_movies_history(sample_history_csv)
        
        # First record has subtitle
        assert 'subtitle' in history['/v/abc123']['torrent_types']
        
        # Second record has hacked_no_subtitle
        assert 'hacked_no_subtitle' in history['/v/def456']['torrent_types']


class TestDetermineTorrentTypes:
    """Tests for determine_torrent_types function"""
    
    def test_determine_types_with_subtitle(self):
        """Test determining types with subtitle magnet"""
        from utils.history_manager import determine_torrent_types
        
        magnet_links = {
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': 'magnet:?xt=urn:btih:abc123',
            'no_subtitle': ''
        }
        
        types = determine_torrent_types(magnet_links)
        assert 'subtitle' in types
        assert len(types) == 1
    
    def test_determine_types_multiple(self):
        """Test determining multiple types"""
        from utils.history_manager import determine_torrent_types
        
        magnet_links = {
            'hacked_subtitle': 'magnet:?xt=urn:btih:abc',
            'hacked_no_subtitle': '',
            'subtitle': 'magnet:?xt=urn:btih:def',
            'no_subtitle': ''
        }
        
        types = determine_torrent_types(magnet_links)
        assert 'hacked_subtitle' in types
        assert 'subtitle' in types
        assert len(types) == 2
    
    def test_determine_types_empty(self):
        """Test with no magnet links"""
        from utils.history_manager import determine_torrent_types
        
        magnet_links = {
            'hacked_subtitle': '',
            'hacked_no_subtitle': '',
            'subtitle': '',
            'no_subtitle': ''
        }
        
        types = determine_torrent_types(magnet_links)
        assert types == []


class TestGetMissingTorrentTypes:
    """Tests for get_missing_torrent_types function"""
    
    def test_missing_subtitle_when_only_no_subtitle_in_history(self):
        """Test detecting missing subtitle when only no_subtitle in history"""
        from utils.history_manager import get_missing_torrent_types
        
        history_types = ['no_subtitle']
        current_types = ['subtitle', 'no_subtitle']
        
        missing = get_missing_torrent_types(history_types, current_types)
        assert 'subtitle' in missing
    
    def test_missing_hacked_subtitle_when_only_hacked_no_subtitle_in_history(self):
        """Test detecting missing hacked_subtitle"""
        from utils.history_manager import get_missing_torrent_types
        
        history_types = ['hacked_no_subtitle']
        current_types = ['hacked_subtitle', 'hacked_no_subtitle']
        
        missing = get_missing_torrent_types(history_types, current_types)
        assert 'hacked_subtitle' in missing
    
    def test_no_missing_types_when_preferred_exists(self):
        """Test that no missing types when preferred version exists"""
        from utils.history_manager import get_missing_torrent_types
        
        history_types = ['subtitle', 'hacked_subtitle']
        current_types = ['subtitle', 'no_subtitle', 'hacked_subtitle', 'hacked_no_subtitle']
        
        missing = get_missing_torrent_types(history_types, current_types)
        # Should not suggest no_subtitle or hacked_no_subtitle since subtitle and hacked_subtitle exist
        assert 'no_subtitle' not in missing
        assert 'hacked_no_subtitle' not in missing
    
    def test_empty_history(self):
        """Test with empty history"""
        from utils.history_manager import get_missing_torrent_types
        
        history_types = []
        current_types = ['subtitle', 'no_subtitle']
        
        missing = get_missing_torrent_types(history_types, current_types)
        # With empty history, should suggest subtitle (preferred over no_subtitle)
        assert 'subtitle' in missing


class TestShouldProcessMovie:
    """Tests for should_process_movie function"""
    
    def test_should_process_new_movie(self):
        """Test that new movies should be processed"""
        from utils.history_manager import should_process_movie
        
        history_data = {}
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:abc123'}
        
        should_process, history_types = should_process_movie('/v/new_movie', history_data, 1, magnet_links)
        
        assert should_process is True
        assert history_types is None
    
    def test_should_process_movie_with_new_torrent_type(self):
        """Test processing movie with new torrent type not in history"""
        from utils.history_manager import should_process_movie
        
        history_data = {
            '/v/existing': {
                'torrent_types': ['no_subtitle'],
                'phase': '1'
            }
        }
        magnet_links = {
            'subtitle': 'magnet:?xt=urn:btih:abc123',
            'no_subtitle': 'magnet:?xt=urn:btih:def456'
        }
        
        should_process, history_types = should_process_movie('/v/existing', history_data, 1, magnet_links)
        
        # Should process because subtitle is available but not in history
        assert should_process is True
    
    def test_should_not_process_when_all_types_in_history(self):
        """Test not processing when all torrent types are in history"""
        from utils.history_manager import should_process_movie
        
        history_data = {
            '/v/existing': {
                'torrent_types': ['subtitle', 'hacked_subtitle'],
                'phase': '1'
            }
        }
        magnet_links = {
            'subtitle': 'magnet:?xt=urn:btih:abc123',
            'hacked_subtitle': 'magnet:?xt=urn:btih:def456'
        }
        
        should_process, history_types = should_process_movie('/v/existing', history_data, 1, magnet_links)
        
        # Should not process because all preferred types are in history
        assert should_process is False


class TestIsDownloadedTorrent:
    """Tests for is_downloaded_torrent function"""
    
    def test_is_downloaded_torrent_true(self):
        """Test detection of downloaded torrent marker"""
        from utils.history_manager import is_downloaded_torrent
        
        assert is_downloaded_torrent("[DOWNLOADED PREVIOUSLY]") is True
        assert is_downloaded_torrent("[DOWNLOADED PREVIOUSLY] extra text") is True
    
    def test_is_downloaded_torrent_false(self):
        """Test that magnet links are not detected as downloaded"""
        from utils.history_manager import is_downloaded_torrent
        
        assert is_downloaded_torrent("magnet:?xt=urn:btih:abc123") is False
        assert is_downloaded_torrent("") is False
        assert is_downloaded_torrent("   ") is False


class TestCheckTorrentInHistory:
    """Tests for check_torrent_in_history function"""
    
    def test_check_torrent_in_history_found(self, sample_history_csv):
        """Test checking for existing torrent in history"""
        from utils.history_manager import check_torrent_in_history
        
        # The first record has subtitle
        result = check_torrent_in_history(sample_history_csv, '/v/abc123', 'subtitle')
        assert result is True
    
    def test_check_torrent_in_history_not_found(self, sample_history_csv):
        """Test checking for non-existent torrent"""
        from utils.history_manager import check_torrent_in_history
        
        # The first record doesn't have hacked_subtitle
        result = check_torrent_in_history(sample_history_csv, '/v/abc123', 'hacked_subtitle')
        assert result is False
    
    def test_check_torrent_in_history_missing_file(self, temp_dir):
        """Test checking in non-existent history file"""
        from utils.history_manager import check_torrent_in_history
        
        missing_file = os.path.join(temp_dir, 'nonexistent.csv')
        result = check_torrent_in_history(missing_file, '/v/test', 'subtitle')
        assert result is False


class TestSaveParsedMovieToHistory:
    """Tests for save_parsed_movie_to_history function"""
    
    def test_save_new_movie(self, temp_dir):
        """Test saving a new movie to history"""
        from utils.history_manager import save_parsed_movie_to_history, load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'test_history.csv')
        
        # Create empty history file
        with open(history_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'href', 'phase', 'video_code', 'create_date', 'update_date',
                'hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'
            ])
            writer.writeheader()
        
        # Save new movie
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:newmovie'}
        save_parsed_movie_to_history(history_file, '/v/newmovie', '1', 'NEW-001', magnet_links)
        
        # Verify
        history = load_parsed_movies_history(history_file)
        assert '/v/newmovie' in history
        assert 'subtitle' in history['/v/newmovie']['torrent_types']
    
    def test_update_existing_movie(self, sample_history_csv):
        """Test updating an existing movie in history"""
        from utils.history_manager import save_parsed_movie_to_history, load_parsed_movies_history
        
        # Add hacked_subtitle to existing movie
        magnet_links = {'hacked_subtitle': 'magnet:?xt=urn:btih:hacked'}
        save_parsed_movie_to_history(sample_history_csv, '/v/abc123', '1', 'ABC-123', magnet_links)
        
        # Verify update
        history = load_parsed_movies_history(sample_history_csv)
        assert '/v/abc123' in history
        assert 'hacked_subtitle' in history['/v/abc123']['torrent_types']


class TestValidateHistoryFile:
    """Tests for validate_history_file function"""
    
    def test_validate_existing_file(self, sample_history_csv):
        """Test validating existing history file"""
        from utils.history_manager import validate_history_file
        
        result = validate_history_file(sample_history_csv)
        assert result is True
    
    def test_validate_nonexistent_file(self, temp_dir):
        """Test validating non-existent file returns True"""
        from utils.history_manager import validate_history_file
        
        missing_file = os.path.join(temp_dir, 'nonexistent.csv')
        result = validate_history_file(missing_file)
        # Non-existent file is considered valid (will be created)
        assert result is True
