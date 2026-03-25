"""
Unit tests for utils/history_manager.py functions.
"""
import os
import sys
import csv
import pytest

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from utils.history_manager import (
    load_parsed_movies_history,
    save_parsed_movie_to_history,
    determine_torrent_types,
    determine_torrent_type,
    get_missing_torrent_types,
    has_complete_subtitles,
    should_skip_recent_yesterday_release,
    should_skip_recent_today_release,
    batch_update_last_visited,
    should_process_movie,
    check_torrent_in_history,
    is_downloaded_torrent,
)
import utils.infra.db as db_mod


def _seed_history_sqlite(records):
    """Seed the isolated SQLite DB with history records."""
    for r in records:
        magnets = {}
        for t in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
            if r.get(t):
                magnets[t] = r[t]
        if not magnets:
            magnets = {'no_subtitle': ''}
        db_mod.db_upsert_history(r['href'], r['video_code'], magnet_links=magnets)


_RECENT_RELEASE_SKIP_FUNCS = [
    pytest.param(should_skip_recent_yesterday_release, id='yesterday'),
    pytest.param(should_skip_recent_today_release, id='today'),
]


class TestLoadParsedMoviesHistory:
    """Test cases for load_parsed_movies_history function."""
    
    def test_load_nonexistent_file(self, temp_dir):
        """Test loading from non-existent file returns empty dict."""
        history_file = os.path.join(temp_dir, 'nonexistent.csv')
        result = load_parsed_movies_history(history_file)
        assert result == {}
    
    def test_load_with_phase_filter(self, sample_history_csv):
        """Test loading history with phase filter."""
        _seed_history_sqlite([
            {'href': '/v/ABC-123', 'phase': 1, 'video_code': 'ABC-123',
             'hacked_subtitle': 'magnet:?xt=urn:btih:abc123'},
            {'href': '/v/DEF-456', 'phase': 2, 'video_code': 'DEF-456',
             'subtitle': 'magnet:?xt=urn:btih:def456'},
        ])
        result = load_parsed_movies_history(sample_history_csv, phase=1)
        assert '/v/ABC-123' in result
        
    def test_load_all_phases(self, sample_history_csv):
        """Test loading history without phase filter."""
        _seed_history_sqlite([
            {'href': '/v/ABC-123', 'phase': 1, 'video_code': 'ABC-123',
             'hacked_subtitle': 'magnet:?xt=urn:btih:abc123'},
            {'href': '/v/DEF-456', 'phase': 2, 'video_code': 'DEF-456',
             'subtitle': 'magnet:?xt=urn:btih:def456'},
        ])
        result = load_parsed_movies_history(sample_history_csv, phase=None)
        assert len(result) == 2
        assert '/v/ABC-123' in result
        assert '/v/DEF-456' in result


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


class TestShouldSkipRecentYesterdayRelease:
    """Test cases for should_skip_recent_yesterday_release function."""

    def test_yesterday_release_recently_visited_should_skip(self):
        """Yesterday release + visited today → skip."""
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        history_data = {
            '/v/ABC-123': {'last_visited_datetime': today, 'update_datetime': today, 'torrent_types': ['subtitle']}
        }
        assert should_skip_recent_yesterday_release('/v/ABC-123', history_data, True) is True

    def test_yesterday_release_visited_yesterday_should_skip(self):
        """Yesterday release + visited yesterday → skip."""
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        history_data = {
            '/v/ABC-123': {'last_visited_datetime': yesterday, 'update_datetime': yesterday, 'torrent_types': ['no_subtitle']}
        }
        assert should_skip_recent_yesterday_release('/v/ABC-123', history_data, True) is True

    def test_yesterday_release_old_visit_should_not_skip(self):
        """Yesterday release + visited 3 days ago → do not skip."""
        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
        history_data = {
            '/v/ABC-123': {'last_visited_datetime': old_date, 'update_datetime': old_date, 'torrent_types': ['subtitle']}
        }
        assert should_skip_recent_yesterday_release('/v/ABC-123', history_data, True) is False

    def test_today_release_recently_visited_should_not_skip(self):
        """Today release (is_yesterday_release=False) + recent visit → do not skip."""
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        history_data = {
            '/v/ABC-123': {'last_visited_datetime': today, 'update_datetime': today, 'torrent_types': ['subtitle']}
        }
        assert should_skip_recent_yesterday_release('/v/ABC-123', history_data, False) is False


