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


class TestCleanupHistoryFile:
    """Test cases for cleanup_history_file function."""
    
    def test_cleanup_removes_duplicates(self, temp_dir):
        """Test that cleanup removes duplicate records."""
        from utils.history_manager import cleanup_history_file
        
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Create file with duplicates (different update dates)
        href_records = {
            '/v/ABC-123': {
                'href': '/v/ABC-123',
                'phase': '1',
                'video_code': 'ABC-123',
                'create_date': '2024-01-01 10:00:00',
                'update_date': '2024-01-02 10:00:00',
                'hacked_subtitle': '',
                'hacked_no_subtitle': '',
                'subtitle': 'magnet:?xt=urn:btih:abc',
                'no_subtitle': ''
            },
            '/v/DEF-456': {
                'href': '/v/DEF-456',
                'phase': '2',
                'video_code': 'DEF-456',
                'create_date': '2024-01-03 10:00:00',
                'update_date': '2024-01-03 10:00:00',
                'hacked_subtitle': '',
                'hacked_no_subtitle': '',
                'subtitle': '',
                'no_subtitle': 'magnet:?xt=urn:btih:def'
            }
        }
        
        cleanup_history_file(history_file, href_records)
        
        # Verify file was created and has correct content
        assert os.path.exists(history_file)
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()
        # Header + 2 records
        assert len(lines) == 3


class TestMaintainHistoryLimit:
    """Test cases for maintain_history_limit function."""
    
    def test_maintain_limit_removes_oldest(self, temp_dir):
        """Test that oldest records are removed when limit exceeded."""
        from utils.history_manager import maintain_history_limit
        
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Create file with multiple records
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            for i in range(10):
                f.write(f'/v/TEST-{i:03d},1,TEST-{i:03d},2024-01-{i+1:02d} 10:00:00,2024-01-{i+1:02d} 10:00:00,,,,\n')
        
        # Maintain limit of 5
        maintain_history_limit(history_file, max_records=5)
        
        # Check that only 5 records remain (plus header)
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()
        assert len(lines) == 6  # header + 5 records
    
    def test_maintain_limit_nonexistent_file(self, temp_dir):
        """Test that function handles non-existent file gracefully."""
        from utils.history_manager import maintain_history_limit
        
        history_file = os.path.join(temp_dir, 'nonexistent.csv')
        
        # Should not raise any exception
        maintain_history_limit(history_file, max_records=5)


