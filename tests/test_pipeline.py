"""
Unit tests for pipeline.py functions.
"""
import os
import sys
import pytest
import tempfile

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

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
    format_email_report
)


class TestMaskSensitiveInfo:
    """Test cases for mask_sensitive_info function."""
    
    def test_mask_github_token(self):
        """Test masking GitHub personal access token."""
        text = "Using token: ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        result = mask_sensitive_info(text)
        assert 'ghp_***MASKED***' in result
        assert 'ghp_1234567890' not in result
    
    def test_mask_other_github_tokens(self):
        """Test masking other GitHub token types."""
        text = "Token gho_1234567890abcdefghijklmnopqrstuvwxyz"
        result = mask_sensitive_info(text)
        assert 'gh*_***MASKED***' in result
    
    def test_mask_password_in_config(self):
        """Test masking password in configuration."""
        text = "password: mysecretpassword123"
        result = mask_sensitive_info(text)
        assert 'mysecretpassword123' not in result
        assert '***MASKED***' in result
    
    def test_mask_smtp_password(self):
        """Test masking SMTP password."""
        text = "SMTP_PASSWORD: mysmtppassword"
        result = mask_sensitive_info(text)
        assert 'mysmtppassword' not in result
        assert '***MASKED***' in result
    
    def test_empty_text(self):
        """Test with empty text."""
        assert mask_sensitive_info('') == ''
        assert mask_sensitive_info(None) is None
    
    def test_normal_text_unchanged(self):
        """Test that normal text is not modified."""
        text = "This is a normal log message without any secrets"
        result = mask_sensitive_info(text)
        assert result == text


class TestGetLogSummary:
    """Test cases for get_log_summary function."""
    
    def test_log_file_not_found(self, temp_dir):
        """Test with non-existent log file."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        result = get_log_summary(log_path)
        assert 'Log file not found' in result
    
    def test_log_summary_extraction(self, temp_dir):
        """Test extracting last N lines from log."""
        log_path = os.path.join(temp_dir, 'test.log')
        with open(log_path, 'w') as f:
            for i in range(300):
                f.write(f"Log line {i}\n")
        
        result = get_log_summary(log_path, lines=50)
        lines = result.strip().split('\n')
        assert len(lines) == 50
        assert 'Log line 299' in result
        assert 'Log line 250' in result


class TestAnalyzeSpiderLog:
    """Test cases for analyze_spider_log function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg = analyze_spider_log(log_path)
        assert is_critical is True
        assert 'not found' in error_msg
    
    def test_proxy_ban_detected(self, temp_dir):
        """Test detection of proxy ban in log."""
        log_path = os.path.join(temp_dir, 'spider.log')
        with open(log_path, 'w') as f:
            f.write("CRITICAL: PROXY BAN DETECTED DURING THIS RUN\n")
        
        is_critical, error_msg = analyze_spider_log(log_path)
        assert is_critical is True
        assert 'Proxy ban' in error_msg
    
    def test_successful_run(self, temp_dir):
        """Test successful spider run detection."""
        log_path = os.path.join(temp_dir, 'spider.log')
        with open(log_path, 'w') as f:
            f.write("PHASE 1: Processing entries\n")
            f.write("Successfully fetched URL: https://javdb.com/?page=1\n")
            f.write("Total entries found: 50\n")
            f.write("OVERALL SUMMARY\n")
        
        is_critical, error_msg = analyze_spider_log(log_path)
        assert is_critical is False
        assert error_msg is None


class TestAnalyzeUploaderLog:
    """Test cases for analyze_uploader_log function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg = analyze_uploader_log(log_path)
        assert is_critical is True
        assert 'not found' in error_msg
    
    def test_qbittorrent_connection_failure(self, temp_dir):
        """Test detection of qBittorrent connection failure."""
        log_path = os.path.join(temp_dir, 'uploader.log')
        with open(log_path, 'w') as f:
            f.write("Cannot connect to qBittorrent\n")
        
        is_critical, error_msg = analyze_uploader_log(log_path)
        assert is_critical is True
        assert 'qBittorrent' in error_msg
    
    def test_successful_upload(self, temp_dir):
        """Test successful upload detection."""
        log_path = os.path.join(temp_dir, 'uploader.log')
        with open(log_path, 'w') as f:
            f.write("Starting to add torrents\n")
            f.write("Successfully added: 10\n")
            f.write("Failed to add: 0\n")
        
        is_critical, error_msg = analyze_uploader_log(log_path)
        assert is_critical is False


class TestAnalyzePikpakLog:
    """Test cases for analyze_pikpak_log function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file - should not be critical."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg = analyze_pikpak_log(log_path)
        # PikPak is optional, so missing log is not critical
        assert is_critical is False
    
    def test_qbittorrent_login_failure(self, temp_dir):
        """Test detection of qBittorrent login failure in PikPak."""
        log_path = os.path.join(temp_dir, 'pikpak.log')
        with open(log_path, 'w') as f:
            f.write("qBittorrent login failed\n")
        
        is_critical, error_msg = analyze_pikpak_log(log_path)
        assert is_critical is True
        assert 'qBittorrent' in error_msg


