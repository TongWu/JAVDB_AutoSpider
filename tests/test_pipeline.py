"""
Unit tests for pipeline.py
"""
import pytest
import os
import tempfile
from unittest.mock import Mock, patch, MagicMock

from pipeline import (
    mask_sensitive_info,
    get_log_summary,
    extract_spider_summary,
    analyze_spider_log,
    analyze_uploader_log,
    analyze_pikpak_log,
    extract_spider_statistics,
    extract_uploader_statistics,
    extract_pikpak_statistics,
    format_email_report,
    get_proxy_ban_summary
)


class TestMaskSensitiveInfo:
    """Tests for mask_sensitive_info function"""
    
    def test_mask_github_token(self):
        """Test masking GitHub personal access tokens"""
        text = "Using token ghp_1234567890abcdefghijklmnopqrstuvwxyz12345"
        masked = mask_sensitive_info(text)
        
        assert "ghp_***MASKED***" in masked
        assert "ghp_1234567890" not in masked
    
    def test_mask_smtp_password(self):
        """Test masking SMTP passwords"""
        text = "SMTP_PASSWORD=secret123"
        masked = mask_sensitive_info(text)
        
        assert "***MASKED***" in masked
        assert "secret123" not in masked
    
    def test_mask_email_password(self):
        """Test masking email passwords in URLs"""
        text = "user@example.com:password123@smtp.gmail.com"
        masked = mask_sensitive_info(text)
        
        assert "***MASKED***" in masked
        assert "password123" not in masked
    
    def test_dont_mask_github_url_password(self):
        """Test that GitHub URL passwords are handled correctly"""
        text = "https://user:ghp_token@github.com/repo"
        masked = mask_sensitive_info(text)
        
        assert "ghp_***MASKED***" in masked
    
    def test_mask_none_text(self):
        """Test masking None text"""
        masked = mask_sensitive_info(None)
        assert masked is None


class TestGetLogSummary:
    """Tests for get_log_summary function"""
    
    @pytest.fixture
    def temp_log_file(self):
        """Create temporary log file"""
        fd, path = tempfile.mkstemp(suffix='.log')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)
    
    def test_get_log_summary_existing_file(self, temp_log_file):
        """Test getting log summary from existing file"""
        # Write test log content
        with open(temp_log_file, 'w') as f:
            for i in range(250):
                f.write(f"Log line {i}\n")
        
        summary = get_log_summary(temp_log_file, lines=100)
        
        assert "Log line 150" in summary  # Should have last 100 lines
        assert "Log line 249" in summary
        assert "Log line 50" not in summary  # Should not have earlier lines
    
    def test_get_log_summary_nonexistent_file(self):
        """Test getting log summary from non-existent file"""
        summary = get_log_summary("/nonexistent/file.log")
        
        assert "Log file not found" in summary


class TestExtractSpiderSummary:
    """Tests for extract_spider_summary function"""
    
    @pytest.fixture
    def temp_log_file(self):
        """Create temporary log file"""
        fd, path = tempfile.mkstemp(suffix='.log')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)
    
    def test_extract_summary_section(self, temp_log_file):
        """Test extracting summary section from log"""
        log_content = """
Some random log content
=================================
SUMMARY REPORT
=================================
Total entries found: 100
Successfully processed: 95
=================================
PROXY POOL STATISTICS
=================================
Proxy stats here
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        summary = extract_spider_summary(temp_log_file)
        
        assert "SUMMARY REPORT" in summary
        assert "Total entries found: 100" in summary
        assert "PROXY POOL STATISTICS" not in summary
    
    def test_extract_summary_no_proxy_stats(self, temp_log_file):
        """Test extracting summary when no proxy stats section"""
        log_content = """
=================================
SUMMARY REPORT
=================================
Total entries found: 50
End of log
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        summary = extract_spider_summary(temp_log_file)
        
        assert "SUMMARY REPORT" in summary
        assert "Total entries found: 50" in summary


class TestAnalyzeSpiderLog:
    """Tests for analyze_spider_log function"""
    
    @pytest.fixture
    def temp_log_file(self):
        """Create temporary log file"""
        fd, path = tempfile.mkstemp(suffix='.log')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)
    
    def test_analyze_success_log(self, temp_log_file):
        """Test analyzing successful spider log"""
        log_content = """
Successfully fetched URL: https://javdb.com/page1
Total entries found: 100
Successfully processed: 95
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        is_critical, error_msg = analyze_spider_log(temp_log_file)
        
        assert is_critical is False
        assert error_msg is None
    
    def test_analyze_proxy_ban_detected(self, temp_log_file):
        """Test detecting proxy ban in log"""
        log_content = """
CRITICAL: PROXY BAN DETECTED DURING THIS RUN
Connection failed
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        is_critical, error_msg = analyze_spider_log(temp_log_file)
        
        assert is_critical is True
        assert "Proxy ban detected" in error_msg
    
    def test_analyze_no_movie_list(self, temp_log_file):
        """Test detecting no movie list errors"""
        log_content = """
[Page 1] No movie list found!
[Page 2] No movie list found!
[Page 3] No movie list found!
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        is_critical, error_msg = analyze_spider_log(temp_log_file)
        
        assert is_critical is True
        assert "movie list" in error_msg.lower()
    
    def test_analyze_missing_log_file(self):
        """Test analyzing missing log file"""
        is_critical, error_msg = analyze_spider_log("/nonexistent/file.log")
        
        assert is_critical is True
        assert "not found" in error_msg


class TestAnalyzeUploaderLog:
    """Tests for analyze_uploader_log function"""
    
    @pytest.fixture
    def temp_log_file(self):
        """Create temporary log file"""
        fd, path = tempfile.mkstemp(suffix='.log')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)
    
    def test_analyze_success_log(self, temp_log_file):
        """Test analyzing successful uploader log"""
        log_content = """
