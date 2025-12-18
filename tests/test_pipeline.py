"""
Unit tests for pipeline.py
Tests for log analysis, email formatting, and utility functions
"""
import pytest
import os
import sys
import re

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestMaskSensitiveInfo:
    """Tests for mask_sensitive_info function"""
    
    def test_mask_github_token_ghp(self):
        """Test masking GitHub personal access tokens (ghp_)"""
        # Import the function directly to avoid module initialization issues
        from pipeline import mask_sensitive_info
        
        text = "Using token ghp_1234567890abcdefghijklmnopqrstuvwxyz12345"
        result = mask_sensitive_info(text)
        assert "ghp_***MASKED***" in result
        assert "1234567890" not in result
    
    def test_mask_github_token_other_types(self):
        """Test masking other GitHub token types (gho_, ghr_, ghs_)"""
        from pipeline import mask_sensitive_info
        
        # Test gho_ token
        text = "Token gho_1234567890abcdefghijklmnopqrstuvwxyz12345"
        result = mask_sensitive_info(text)
        assert "***MASKED***" in result
    
    def test_mask_smtp_password(self):
        """Test masking SMTP passwords"""
        from pipeline import mask_sensitive_info
        
        text = "SMTP_PASSWORD=mysecretpassword"
        result = mask_sensitive_info(text)
        assert "SMTP_PASSWORD:***MASKED***" in result
        assert "mysecretpassword" not in result
    
    def test_mask_qbittorrent_password(self):
        """Test masking qBittorrent passwords"""
        from pipeline import mask_sensitive_info
        
        text = "password: secretpass123"
        result = mask_sensitive_info(text)
        assert "password:***MASKED***" in result
        assert "secretpass123" not in result
    
    def test_no_masking_for_safe_text(self):
        """Test that safe text is not modified"""
        from pipeline import mask_sensitive_info
        
        text = "This is a normal log message without sensitive info"
        result = mask_sensitive_info(text)
        assert result == text
    
    def test_mask_empty_text(self):
        """Test handling of empty text"""
        from pipeline import mask_sensitive_info
        
        assert mask_sensitive_info("") == ""
        assert mask_sensitive_info(None) is None


class TestGetLogSummary:
    """Tests for get_log_summary function"""
    
    def test_get_log_summary_existing_file(self, sample_spider_log):
        """Test getting log summary from existing file"""
        from pipeline import get_log_summary
        
        result = get_log_summary(sample_spider_log, lines=50)
        assert "Starting JavDB spider" in result
        assert "SUMMARY REPORT" in result
    
    def test_get_log_summary_missing_file(self, temp_dir):
        """Test handling of missing log file"""
        from pipeline import get_log_summary
        
        missing_file = os.path.join(temp_dir, 'nonexistent.log')
        result = get_log_summary(missing_file)
        assert "Log file not found" in result
    
    def test_get_log_summary_limited_lines(self, sample_spider_log):
        """Test getting limited number of lines"""
        from pipeline import get_log_summary
        
        result = get_log_summary(sample_spider_log, lines=5)
        # Should contain fewer lines than full log
        assert len(result.split('\n')) <= 6  # 5 lines + possible trailing newline


class TestExtractSpiderSummary:
    """Tests for extract_spider_summary function"""
    
    def test_extract_spider_summary_with_summary_section(self, sample_spider_log):
        """Test extracting summary section from spider log"""
        from pipeline import extract_spider_summary
        
        result = extract_spider_summary(sample_spider_log)
        assert "SUMMARY REPORT" in result
        assert "Total entries found" in result
        # Should not include proxy statistics
        assert "Total proxies:" not in result or "PROXY POOL STATISTICS" not in result
    
    def test_extract_spider_summary_missing_file(self, temp_dir):
        """Test handling of missing spider log"""
        from pipeline import extract_spider_summary
        
        missing_file = os.path.join(temp_dir, 'nonexistent.log')
        result = extract_spider_summary(missing_file)
        assert "Log file not found" in result


class TestAnalyzeSpiderLog:
    """Tests for analyze_spider_log function"""
    
    def test_analyze_spider_log_success(self, sample_spider_log):
        """Test analyzing successful spider log"""
        from pipeline import analyze_spider_log
        
        is_critical, error_msg = analyze_spider_log(sample_spider_log)
        assert is_critical is False
        assert error_msg is None
    
    def test_analyze_spider_log_missing_file(self, temp_dir):
        """Test handling of missing log file"""
        from pipeline import analyze_spider_log
        
        missing_file = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg = analyze_spider_log(missing_file)
        assert is_critical is True
        assert "not found" in error_msg
    
    def test_analyze_spider_log_proxy_ban(self, temp_dir):
        """Test detection of proxy ban"""
        from pipeline import analyze_spider_log
        
        log_file = os.path.join(temp_dir, 'spider_ban.log')
        with open(log_file, 'w') as f:
            f.write("CRITICAL: PROXY BAN DETECTED DURING THIS RUN\n")
        
        is_critical, error_msg = analyze_spider_log(log_file)
        assert is_critical is True
        assert "ban" in error_msg.lower()


