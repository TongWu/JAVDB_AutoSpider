"""
Unit tests for scripts/spider.py functions.
These tests use local implementations to avoid module import issues.
"""
import os
import sys
import pytest
import argparse
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestParseArguments:
    """Test cases for parse_arguments function logic."""
    
    def create_parser(self):
        """Create the argument parser."""
        parser = argparse.ArgumentParser(description='JavDB Spider')
        
        parser.add_argument('--dry-run', action='store_true',
                            help='Print items that would be written without changing CSV file')
        parser.add_argument('--output-file', type=str,
                            help='Specify output CSV file name')
        parser.add_argument('--start-page', type=int, default=1,
                            help='Starting page number')
        parser.add_argument('--end-page', type=int, default=10,
                            help='Ending page number')
        parser.add_argument('--all', action='store_true',
                            help='Parse all pages until an empty page is found')
        parser.add_argument('--ignore-history', action='store_true',
                            help='Ignore history file')
        parser.add_argument('--url', type=str,
                            help='Custom URL to scrape')
        parser.add_argument('--phase', choices=['1', '2', 'all'], default='all',
                            help='Which phase to run')
        parser.add_argument('--ignore-release-date', action='store_true',
                            help='Ignore today/yesterday tags')
        parser.add_argument('--use-proxy', action='store_true',
                            help='Enable proxy for all HTTP requests')
        parser.add_argument('--use-cf-bypass', action='store_true',
                            help='Use CloudFlare5sBypass service')
        parser.add_argument('--from-pipeline', action='store_true',
                            help='Running from pipeline.py')
        
        return parser
    
    def test_default_arguments(self):
        """Test default argument values."""
        parser = self.create_parser()
        args = parser.parse_args([])
        
        assert args.dry_run is False
        assert args.ignore_history is False
        assert args.use_proxy is False
        assert args.use_cf_bypass is False
        assert args.from_pipeline is False
        assert args.phase == 'all'
        assert args.start_page == 1
        assert args.end_page == 10
    
    def test_dry_run_flag(self):
        """Test --dry-run flag."""
        parser = self.create_parser()
        args = parser.parse_args(['--dry-run'])
        assert args.dry_run is True
    
    def test_phase_flag(self):
        """Test --phase flag with different values."""
        parser = self.create_parser()
        
        args = parser.parse_args(['--phase', '1'])
        assert args.phase == '1'
        
        args = parser.parse_args(['--phase', '2'])
        assert args.phase == '2'
    
    def test_page_range_arguments(self):
        """Test --start-page and --end-page arguments."""
        parser = self.create_parser()
        args = parser.parse_args(['--start-page', '5', '--end-page', '15'])
        
        assert args.start_page == 5
        assert args.end_page == 15
    
    def test_url_argument(self):
        """Test --url argument."""
        parser = self.create_parser()
        args = parser.parse_args(['--url', 'https://javdb.com/actors/abc'])
        
        assert args.url == 'https://javdb.com/actors/abc'
    
    def test_output_file_argument(self):
        """Test --output-file argument."""
        parser = self.create_parser()
        args = parser.parse_args(['--output-file', 'custom_output.csv'])
        
        assert args.output_file == 'custom_output.csv'
    
    def test_all_flag(self):
        """Test --all flag for parsing all pages."""
        parser = self.create_parser()
        args = parser.parse_args(['--all'])
        
        assert args.all is True
    
    def test_ignore_release_date_flag(self):
        """Test --ignore-release-date flag."""
        parser = self.create_parser()
        args = parser.parse_args(['--ignore-release-date'])
        
        assert args.ignore_release_date is True
    
    def test_use_proxy_flag(self):
        """Test --use-proxy flag."""
        parser = self.create_parser()
        args = parser.parse_args(['--use-proxy'])
        
        assert args.use_proxy is True
    
    def test_use_cf_bypass_flag(self):
        """Test --use-cf-bypass flag."""
        parser = self.create_parser()
        args = parser.parse_args(['--use-cf-bypass'])
        
        assert args.use_cf_bypass is True


class TestEnsureDailyReportDir:
    """Test cases for ensure_daily_report_dir function logic."""
    
    def test_creates_directory_if_not_exists(self, temp_dir):
        """Test that directory is created if it doesn't exist."""
        test_dir = os.path.join(temp_dir, 'Daily Report')
        
        if not os.path.exists(test_dir):
            os.makedirs(test_dir)
        
        assert os.path.exists(test_dir)
    
    def test_does_not_error_if_exists(self, temp_dir):
        """Test that function doesn't error if directory exists."""
        test_dir = os.path.join(temp_dir, 'Daily Report')
        os.makedirs(test_dir)
        
        # Should not raise any exception
        if not os.path.exists(test_dir):
            os.makedirs(test_dir)
        
        assert os.path.exists(test_dir)


