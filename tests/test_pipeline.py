"""
Unit tests for email_notification.py functions (formerly in pipeline.py).
"""
import os
import sys
import pytest
import tempfile

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Import functions from email_notification script
from scripts.email_notification import (
    analyze_spider_log,
    analyze_uploader_log,
    analyze_pikpak_log,
    analyze_pipeline_log,
    extract_spider_statistics,
    extract_uploader_statistics,
    extract_pikpak_statistics,
    format_email_report
)

# Import mask_sensitive_info from git_helper (it's now the canonical location)
from utils.git_helper import mask_sensitive_info


def get_log_summary(log_path, lines=200):
    """Helper function to get last N lines from a log file."""
    if not os.path.exists(log_path):
        return f'Log file not found: {log_path}'
    with open(log_path, 'r', encoding='utf-8') as f:
        log_lines = f.readlines()
    return ''.join(log_lines[-lines:])


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
        """Test with non-existent log file - not critical since script may not have run."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg, log_exists = analyze_spider_log(log_path)
        # Missing log is not critical - email notification should still succeed
        assert is_critical is False
        assert 'not found' in error_msg
        assert log_exists is False
    
    def test_proxy_ban_detected(self, temp_dir):
        """Test detection of proxy ban in log."""
        log_path = os.path.join(temp_dir, 'spider.log')
        with open(log_path, 'w') as f:
            f.write("CRITICAL: PROXY BAN DETECTED DURING THIS RUN\n")
        
        is_critical, error_msg, log_exists = analyze_spider_log(log_path)
        assert is_critical is True
        assert 'Proxy ban' in error_msg
        assert log_exists is True
    
    def test_successful_run(self, temp_dir):
        """Test successful spider run detection."""
        log_path = os.path.join(temp_dir, 'spider.log')
        with open(log_path, 'w') as f:
            f.write("PHASE 1: Processing entries\n")
            f.write("Successfully fetched URL: https://javdb.com/?page=1\n")
            f.write("Total entries found: 50\n")
            f.write("OVERALL SUMMARY\n")
        
        is_critical, error_msg, log_exists = analyze_spider_log(log_path)
        assert is_critical is False
        assert error_msg is None
        assert log_exists is True


class TestAnalyzeUploaderLog:
    """Test cases for analyze_uploader_log function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file - not critical since script may not have run."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg, log_exists = analyze_uploader_log(log_path)
        # Missing log is not critical - email notification should still succeed
        assert is_critical is False
        assert 'not found' in error_msg
        assert log_exists is False
    
    def test_qbittorrent_connection_failure(self, temp_dir):
        """Test detection of qBittorrent connection failure."""
        log_path = os.path.join(temp_dir, 'uploader.log')
        with open(log_path, 'w') as f:
            f.write("Cannot connect to qBittorrent\n")
        
        is_critical, error_msg, log_exists = analyze_uploader_log(log_path)
        assert is_critical is True
        assert 'qBittorrent' in error_msg
        assert log_exists is True
    
    def test_successful_upload(self, temp_dir):
        """Test successful upload detection."""
        log_path = os.path.join(temp_dir, 'uploader.log')
        with open(log_path, 'w') as f:
            f.write("Starting to add torrents\n")
            f.write("Successfully added: 10\n")
            f.write("Failed to add: 0\n")
        
        is_critical, error_msg, log_exists = analyze_uploader_log(log_path)
        assert is_critical is False
        assert log_exists is True


class TestAnalyzePikpakLog:
    """Test cases for analyze_pikpak_log function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file - should not be critical."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg, log_exists = analyze_pikpak_log(log_path)
        # PikPak is optional, so missing log is not critical
        assert is_critical is False
        assert log_exists is False
    
    def test_qbittorrent_login_failure(self, temp_dir):
        """Test detection of qBittorrent login failure in PikPak."""
        log_path = os.path.join(temp_dir, 'pikpak.log')
        with open(log_path, 'w') as f:
            f.write("qBittorrent login failed\n")
        
        is_critical, error_msg, log_exists = analyze_pikpak_log(log_path)
        assert is_critical is True
        assert 'qBittorrent' in error_msg
        assert log_exists is True


class TestAnalyzePipelineLog:
    """Test cases for analyze_pipeline_log function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file - not critical since email should still work."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg, log_exists = analyze_pipeline_log(log_path)
        # Missing log is not critical - email notification should still succeed
        assert is_critical is False
        assert 'not found' in error_msg
        assert log_exists is False
    
    def test_script_execution_failure(self, temp_dir):
        """Test detection of script execution failure."""
        log_path = os.path.join(temp_dir, 'pipeline.log')
        with open(log_path, 'w') as f:
            f.write("PIPELINE EXECUTION ERROR\n")
            f.write("Script scripts/spider.py failed with return code 1\n")
        
        is_critical, error_msg, log_exists = analyze_pipeline_log(log_path)
        assert is_critical is True
        assert 'spider' in error_msg or 'Pipeline' in error_msg
        assert log_exists is True
    
    def test_syntax_error_detected(self, temp_dir):
        """Test detection of syntax errors in pipeline log."""
        log_path = os.path.join(temp_dir, 'pipeline.log')
        with open(log_path, 'w') as f:
            f.write("Starting pipeline...\n")
            f.write("IndentationError: unexpected indent\n")
        
        is_critical, error_msg, log_exists = analyze_pipeline_log(log_path)
        assert is_critical is True
        assert 'IndentationError' in error_msg
        assert log_exists is True
    
    def test_module_not_found_error(self, temp_dir):
        """Test detection of ModuleNotFoundError."""
        log_path = os.path.join(temp_dir, 'pipeline.log')
        with open(log_path, 'w') as f:
            f.write("Starting pipeline...\n")
            f.write("ModuleNotFoundError: No module named 'missing_module'\n")
        
        is_critical, error_msg, log_exists = analyze_pipeline_log(log_path)
        assert is_critical is True
        assert 'module' in error_msg.lower()
        assert log_exists is True
    
    def test_import_error_detected(self, temp_dir):
        """Test detection of ImportError."""
        log_path = os.path.join(temp_dir, 'pipeline.log')
        with open(log_path, 'w') as f:
            f.write("Starting pipeline...\n")
            f.write("ImportError: cannot import name 'something'\n")
        
        is_critical, error_msg, log_exists = analyze_pipeline_log(log_path)
        assert is_critical is True
        assert 'Import' in error_msg
        assert log_exists is True
    
    def test_successful_run(self, temp_dir):
        """Test successful pipeline run detection."""
        log_path = os.path.join(temp_dir, 'pipeline.log')
        with open(log_path, 'w') as f:
            f.write("Starting pipeline...\n")
            f.write("Step 1: Running Spider...\n")
            f.write("Spider completed successfully\n")
            f.write("Pipeline completed\n")
        
        is_critical, error_msg, log_exists = analyze_pipeline_log(log_path)
        assert is_critical is False
        assert error_msg is None
        assert log_exists is True