class TestAnalyzeUploaderLog:
    """Tests for analyze_uploader_log function"""
    
    def test_analyze_uploader_log_success(self, sample_uploader_log):
        """Test analyzing successful uploader log"""
        from pipeline import analyze_uploader_log
        
        is_critical, error_msg = analyze_uploader_log(sample_uploader_log)
        assert is_critical is False
        assert error_msg is None
    
    def test_analyze_uploader_log_missing_file(self, temp_dir):
        """Test handling of missing log file"""
        from pipeline import analyze_uploader_log
        
        missing_file = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg = analyze_uploader_log(missing_file)
        assert is_critical is True
        assert "not found" in error_msg
    
    def test_analyze_uploader_log_connection_error(self, temp_dir):
        """Test detection of qBittorrent connection error"""
        from pipeline import analyze_uploader_log
        
        log_file = os.path.join(temp_dir, 'uploader_error.log')
        with open(log_file, 'w') as f:
            f.write("Cannot connect to qBittorrent\n")
        
        is_critical, error_msg = analyze_uploader_log(log_file)
        assert is_critical is True
        assert "qBittorrent" in error_msg


class TestAnalyzePikpakLog:
    """Tests for analyze_pikpak_log function"""
    
    def test_analyze_pikpak_log_success(self, sample_pikpak_log):
        """Test analyzing successful PikPak log"""
        from pipeline import analyze_pikpak_log
        
        is_critical, error_msg = analyze_pikpak_log(sample_pikpak_log)
        assert is_critical is False
        assert error_msg is None
    
    def test_analyze_pikpak_log_missing_file_not_critical(self, temp_dir):
        """Test that missing PikPak log is not critical"""
        from pipeline import analyze_pikpak_log
        
        missing_file = os.path.join(temp_dir, 'nonexistent.log')
        is_critical, error_msg = analyze_pikpak_log(missing_file)
        # PikPak is optional, so missing log should not be critical
        assert is_critical is False


class TestExtractStatistics:
    """Tests for statistics extraction functions"""
    
    def test_extract_spider_statistics(self, sample_spider_log):
        """Test extracting spider statistics"""
        from pipeline import extract_spider_statistics
        
        stats = extract_spider_statistics(sample_spider_log)
        
        assert 'phase1' in stats
        assert 'phase2' in stats
        assert 'overall' in stats
        assert 'total_found' in stats['overall']
        assert stats['overall']['total_found'] == 8
    
    def test_extract_uploader_statistics(self, sample_uploader_log):
        """Test extracting uploader statistics"""
        from pipeline import extract_uploader_statistics
        
        stats = extract_uploader_statistics(sample_uploader_log)
        
        assert stats['total'] == 10
        assert stats['success'] == 8
        assert stats['failed'] == 2
        assert stats['success_rate'] == 80.0
    
    def test_extract_pikpak_statistics(self, sample_pikpak_log):
        """Test extracting PikPak statistics"""
        from pipeline import extract_pikpak_statistics
        
        stats = extract_pikpak_statistics(sample_pikpak_log)
        
        assert 'total_torrents' in stats
        assert 'filtered_old' in stats
        assert 'threshold_days' in stats


class TestFormatEmailReport:
    """Tests for format_email_report function"""
    
    def test_format_email_report_basic(self):
        """Test basic email report formatting"""
        from pipeline import format_email_report
        
        spider_stats = {
            'phase1': {'found': 10, 'processed': 8, 'skipped_session': 1, 'skipped_history': 1},
            'phase2': {'found': 5, 'processed': 3, 'skipped_session': 1, 'skipped_history': 1},
            'overall': {'total_found': 15, 'successfully_processed': 11, 'skipped_session': 2, 'skipped_history': 2}
        }
        
        uploader_stats = {
            'total': 10,
            'success': 8,
            'failed': 2,
            'hacked_sub': 2,
            'hacked_nosub': 1,
            'subtitle': 3,
            'no_subtitle': 2,
            'success_rate': 80.0
        }
        
        pikpak_stats = {
            'total_torrents': 20,
            'filtered_old': 5,
            'added_to_pikpak': 4,
            'removed_from_qb': 4,
            'failed': 1,
            'threshold_days': 3
        }
        
        ban_summary = "No proxies currently banned."
        
        result = format_email_report(spider_stats, uploader_stats, pikpak_stats, ban_summary)
        
        assert "SPIDER STATISTICS" in result
        assert "QBITTORRENT UPLOADER" in result
        assert "PIKPAK BRIDGE" in result
        assert "PROXY STATUS" in result
        assert "Phase 1" in result
        assert "Phase 2" in result


class TestParseArguments:
    """Tests for parse_arguments function"""
    
    def test_parse_arguments_default(self, monkeypatch):
        """Test default argument parsing"""
        from pipeline import parse_arguments
        
        # Mock sys.argv
        monkeypatch.setattr(sys, 'argv', ['pipeline.py'])
        
        args = parse_arguments()
        assert args.url is None
        assert args.dry_run is False
        assert args.use_proxy is False
    
    def test_parse_arguments_with_options(self, monkeypatch):
        """Test argument parsing with various options"""
        from pipeline import parse_arguments
        
        monkeypatch.setattr(sys, 'argv', [
            'pipeline.py',
            '--dry-run',
            '--use-proxy',
            '--phase', '1',
            '--start-page', '5'
        ])
        
        args = parse_arguments()
        assert args.dry_run is True
        assert args.use_proxy is True
        assert args.phase == '1'
        assert args.start_page == 5