Starting to add torrents
Successfully added: 10
Failed to add: 0
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        is_critical, error_msg = analyze_uploader_log(temp_log_file)
        
        assert is_critical is False
        assert error_msg is None
    
    def test_analyze_connection_refused(self, temp_log_file):
        """Test detecting connection refused errors"""
        log_content = """
Cannot connect to qBittorrent
Connection refused
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        is_critical, error_msg = analyze_uploader_log(temp_log_file)
        
        assert is_critical is True
        assert "qBittorrent" in error_msg


class TestExtractStatistics:
    """Tests for statistics extraction functions"""
    
    @pytest.fixture
    def temp_log_file(self):
        """Create temporary log file"""
        fd, path = tempfile.mkstemp(suffix='.log')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)
    
    def test_extract_spider_statistics(self, temp_log_file):
        """Test extracting spider statistics"""
        log_content = """
[Page 1] Found 50 entries for phase 1
[Page 2] Found 30 entries for phase 1
Phase 1 completed: 80 entries processed
[Page 1] Found 20 entries for phase 2
Phase 2 completed: 20 entries processed
Total entries found: 100
Successfully processed: 95
Skipped already parsed in this session: 3
Skipped already parsed in previous runs: 2
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        stats = extract_spider_statistics(temp_log_file)
        
        assert stats['phase1']['found'] == 80
        assert stats['phase1']['processed'] == 80
        assert stats['phase2']['found'] == 20
        assert stats['overall']['total_found'] == 100
        assert stats['overall']['successfully_processed'] == 95
    
    def test_extract_uploader_statistics(self, temp_log_file):
        """Test extracting uploader statistics"""
        log_content = """
Total torrents found: 50
Successfully added: 45
Failed to add: 5
Hacked subtitle torrents: 10
Hacked no subtitle torrents: 15
Subtitle torrents: 10
No subtitle torrents: 10
Success rate: 90.0%
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        stats = extract_uploader_statistics(temp_log_file)
        
        assert stats['total'] == 50
        assert stats['success'] == 45
        assert stats['failed'] == 5
        assert stats['hacked_sub'] == 10
        assert stats['success_rate'] == 90.0
    
    def test_extract_pikpak_statistics(self, temp_log_file):
        """Test extracting PikPak statistics"""
        log_content = """
Found 100 torrents in qBittorrent
Filtered 20 torrents older than 3 days
Successfully added to PikPak: magnet:test1
Successfully added to PikPak: magnet:test2
Removed from qBittorrent: TEST-001
Removed from qBittorrent: TEST-002
Failed to add: TEST-003
"""
        with open(temp_log_file, 'w') as f:
            f.write(log_content)
        
        stats = extract_pikpak_statistics(temp_log_file)
        
        assert stats['total_torrents'] == 100
        assert stats['filtered_old'] == 20
        assert stats['added_to_pikpak'] == 2
        assert stats['removed_from_qb'] == 2
        assert stats['failed'] == 1


class TestFormatEmailReport:
    """Tests for format_email_report function"""
    
    def test_format_email_report(self):
        """Test formatting email report"""
        spider_stats = {
            'phase1': {'found': 50, 'processed': 48, 'skipped_session': 1, 'skipped_history': 1},
            'phase2': {'found': 30, 'processed': 28, 'skipped_session': 1, 'skipped_history': 1},
            'overall': {'total_found': 80, 'successfully_processed': 76, 'skipped_session': 2, 'skipped_history': 2}
        }
        uploader_stats = {
            'total': 50, 'success': 45, 'failed': 5,
            'hacked_sub': 10, 'hacked_nosub': 15, 'subtitle': 10, 'no_subtitle': 10,
            'success_rate': 90.0
        }
        pikpak_stats = {
            'total_torrents': 100, 'filtered_old': 20, 'added_to_pikpak': 15,
            'removed_from_qb': 15, 'failed': 0, 'threshold_days': 3
        }
        ban_summary = "No proxies currently banned."
        
        report = format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary)
        
        assert "JavDB Pipeline Report" in report
        assert "SPIDER STATISTICS" in report
        assert "QBITTORRENT UPLOADER" in report
        assert "PIKPAK BRIDGE" in report
        assert "PROXY STATUS" in report
        assert "Total Found: 80" in report
        assert "Success: 45" in report


class TestGetProxyBanSummary:
    """Tests for get_proxy_ban_summary function"""
    
    @patch('pipeline.get_ban_manager')
    def test_get_proxy_ban_summary(self, mock_get_ban_manager):
        """Test getting proxy ban summary"""
        mock_manager = Mock()
        mock_manager.get_ban_summary.return_value = "Proxy ban summary"
        mock_get_ban_manager.return_value = mock_manager
        
        summary = get_proxy_ban_summary()
        
        assert summary == "Proxy ban summary"
        mock_manager.get_ban_summary.assert_called_once_with(include_ip=True)
    
    @patch('pipeline.get_ban_manager')
    def test_get_proxy_ban_summary_error(self, mock_get_ban_manager):
        """Test getting proxy ban summary when error occurs"""
        mock_get_ban_manager.side_effect = Exception("Test error")
        
        summary = get_proxy_ban_summary()
        
        assert "not available" in summary