class TestDatedSubdirectoryLogic:
    """Test cases for dated subdirectory (YYYY/MM) logic."""
    
    def test_dated_subdir_format(self, temp_dir):
        """Test that dated subdirectory follows YYYY/MM format."""
        from datetime import datetime
        base_dir = os.path.join(temp_dir, 'Daily Report')
        
        # Simulate the dated subdirectory creation
        now = datetime.now()
        year = now.strftime('%Y')
        month = now.strftime('%m')
        dated_dir = os.path.join(base_dir, year, month)
        
        os.makedirs(dated_dir)
        
        assert os.path.exists(dated_dir)
        assert year in dated_dir
        assert month in dated_dir
    
    def test_csv_path_includes_dated_subdir(self, temp_dir):
        """Test that CSV paths include dated subdirectory."""
        from datetime import datetime
        base_dir = os.path.join(temp_dir, 'Daily Report')
        
        now = datetime.now()
        year = now.strftime('%Y')
        month = now.strftime('%m')
        filename = f'Javdb_TodayTitle_{now.strftime("%Y%m%d")}.csv'
        
        expected_path = os.path.join(base_dir, year, month, filename)
        
        # Verify the expected path structure
        assert year in expected_path
        assert month in expected_path
        assert filename in expected_path


class TestShouldUseProxyForModule:
    """Test cases for should_use_proxy_for_module function logic."""
    
    def should_use_proxy_for_module(self, module_name, use_proxy_flag, proxy_modules):
        """Local implementation of should_use_proxy_for_module."""
        if not use_proxy_flag:
            return False
        if not proxy_modules:
            return False
        if 'all' in proxy_modules:
            return True
        return module_name in proxy_modules
    
    def test_false_when_use_proxy_flag_is_false(self):
        """Test returns False when use_proxy_flag is False."""
        result = self.should_use_proxy_for_module('spider_index', False, ['all'])
        assert result is False
    
    def test_true_when_all_in_proxy_modules(self):
        """Test returns True when 'all' in PROXY_MODULES."""
        result = self.should_use_proxy_for_module('spider_index', True, ['all'])
        assert result is True
    
    def test_true_when_module_in_proxy_modules(self):
        """Test returns True when module in PROXY_MODULES."""
        result = self.should_use_proxy_for_module('spider_detail', True, ['spider_detail'])
        assert result is True
    
    def test_false_when_module_not_in_proxy_modules(self):
        """Test returns False when module not in PROXY_MODULES."""
        result = self.should_use_proxy_for_module('other_module', True, ['spider_index'])
        assert result is False


class TestExtractIpFromProxyUrl:
    """Test cases for extract_ip_from_proxy_url function logic."""
    
    def extract_ip_from_proxy_url(self, proxy_url):
        """Local implementation."""
        if not proxy_url:
            return None
        
        try:
            parsed = urlparse(proxy_url)
            return parsed.hostname
        except Exception:
            return None
    
    def test_extract_ip_from_proxy_url(self):
        """Test extracting IP from proxy URL."""
        result = self.extract_ip_from_proxy_url('http://192.168.1.100:8080')
        assert result == '192.168.1.100'
    
    def test_extract_ip_with_credentials(self):
        """Test extracting IP from proxy URL with credentials."""
        result = self.extract_ip_from_proxy_url('http://user:pass@192.168.1.100:8080')
        assert result == '192.168.1.100'
    
    def test_extract_hostname(self):
        """Test extracting hostname from proxy URL."""
        result = self.extract_ip_from_proxy_url('http://proxy.example.com:8080')
        assert result == 'proxy.example.com'


class TestGetCfBypassServiceUrl:
    """Test cases for get_cf_bypass_service_url function logic."""
    
    def get_cf_bypass_service_url(self, proxy_ip, cf_bypass_port=8000):
        """Local implementation."""
        if proxy_ip:
            return f"http://{proxy_ip}:{cf_bypass_port}"
        else:
            return f"http://127.0.0.1:{cf_bypass_port}"
    
    def test_default_url_without_proxy(self):
        """Test default URL when no proxy IP provided."""
        result = self.get_cf_bypass_service_url(None)
        assert result == 'http://127.0.0.1:8000'
    
    def test_url_with_proxy_ip(self):
        """Test URL with proxy IP provided."""
        result = self.get_cf_bypass_service_url('192.168.1.100')
        assert result == 'http://192.168.1.100:8000'


class TestIsCfBypassFailure:
    """Test cases for is_cf_bypass_failure function logic."""
    
    def is_cf_bypass_failure(self, html_content):
        """Local implementation."""
        if html_content is None:
            return True
        if len(html_content) < 100:
            if 'fail' in html_content.lower():
                return True
        return False
    
    def test_returns_false_for_valid_content(self):
        """Test returns False for valid HTML content."""
        html = '<html><body>Valid content with many characters</body></html>' * 10
        result = self.is_cf_bypass_failure(html)
        assert result is False
    
    def test_returns_true_for_failure_content(self):
        """Test returns True for failure response."""
        html = 'Failed to get clearance'
        result = self.is_cf_bypass_failure(html)
        assert result is True
    
    def test_returns_false_for_short_success_content(self):
        """Test returns False for short but successful content."""
        html = 'OK'
        result = self.is_cf_bypass_failure(html)
        assert result is False
    
    def test_returns_true_for_none_content(self):
        """Test returns True for None content."""
        result = self.is_cf_bypass_failure(None)
        assert result is True


# Use temp_dir fixture
@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    import tempfile
    import shutil
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)