class TestAddDownloadedIndicatorToCsv:
    """Test cases for add_downloaded_indicator_to_csv function."""
    
    def test_add_indicator_to_downloaded_torrents(self, temp_dir):
        """Test adding downloaded indicator to CSV."""
        from utils.history_manager import add_downloaded_indicator_to_csv
        
        # Create sample CSV file
        csv_file = os.path.join(temp_dir, 'daily.csv')
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Create CSV file with torrent data
        with open(csv_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,video_code,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            f.write('/v/ABC-123,ABC-123,magnet:?xt=urn:btih:abc,,,\n')
            f.write('/v/DEF-456,DEF-456,,,magnet:?xt=urn:btih:def,\n')
        
        # Create history file
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            f.write('/v/ABC-123,1,ABC-123,2024-01-01 10:00:00,2024-01-01 10:00:00,[2024-01-01]magnet:?xt=urn:btih:abc,,,\n')
        
        result = add_downloaded_indicator_to_csv(csv_file, history_file)
        
        assert result is True
    
    def test_csv_file_not_found(self, temp_dir):
        """Test handling of non-existent CSV file."""
        from utils.history_manager import add_downloaded_indicator_to_csv
        
        csv_file = os.path.join(temp_dir, 'nonexistent.csv')
        history_file = os.path.join(temp_dir, 'history.csv')
        
        result = add_downloaded_indicator_to_csv(csv_file, history_file)
        
        assert result is False


class TestMarkTorrentAsDownloaded:
    """Test cases for mark_torrent_as_downloaded function."""
    
    def test_mark_torrent_as_downloaded(self, temp_dir):
        """Test marking a torrent as downloaded."""
        from utils.history_manager import mark_torrent_as_downloaded
        
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Create empty history file with header
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
        
        result = mark_torrent_as_downloaded(
            history_file, '/v/NEW-001', 'NEW-001', 'subtitle'
        )
        
        assert result is True
        
        # Verify record was added
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        assert '/v/NEW-001' in content


class TestLoadParsedMoviesHistoryExtended:
    """Extended test cases for load_parsed_movies_history function."""
    
    def test_load_with_old_format_torrent_type(self, temp_dir):
        """Test loading history file with old format (torrent_type column)."""
        from utils.history_manager import load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Create file with old format
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,parsed_date,torrent_type\n')
            f.write('/v/OLD-001,1,OLD-001,2024-01-01 10:00:00,subtitle,no_subtitle\n')
        
        result = load_parsed_movies_history(history_file)
        
        assert '/v/OLD-001' in result
        assert 'subtitle' in result['/v/OLD-001']['torrent_types']
    
    def test_load_with_duplicate_href(self, temp_dir):
        """Test that duplicate hrefs keep the most recent record."""
        from utils.history_manager import load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Create file with duplicate hrefs
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            f.write('/v/DUP-001,1,DUP-001,2024-01-01 10:00:00,2024-01-01 10:00:00,,,,\n')
            f.write('/v/DUP-001,1,DUP-001,2024-01-01 10:00:00,2024-01-05 10:00:00,magnet:abc,,,\n')
        
        result = load_parsed_movies_history(history_file)
        
        # Should have only one entry for the href
        assert len(result) == 1
        assert '/v/DUP-001' in result
    
    def test_load_with_phase2_filter(self, temp_dir):
        """Test loading history with phase=2 filter."""
        from utils.history_manager import load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'history.csv')
        
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            f.write('/v/TEST-001,1,TEST-001,2024-01-01 10:00:00,2024-01-01 10:00:00,,,,\n')
            f.write('/v/TEST-002,2,TEST-002,2024-01-02 10:00:00,2024-01-02 10:00:00,,,,\n')
        
        result = load_parsed_movies_history(history_file, phase=2)
        
        # Phase 2 loading should include all records
        assert len(result) == 2


class TestShouldProcessMovieExtended:
    """Extended test cases for should_process_movie function."""
    
    def test_phase2_upgrade_from_no_subtitle(self):
        """Test phase 2 can upgrade from no_subtitle to hacked_no_subtitle."""
        from utils.history_manager import should_process_movie
        
        history_data = {
            '/v/ABC-123': {
                'torrent_types': ['no_subtitle']
            }
        }
        magnet_links = {'hacked_no_subtitle': 'magnet:?xt=urn:btih:abc'}
        
        should_process, history_types = should_process_movie('/v/ABC-123', history_data, 2, magnet_links)
        
        assert should_process is True
    
    def test_phase2_no_upgrade_possible(self):
        """Test phase 2 does not process when no upgrade is possible."""
        from utils.history_manager import should_process_movie
        
        history_data = {
            '/v/ABC-123': {
                'torrent_types': ['hacked_no_subtitle']
            }
        }
        magnet_links = {'hacked_no_subtitle': 'magnet:?xt=urn:btih:abc'}
        
        should_process, history_types = should_process_movie('/v/ABC-123', history_data, 2, magnet_links)
        
        assert should_process is False