class TestExtractSpiderStatistics:
    """Test cases for extract_spider_statistics function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        stats = extract_spider_statistics(log_path)
        assert stats['overall']['total_found'] == 0
    
    def test_statistics_extraction(self, temp_dir):
        """Test extracting statistics from spider log."""
        log_path = os.path.join(temp_dir, 'spider.log')
        with open(log_path, 'w') as f:
            f.write("[Page 1] Found 20 entries for phase 1\n")
            f.write("[Page 2] Found 15 entries for phase 1\n")
            f.write("Phase 1 completed: 30 entries processed\n")
            f.write("[Page 1] Found 10 entries for phase 2\n")
            f.write("Phase 2 completed: 8 entries processed\n")
            f.write("Total entries found: 38\n")
            f.write("Successfully processed: 38\n")
            f.write("Skipped already parsed in this session: 5\n")
            f.write("Skipped already parsed in previous runs: 10\n")
        
        stats = extract_spider_statistics(log_path)
        assert stats['phase1']['found'] == 35  # 20 + 15
        assert stats['phase1']['processed'] == 30
        assert stats['phase2']['found'] == 10
        assert stats['phase2']['processed'] == 8
        assert stats['overall']['total_found'] == 38
        assert stats['overall']['successfully_processed'] == 38
        assert stats['overall']['skipped_session'] == 5
        assert stats['overall']['skipped_history'] == 10


class TestExtractUploaderStatistics:
    """Test cases for extract_uploader_statistics function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        stats = extract_uploader_statistics(log_path)
        assert stats['total'] == 0
        assert stats['success'] == 0
    
    def test_statistics_extraction(self, temp_dir):
        """Test extracting statistics from uploader log."""
        log_path = os.path.join(temp_dir, 'uploader.log')
        with open(log_path, 'w') as f:
            f.write("Total torrents found: 25\n")
            f.write("Successfully added: 22\n")
            f.write("Failed to add: 3\n")
            f.write("Hacked subtitle torrents: 5\n")
            f.write("Hacked no subtitle torrents: 3\n")
            f.write("Subtitle torrents: 10\n")
            f.write("No subtitle torrents: 4\n")
            f.write("Success rate: 88.0%\n")
        
        stats = extract_uploader_statistics(log_path)
        assert stats['total'] == 25
        assert stats['success'] == 22
        assert stats['failed'] == 3
        assert stats['hacked_sub'] == 5
        assert stats['hacked_nosub'] == 3
        assert stats['subtitle'] == 10
        assert stats['no_subtitle'] == 4
        assert stats['success_rate'] == 88.0


class TestExtractPikpakStatistics:
    """Test cases for extract_pikpak_statistics function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        stats = extract_pikpak_statistics(log_path)
        assert stats['total_torrents'] == 0
    
    def test_statistics_extraction(self, temp_dir):
        """Test extracting statistics from PikPak log."""
        log_path = os.path.join(temp_dir, 'pikpak.log')
        with open(log_path, 'w') as f:
            f.write("Processing torrents older than 3 days\n")
            f.write("Found 15 torrents\n")
            f.write("Filtered 8 torrents older than 3 days\n")
            f.write("Successfully added to PikPak\n")
            f.write("Successfully added to PikPak\n")
            f.write("Removed from qBittorrent\n")
            f.write("Removed from qBittorrent\n")
            f.write("Failed to add\n")
        
        stats = extract_pikpak_statistics(log_path)
        assert stats['total_torrents'] == 15
        assert stats['filtered_old'] == 8
        assert stats['added_to_pikpak'] == 2
        assert stats['removed_from_qb'] == 2
        assert stats['failed'] == 1


class TestFormatEmailReport:
    """Test cases for format_email_report function."""
    
    def test_format_email_report(self):
        """Test email report formatting."""
        spider_stats = {
            'phase1': {'found': 20, 'processed': 15, 'skipped_session': 2, 'skipped_history': 3},
            'phase2': {'found': 10, 'processed': 8, 'skipped_session': 1, 'skipped_history': 1},
            'overall': {'total_found': 30, 'successfully_processed': 23, 'skipped_session': 3, 'skipped_history': 4}
        }
        uploader_stats = {
            'total': 23,
            'success': 20,
            'failed': 3,
            'hacked_sub': 5,
            'hacked_nosub': 2,
            'subtitle': 10,
            'no_subtitle': 3,
            'success_rate': 87.0
        }
        pikpak_stats = {
            'total_torrents': 10,
            'filtered_old': 5,
            'added_to_pikpak': 4,
            'removed_from_qb': 4,
            'failed': 1,
            'threshold_days': 3
        }
        ban_summary = "No banned proxies"
        
        result = format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary)
        
        assert 'JavDB Pipeline Report' in result
        assert 'SPIDER STATISTICS' in result
        assert 'QBITTORRENT UPLOADER' in result
        assert 'PIKPAK BRIDGE' in result
        assert 'PROXY STATUS' in result
        assert '20' in result  # phase1 found
        assert '87.0%' in result  # success rate
        assert 'No banned proxies' in result