class TestShouldSkipRecentTodayRelease:
    """Test cases for should_skip_recent_today_release function."""

    def test_today_release_visited_today_should_skip(self):
        """Today release + visited today -> skip."""
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        history_data = {
            '/v/ABC-123': {'last_visited_datetime': today, 'update_datetime': today, 'torrent_types': ['subtitle']}
        }
        assert should_skip_recent_today_release('/v/ABC-123', history_data, True) is True

    def test_today_release_visited_yesterday_should_not_skip(self):
        """Today release + visited yesterday -> do not skip."""
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        history_data = {
            '/v/ABC-123': {'last_visited_datetime': yesterday, 'update_datetime': yesterday, 'torrent_types': ['subtitle']}
        }
        assert should_skip_recent_today_release('/v/ABC-123', history_data, True) is False

    def test_yesterday_release_flag_should_not_skip(self):
        """is_today_release=False + recent visit -> do not skip."""
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        history_data = {
            '/v/ABC-123': {'last_visited_datetime': today, 'update_datetime': today, 'torrent_types': ['subtitle']}
        }
        assert should_skip_recent_today_release('/v/ABC-123', history_data, False) is False

    def test_today_release_visited_old_should_not_skip(self):
        """Today release + visited 3 days ago -> do not skip."""
        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
        history_data = {
            '/v/ABC-123': {'last_visited_datetime': old_date, 'update_datetime': old_date, 'torrent_types': ['subtitle']}
        }
        assert should_skip_recent_today_release('/v/ABC-123', history_data, True) is False


@pytest.mark.parametrize("skip_fn", _RECENT_RELEASE_SKIP_FUNCS)
def test_recent_release_skip_returns_false_without_history(skip_fn):
    """Missing history inputs should not skip regardless of release flavor."""
    assert skip_fn('/v/NEW-001', {}, True) is False
    assert skip_fn('/v/ABC-123', None, True) is False


@pytest.mark.parametrize("skip_fn", _RECENT_RELEASE_SKIP_FUNCS)
def test_recent_release_skip_respects_release_flag(skip_fn):
    """A recent visit should not skip when the release flag is disabled."""
    from datetime import datetime

    today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    history_data = {
        '/v/ABC-123': {'last_visited_datetime': today, 'update_datetime': today, 'torrent_types': ['subtitle']}
    }
    assert skip_fn('/v/ABC-123', history_data, False) is False


@pytest.mark.parametrize("skip_fn", _RECENT_RELEASE_SKIP_FUNCS)
def test_recent_release_skip_ignores_empty_visit_timestamps(skip_fn):
    """Empty visit timestamps should not trigger skip behavior."""
    history_data = {
        '/v/ABC-123': {'last_visited_datetime': '', 'update_datetime': '', 'torrent_types': ['subtitle']}
    }
    assert skip_fn('/v/ABC-123', history_data, True) is False


@pytest.mark.parametrize("skip_fn", _RECENT_RELEASE_SKIP_FUNCS)
def test_recent_release_skip_falls_back_to_update_datetime(skip_fn):
    """Both recent-release helpers should fall back to update_datetime."""
    from datetime import datetime

    today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    history_data = {
        '/v/ABC-123': {'last_visited_datetime': '', 'update_datetime': today, 'torrent_types': ['subtitle']}
    }
    assert skip_fn('/v/ABC-123', history_data, True) is True


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
    
    def test_cleanup_is_noop_in_sqlite(self, temp_dir):
        """In SQLite mode, cleanup is a no-op (dedup handled by UPSERT)."""
        from utils.history_manager import cleanup_history_file
        
        history_file = os.path.join(temp_dir, 'history.csv')
        href_records = {'/v/ABC-123': {}, '/v/DEF-456': {}}
        
        cleanup_history_file(history_file, href_records)
        # No CSV created in SQLite mode
        assert not os.path.exists(history_file)