class TestExtractSpiderStatistics:
    """Test cases for extract_spider_statistics function."""
    
    def test_log_not_found(self, temp_dir):
        """Test with non-existent log file."""
        log_path = os.path.join(temp_dir, 'nonexistent.log')
        stats = extract_spider_statistics(log_path)
        # When log not found, discovered should be None (not 0) per new logic
        assert stats['overall']['total_discovered'] is None
    
    def test_statistics_extraction(self, temp_dir):
        """Test extracting statistics from spider log (new format with no_new_torrents)."""
        log_path = os.path.join(temp_dir, 'spider.log')
        with open(log_path, 'w') as f:
            f.write("[Page  1] Found  20 entries for phase 1,  10 for phase 2\n")
            f.write("[Page  2] Found  15 entries for phase 1,   0 for phase 2\n")
            f.write("Phase 1 completed: 35 movies discovered, 28 processed, 3 skipped (history), 1 no new torrents, 1 failed\n")
            f.write("Phase 2 completed: 10 movies discovered, 7 processed, 1 skipped (history), 0 no new torrents, 1 failed\n")
            f.write("Total movies discovered: 45\n")
            f.write("Successfully processed: 35\n")
            f.write("Skipped already parsed in previous runs: 4\n")
            f.write("No new torrents to download: 1\n")
            f.write("Failed to fetch/parse: 2\n")
        
        stats = extract_spider_statistics(log_path)
        assert stats['phase1']['discovered'] == 35
        assert stats['phase1']['processed'] == 28
        assert stats['phase1']['skipped_history'] == 3
        assert stats['phase1']['no_new_torrents'] == 1
        assert stats['phase1']['failed'] == 1
        assert stats['phase2']['discovered'] == 10
        assert stats['phase2']['processed'] == 7
        assert stats['overall']['total_discovered'] == 45
        assert stats['overall']['successfully_processed'] == 35
        assert stats['overall']['skipped_history'] == 4
        assert stats['overall']['no_new_torrents'] == 1
        assert stats['overall']['failed'] == 2
    
    def test_statistics_extraction_old_format(self, temp_dir):
        """Test extracting statistics from spider log (old format for backwards compatibility)."""
        log_path = os.path.join(temp_dir, 'spider.log')
        with open(log_path, 'w') as f:
            f.write("Phase 1 completed: 35 found, 5 skipped (history), 30 written to CSV\n")
            f.write("Phase 2 completed: 10 found, 2 skipped (history), 8 written to CSV\n")
            f.write("Total entries found: 38\n")
            f.write("Successfully processed: 38\n")
            f.write("Skipped already parsed in previous runs: 10\n")
        
        stats = extract_spider_statistics(log_path)
        assert stats['phase1']['discovered'] == 35
        assert stats['phase1']['processed'] == 30
        assert stats['phase2']['discovered'] == 10
        assert stats['phase2']['processed'] == 8
        assert stats['overall']['total_discovered'] == 38
        assert stats['overall']['successfully_processed'] == 38
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
            f.write("Total torrents in CSV: 25\n")
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
            'phase1': {'discovered': 20, 'processed': 15, 'skipped_history': 3, 'no_new_torrents': 0, 'failed': 0},
            'phase2': {'discovered': 10, 'processed': 8, 'skipped_history': 1, 'no_new_torrents': 0, 'failed': 0},
            'overall': {'total_discovered': 30, 'successfully_processed': 23, 'skipped_history': 4, 'no_new_torrents': 0, 'failed': 0}
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
        assert '20' in result  # phase1 discovered
        assert '87.0%' in result  # success rate
        assert 'No banned proxies' in result
        assert 'No New Torrents' in result  # new field
        assert 'Skipped (Session)' not in result