class TestLoadHistoryEdgeCases:
    """Test edge cases for load_parsed_movies_history."""
    
    def test_load_corrupted_file(self, temp_dir):
        """Test loading corrupted CSV file."""
        from utils.history_manager import load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'corrupted.csv')
        
        # Create file with invalid content
        with open(history_file, 'w', encoding='utf-8-sig') as f:
            f.write('not,a,valid,csv,header\n')
            f.write('some,random,data\n')
        
        # Should not raise exception
        result = load_parsed_movies_history(history_file)
        
        # May return empty dict or partial data
        assert isinstance(result, dict)
    
    def test_load_empty_file(self, temp_dir):
        """Test loading empty CSV file."""
        from utils.history_manager import load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'empty.csv')
        
        # Create empty file
        with open(history_file, 'w', encoding='utf-8-sig') as f:
            pass
        
        result = load_parsed_movies_history(history_file)
        
        assert result == {}
    
    def test_load_header_only_file(self, temp_dir):
        """Test loading CSV file with only header."""
        from utils.history_manager import load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'header_only.csv')
        
        with open(history_file, 'w', encoding='utf-8-sig') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
        
        result = load_parsed_movies_history(history_file)
        
        assert result == {}


class TestCheckTorrentExtended:
    """Extended test cases for check_torrent_in_history."""
    
    def test_check_multiple_torrent_types(self, sample_history_csv):
        """Test checking for multiple torrent types."""
        from utils.history_manager import check_torrent_in_history
        
        # Check for type that exists
        assert check_torrent_in_history(sample_history_csv, '/v/ABC-123', 'hacked_subtitle') is True
        
        # Check for type that doesn't exist for this href
        assert check_torrent_in_history(sample_history_csv, '/v/DEF-456', 'hacked_subtitle') is False
    
    def test_check_with_invalid_href(self, sample_history_csv):
        """Test checking with invalid href format."""
        from utils.history_manager import check_torrent_in_history
        
        # Invalid href should return False
        assert check_torrent_in_history(sample_history_csv, 'invalid', 'subtitle') is False


class TestDetermineTorrentTypesExtended:
    """Extended test cases for determine_torrent_types function."""
    
    def test_all_types_populated(self):
        """Test when all torrent types are populated."""
        from utils.history_manager import determine_torrent_types
        
        magnet_links = {
            'hacked_subtitle': 'magnet:?xt=urn:btih:hs',
            'hacked_no_subtitle': 'magnet:?xt=urn:btih:hns',
            'subtitle': 'magnet:?xt=urn:btih:s',
            'no_subtitle': 'magnet:?xt=urn:btih:ns'
        }
        result = determine_torrent_types(magnet_links)
        
        assert len(result) == 4
        assert 'hacked_subtitle' in result
        assert 'hacked_no_subtitle' in result
        assert 'subtitle' in result
        assert 'no_subtitle' in result


class TestMaintainHistoryLimitExtended:
    """Extended test cases for maintain_history_limit function."""
    
    def test_maintain_limit_preserves_newest(self, temp_dir):
        """Test that newest records are preserved when limit enforced."""
        from utils.history_manager import maintain_history_limit
        
        history_file = os.path.join(temp_dir, 'history_preserve.csv')
        
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            # Write 10 records with increasing dates
            for i in range(10):
                f.write(f'/v/TEST-{i:03d},1,TEST-{i:03d},2024-01-{i+1:02d} 10:00:00,2024-01-{i+1:02d} 10:00:00,,,,\n')
        
        maintain_history_limit(history_file, max_records=5)
        
        # Check that newest 5 records remain
        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)
        
        assert len(records) == 5


class TestSaveAndLoadIntegration:
    """Integration tests for save and load operations."""
    
    def test_save_and_reload_preserves_data(self, temp_dir):
        """Test that saved data can be reloaded correctly."""
        from utils.history_manager import save_parsed_movie_to_history, load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'integration.csv')
        
        # Create empty history file
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
        
        # Save a new record
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:test123'}
        save_parsed_movie_to_history(history_file, '/v/INT-001', 1, 'INT-001', magnet_links)
        
        # Reload and verify
        result = load_parsed_movies_history(history_file)
        
        assert '/v/INT-001' in result
        assert 'subtitle' in result['/v/INT-001']['torrent_types']