class TestMaintainHistoryLimit:
    """Test cases for maintain_history_limit function."""
    
    def test_maintain_limit_is_noop_in_sqlite(self, temp_dir):
        """In SQLite mode, maintain_limit is a no-op (no record limit needed)."""
        from utils.history_manager import maintain_history_limit
        
        history_file = os.path.join(temp_dir, 'history.csv')
        
        # Seed SQLite with 10 records
        for i in range(10):
            db_mod.db_upsert_history(f'/v/TEST-{i:03d}', f'TEST-{i:03d}')
        
        maintain_history_limit(history_file, max_records=5)
        
        # All 10 records should still exist (no limit in SQLite)
        history = db_mod.db_load_history()
        assert len(history) == 10
    
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
            f.write('href,phase,video_code,create_datetime,update_datetime,last_visited_datetime,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            f.write('/v/ABC-123,1,ABC-123,2024-01-01 10:00:00,2024-01-01 10:00:00,2024-01-01 10:00:00,[2024-01-01]magnet:?xt=urn:btih:abc,,,\n')
        
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
        from utils.history_manager import mark_torrent_as_downloaded, check_torrent_in_history
        
        history_file = os.path.join(temp_dir, 'history.csv')
        
        result = mark_torrent_as_downloaded(
            history_file, '/v/NEW-001', 'NEW-001', 'subtitle'
        )
        
        assert result is True
        
        history = db_mod.db_load_history()
        assert '/v/NEW-001' in history
        
        assert check_torrent_in_history(history_file, '/v/NEW-001', 'subtitle') is True


class TestLoadParsedMoviesHistoryExtended:
    """Extended test cases for load_parsed_movies_history function."""
    
    def test_load_with_torrent_types(self, temp_dir):
        """Test loading history and checking torrent_types field."""
        _seed_history_sqlite([
            {'href': '/v/OLD-001', 'phase': 1, 'video_code': 'OLD-001',
             'subtitle': 'magnet:?xt=urn:btih:sub'},
        ])
        history_file = os.path.join(temp_dir, 'history.csv')
        
        result = load_parsed_movies_history(history_file)
        
        assert '/v/OLD-001' in result
        assert 'subtitle' in result['/v/OLD-001']['torrent_types']
    
    def test_load_dedup_by_href(self, temp_dir):
        """Test that SQLite UPSERT keeps only one entry per href."""
        db_mod.db_upsert_history('/v/DUP-001', 'DUP-001')
        db_mod.db_upsert_history('/v/DUP-001', 'DUP-001',
                                  magnet_links={'hacked_subtitle': 'magnet:abc'})
        
        history_file = os.path.join(temp_dir, 'history.csv')
        result = load_parsed_movies_history(history_file)
        
        assert len(result) == 1
        assert '/v/DUP-001' in result
    
    def test_load_with_phase2_filter(self, temp_dir):
        """Test loading history with phase=2 filter."""
        _seed_history_sqlite([
            {'href': '/v/TEST-001', 'phase': 1, 'video_code': 'TEST-001'},
            {'href': '/v/TEST-002', 'phase': 2, 'video_code': 'TEST-002'},
        ])
        history_file = os.path.join(temp_dir, 'history.csv')
        
        result = load_parsed_movies_history(history_file, phase=2)
        
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
            f.write('href,phase,video_code,create_datetime,update_datetime,last_visited_datetime,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
        
        result = load_parsed_movies_history(history_file)
        
        assert result == {}


class TestCheckTorrentExtended:
    """Extended test cases for check_torrent_in_history."""
    
    def test_check_multiple_torrent_types(self, sample_history_csv):
        """Test checking for multiple torrent types."""
        from utils.history_manager import check_torrent_in_history
        
        _seed_history_sqlite([
            {'href': '/v/ABC-123', 'phase': 1, 'video_code': 'ABC-123',
             'hacked_subtitle': 'magnet:?xt=urn:btih:abc123'},
            {'href': '/v/DEF-456', 'phase': 2, 'video_code': 'DEF-456',
             'subtitle': 'magnet:?xt=urn:btih:def456'},
        ])
        
        assert check_torrent_in_history(sample_history_csv, '/v/ABC-123', 'hacked_subtitle') is True
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
    
    def test_maintain_limit_noop_sqlite(self, temp_dir):
        """SQLite mode: maintain_limit is a no-op, all records are preserved."""
        from utils.history_manager import maintain_history_limit
        
        for i in range(10):
            db_mod.db_upsert_history(f'/v/TEST-{i:03d}', f'TEST-{i:03d}')
        
        history_file = os.path.join(temp_dir, 'history_preserve.csv')
        maintain_history_limit(history_file, max_records=5)
        
        history = db_mod.db_load_history()
        assert len(history) == 10


class TestSaveAndLoadIntegration:
    """Integration tests for save and load operations."""
    
    def test_save_and_reload_preserves_data(self, temp_dir):
        """Test that saved data can be reloaded correctly."""
        from utils.history_manager import save_parsed_movie_to_history, load_parsed_movies_history
        
        history_file = os.path.join(temp_dir, 'integration.csv')
        
        magnet_links = {'subtitle': 'magnet:?xt=urn:btih:test123'}
        save_parsed_movie_to_history(history_file, '/v/INT-001', 1, 'INT-001', magnet_links)
        
        result = load_parsed_movies_history(history_file)
        
        assert '/v/INT-001' in result
        assert 'subtitle' in result['/v/INT-001']['torrent_types']


class TestBatchUpdateLastVisited:
    """Test cases for batch_update_last_visited function."""

    def test_updates_visited_hrefs(self, temp_dir):
        """Test that last_visited_datetime is updated for visited hrefs."""
        _seed_history_sqlite([
            {'href': '/v/ABC-123', 'phase': 1, 'video_code': 'ABC-123'},
            {'href': '/v/DEF-456', 'phase': 1, 'video_code': 'DEF-456'},
        ])
        history_file = os.path.join(temp_dir, 'history.csv')

        batch_update_last_visited(history_file, {'/v/ABC-123'})

        history = db_mod.db_load_history()
        # ABC-123 should have an updated timestamp (not the seed default)
        assert history['/v/ABC-123']['DateTimeVisited'] != ''

    def test_empty_visited_set(self, temp_dir):
        """Test that empty visited set is a no-op."""
        history_file = os.path.join(temp_dir, 'history.csv')
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_datetime,update_datetime,last_visited_datetime,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            f.write('/v/ABC-123,1,ABC-123,2024-01-01 10:00:00,2024-01-01 10:00:00,2024-01-01 10:00:00,,,,\n')

        batch_update_last_visited(history_file, set())

        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)

        assert records[0]['last_visited_datetime'] == '2024-01-01 10:00:00'

    def test_nonexistent_file(self, temp_dir):
        """Test that nonexistent file is handled gracefully."""
        history_file = os.path.join(temp_dir, 'nonexistent.csv')
        batch_update_last_visited(history_file, {'/v/ABC-123'})

    def test_unknown_hrefs_ignored(self, temp_dir):
        """Test that hrefs not in history are silently ignored."""
        history_file = os.path.join(temp_dir, 'history.csv')
        with open(history_file, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('href,phase,video_code,create_datetime,update_datetime,last_visited_datetime,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle\n')
            f.write('/v/ABC-123,1,ABC-123,2024-01-01 10:00:00,2024-01-01 10:00:00,2024-01-01 10:00:00,,,,\n')

        batch_update_last_visited(history_file, {'/v/UNKNOWN'})

        with open(history_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            records = list(reader)

        assert records[0]['last_visited_datetime'] == '2024-01-01 10:00:00'


# ── STORAGE_MODE tests ──────────────────────────────────────────────────

class TestStorageModeDb:
    """In db mode, writes go to SQLite only."""

    def test_save_writes_sqlite(self, temp_dir, storage_mode_db):
        hf = os.path.join(temp_dir, 'history.csv')
        save_parsed_movie_to_history(hf, '/v/SM-001', 1, 'SM-001',
                                     {'no_subtitle': 'magnet:?xt=urn:btih:sm1'})
        history = load_parsed_movies_history(hf)
        assert '/v/SM-001' in history
        assert not os.path.exists(hf)

    def test_batch_update_sqlite_only(self, temp_dir, storage_mode_db):
        save_parsed_movie_to_history('', '/v/SM-002', 1, 'SM-002')
        batch_update_last_visited('', {'/v/SM-002'})
        history = load_parsed_movies_history('')
        assert history['/v/SM-002']['DateTimeVisited'] != ''


class TestStorageModeCsv:
    """In csv mode, writes go to CSV only; SQLite reads return empty."""

    def test_save_writes_csv_only(self, temp_dir, storage_mode_csv):
        hf = os.path.join(temp_dir, 'history.csv')
        save_parsed_movie_to_history(hf, '/v/CSV-001', 1, 'CSV-001',
                                     {'no_subtitle': 'magnet:?xt=urn:btih:c1'})
        assert os.path.exists(hf)
        with open(hf, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert any(r['href'] == '/v/CSV-001' for r in rows)

    def test_load_reads_csv(self, temp_dir, storage_mode_csv):
        hf = os.path.join(temp_dir, 'history.csv')
        save_parsed_movie_to_history(hf, '/v/CSV-002', 1, 'CSV-002',
                                     {'no_subtitle': 'magnet:?xt=urn:btih:c2'})
        history = load_parsed_movies_history(hf)
        assert '/v/CSV-002' in history


class TestStorageModeDuo:
    """In duo mode, both SQLite and CSV are written."""

    def test_save_writes_both(self, temp_dir, storage_mode_duo):
        hf = os.path.join(temp_dir, 'history.csv')
        save_parsed_movie_to_history(hf, '/v/DUO-001', 1, 'DUO-001',
                                     {'no_subtitle': 'magnet:?xt=urn:btih:d1'})
        history_sqlite = db_mod.db_load_history()
        assert '/v/DUO-001' in history_sqlite
        assert os.path.exists(hf)
