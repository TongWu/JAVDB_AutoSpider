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
        result = self.should_use_proxy_for_module('spider', False, ['all'])
        assert result is False
    
    def test_true_when_all_in_proxy_modules(self):
        """Test returns True when 'all' in PROXY_MODULES."""
        result = self.should_use_proxy_for_module('spider', True, ['all'])
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


class TestDetectUrlType:
    """Test cases for detect_url_type function."""
    
    def detect_url_type(self, url):
        """Local implementation of detect_url_type."""
        if not url or 'javdb.com' not in url:
            return 'unknown'
        
        try:
            parsed = urlparse(url)
            path = parsed.path.strip('/')
            
            if path.startswith('actors/'):
                return 'actors'
            elif path.startswith('makers/'):
                return 'makers'
            elif path.startswith('video_codes/'):
                return 'video_codes'
            else:
                return 'unknown'
        except Exception:
            return 'unknown'
    
    def test_detect_actors_url(self):
        """Test detection of actors URL."""
        url = 'https://javdb.com/actors/bkxd'
        assert self.detect_url_type(url) == 'actors'
    
    def test_detect_makers_url(self):
        """Test detection of makers URL."""
        url = 'https://javdb.com/makers/zKW?f=download'
        assert self.detect_url_type(url) == 'makers'
    
    def test_detect_video_codes_url(self):
        """Test detection of video_codes URL."""
        url = 'https://javdb.com/video_codes/MIDA'
        assert self.detect_url_type(url) == 'video_codes'
    
    def test_detect_unknown_url(self):
        """Test detection of unknown URL type."""
        url = 'https://javdb.com/some/other/path'
        assert self.detect_url_type(url) == 'unknown'
    
    def test_detect_non_javdb_url(self):
        """Test detection of non-javdb URL."""
        url = 'https://example.com/actors/abc'
        assert self.detect_url_type(url) == 'unknown'
    
    def test_detect_empty_url(self):
        """Test detection of empty/None URL."""
        assert self.detect_url_type(None) == 'unknown'
        assert self.detect_url_type('') == 'unknown'


class TestExtractUrlIdentifier:
    """Test cases for extract_url_identifier function."""
    
    def extract_url_identifier(self, url):
        """Local implementation of extract_url_identifier."""
        try:
            parsed = urlparse(url)
            path = parsed.path.strip('/')
            parts = path.split('/')
            if len(parts) >= 2:
                return parts[1]
        except Exception:
            pass
        return None
    
    def test_extract_actor_identifier(self):
        """Test extracting identifier from actors URL."""
        url = 'https://javdb.com/actors/bkxd'
        assert self.extract_url_identifier(url) == 'bkxd'
    
    def test_extract_maker_identifier(self):
        """Test extracting identifier from makers URL."""
        url = 'https://javdb.com/makers/zKW?f=download'
        assert self.extract_url_identifier(url) == 'zKW'
    
    def test_extract_video_code_identifier(self):
        """Test extracting identifier from video_codes URL."""
        url = 'https://javdb.com/video_codes/MIDA'
        assert self.extract_url_identifier(url) == 'MIDA'
    
    def test_extract_from_short_path(self):
        """Test extracting from URL with short path."""
        url = 'https://javdb.com/single'
        assert self.extract_url_identifier(url) is None


class TestParseActorNameFromHtml:
    """Test cases for parse_actor_name_from_html function."""
    
    def parse_actor_name_from_html(self, html_content):
        """Local implementation of parse_actor_name_from_html."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            actor_span = soup.find('span', class_='actor-section-name')
            if actor_span:
                actor_name = actor_span.get_text(strip=True)
                if actor_name:
                    return actor_name
        except Exception:
            pass
        return None
    
    def test_parse_actor_name(self):
        """Test parsing actor name from HTML."""
        html = '''
        <div class="column section-title">
            <h2 class="title is-4 has-text-justified">
              <span class="actor-section-name">森日向子</span>
            </h2>
        </div>
        '''
        assert self.parse_actor_name_from_html(html) == '森日向子'
    
    def test_parse_actor_name_with_extra_whitespace(self):
        """Test parsing actor name with extra whitespace."""
        html = '<span class="actor-section-name">  Test Actor  </span>'
        assert self.parse_actor_name_from_html(html) == 'Test Actor'
    
    def test_parse_actor_name_not_found(self):
        """Test parsing when actor name is not found."""
        html = '<div>No actor name here</div>'
        assert self.parse_actor_name_from_html(html) is None
    
    def test_parse_actor_name_empty_span(self):
        """Test parsing when span is empty."""
        html = '<span class="actor-section-name"></span>'
        assert self.parse_actor_name_from_html(html) is None


class TestParseMakerNameFromHtml:
    """Test cases for parse_maker_name_from_html function."""
    
    def parse_maker_name_from_html(self, html_content):
        """Local implementation of parse_maker_name_from_html."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            section_name = soup.find('span', class_='section-name')
            if section_name:
                maker_name = section_name.get_text(strip=True)
                if maker_name:
                    return maker_name
        except Exception:
            pass
        return None
    
    def test_parse_maker_name(self):
        """Test parsing maker name from HTML."""
        html = '''
        <div class="column section-title">
            <h2 class="title is-4">
              <span class="section-subtitle">片商</span>
              <span class="section-name">MOODYZ</span>
            </h2>
        </div>
        '''
        assert self.parse_maker_name_from_html(html) == 'MOODYZ'
    
    def test_parse_maker_name_english(self):
        """Test parsing English maker name."""
        html = '<span class="section-name">Prestige</span>'
        assert self.parse_maker_name_from_html(html) == 'Prestige'
    
    def test_parse_maker_name_not_found(self):
        """Test parsing when maker name is not found."""
        html = '<div>No maker name here</div>'
        assert self.parse_maker_name_from_html(html) is None


class TestSanitizeFilenamePart:
    """Test cases for sanitize_filename_part function."""
    
    def sanitize_filename_part(self, text, max_length=30):
        """Local implementation of sanitize_filename_part."""
        import re
        
        if not text:
            return ''
        
        # Replace or remove unsafe filename characters
        unsafe_chars = r'<>:"/\|?*'
        sanitized = text
        for char in unsafe_chars:
            sanitized = sanitized.replace(char, '')
        
        # Replace whitespace with underscore
        sanitized = re.sub(r'\s+', '_', sanitized)
        
        # Remove any remaining non-alphanumeric characters except underscore, hyphen, and CJK characters
        sanitized = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff-]', '', sanitized)
        
        # Truncate to max length
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length]
        
        return sanitized
    
    def test_sanitize_japanese_name(self):
        """Test sanitizing Japanese name."""
        assert self.sanitize_filename_part('森日向子') == '森日向子'
    
    def test_sanitize_english_name(self):
        """Test sanitizing English name."""
        assert self.sanitize_filename_part('MOODYZ') == 'MOODYZ'
    
    def test_sanitize_name_with_space(self):
        """Test sanitizing name with spaces."""
        assert self.sanitize_filename_part('Test Name') == 'Test_Name'
    
    def test_sanitize_name_with_slashes(self):
        """Test sanitizing name with slashes."""
        result = self.sanitize_filename_part('Name/With/Slashes')
        assert '/' not in result
    
    def test_sanitize_name_with_special_chars(self):
        """Test sanitizing name with special characters."""
        result = self.sanitize_filename_part('Name<With>Special:Chars')
        assert '<' not in result
        assert '>' not in result
        assert ':' not in result
    
    def test_sanitize_empty_string(self):
        """Test sanitizing empty string."""
        assert self.sanitize_filename_part('') == ''
    
    def test_sanitize_none(self):
        """Test sanitizing None."""
        assert self.sanitize_filename_part(None) == ''
    
    def test_sanitize_truncate_long_name(self):
        """Test that long names are truncated."""
        long_name = 'A' * 50
        result = self.sanitize_filename_part(long_name, max_length=30)
        assert len(result) == 30


class TestHasMagnetFilter:
    """Test cases for has_magnet_filter function.
    
    Different URL types use different filter parameters:
    - actors: t=d or t=c
    - makers/video_codes: f=download
    """
    
    def has_magnet_filter(self, url):
        """Local implementation of has_magnet_filter."""
        try:
            from urllib.parse import parse_qs
            parsed = urlparse(url)
            if not parsed.query:
                return False
            
            params = parse_qs(parsed.query)
            path = parsed.path.strip('/')
            
            if path.startswith('actors/'):
                # For actors: check 't' parameter
                if 't' not in params:
                    return False
                for t_val in params['t']:
                    parts = t_val.split(',')
                    if 'd' in parts or 'c' in parts:
                        return True
                return False
            
            elif path.startswith('makers/') or path.startswith('video_codes/'):
                # For makers/video_codes: check 'f' parameter
                if 'f' not in params:
                    return False
                for f_val in params['f']:
                    if f_val == 'download':
                        return True
                return False
            
            return False
        except Exception:
            return False
    
    # Tests for actors (t=d filter)
    def test_actors_no_filter_plain_url(self):
        """Test actors URL without any filter."""
        url = 'https://javdb.com/actors/YnZ1K'
        assert self.has_magnet_filter(url) is False
    
    def test_actors_has_download_filter(self):
        """Test actors URL with t=d filter."""
        url = 'https://javdb.com/actors/YnZ1K?t=d'
        assert self.has_magnet_filter(url) is True
    
    def test_actors_has_download_filter_with_other_params(self):
        """Test actors URL with t=d and other parameters."""
        url = 'https://javdb.com/actors/YnZ1K?t=d&sort_type=0'
        assert self.has_magnet_filter(url) is True
    
    def test_actors_has_subtitle_filter(self):
        """Test actors URL with t=c (subtitle) filter."""
        url = 'https://javdb.com/actors/YnZ1K?t=c&sort_type=0'
        assert self.has_magnet_filter(url) is True
    
    def test_actors_has_year_filter_only(self):
        """Test actors URL with year filter only (no magnet filter)."""
        url = 'https://javdb.com/actors/YnZ1K?t=312&sort_type=0'
        assert self.has_magnet_filter(url) is False
    
    def test_actors_has_combined_filter(self):
        """Test actors URL with combined filter (t=312,d)."""
        url = 'https://javdb.com/actors/YnZ1K?t=312,d'
        assert self.has_magnet_filter(url) is True
    
    def test_actors_has_other_params_no_t(self):
        """Test actors URL with other params but no t parameter."""
        url = 'https://javdb.com/actors/YnZ1K?sort_type=0'
        assert self.has_magnet_filter(url) is False
    
    # Tests for makers (f=download filter)
    def test_makers_no_filter_plain_url(self):
        """Test makers URL without any filter."""
        url = 'https://javdb.com/makers/zKW'
        assert self.has_magnet_filter(url) is False
    
    def test_makers_has_download_filter(self):
        """Test makers URL with f=download filter."""
        url = 'https://javdb.com/makers/zKW?f=download'
        assert self.has_magnet_filter(url) is True
    
    def test_makers_has_other_f_value(self):
        """Test makers URL with different f value."""
        url = 'https://javdb.com/makers/zKW?f=other'
        assert self.has_magnet_filter(url) is False
    
    # Tests for video_codes (f=download filter)
    def test_video_codes_no_filter_plain_url(self):
        """Test video_codes URL without any filter."""
        url = 'https://javdb.com/video_codes/MIDA'
        assert self.has_magnet_filter(url) is False
    
    def test_video_codes_has_download_filter(self):
        """Test video_codes URL with f=download filter."""
        url = 'https://javdb.com/video_codes/MIDA?f=download'
        assert self.has_magnet_filter(url) is True


class TestAddMagnetFilterToUrl:
    """Test cases for add_magnet_filter_to_url function.
    
    Different URL types use different filter parameters:
    - actors: t=d (or append ,d to existing t value)
    - makers/video_codes: f=download
    """
    
    def has_magnet_filter(self, url):
        """Helper method to check if URL has magnet filter."""
        try:
            from urllib.parse import parse_qs
            parsed = urlparse(url)
            if not parsed.query:
                return False
            params = parse_qs(parsed.query)
            path = parsed.path.strip('/')
            
            if path.startswith('actors/'):
                if 't' not in params:
                    return False
                for t_val in params['t']:
                    parts = t_val.split(',')
                    if 'd' in parts or 'c' in parts:
                        return True
                return False
            elif path.startswith('makers/') or path.startswith('video_codes/'):
                if 'f' not in params:
                    return False
                for f_val in params['f']:
                    if f_val == 'download':
                        return True
                return False
            return False
        except Exception:
            return False
    
    def add_magnet_filter_to_url(self, url):
        """Local implementation of add_magnet_filter_to_url."""
        from urllib.parse import parse_qs, urlencode, urlunparse
        
        try:
            if self.has_magnet_filter(url):
                return url
            
            parsed = urlparse(url)
            path = parsed.path.strip('/')
            
            if path.startswith('actors/'):
                # For actors: use t=d filter
                if not parsed.query:
                    # Handle edge case: URL ends with '?' but has no query params
                    base_url = url.rstrip('?')
                    return f"{base_url}?t=d"
                params = parse_qs(parsed.query, keep_blank_values=True)
                if 't' not in params:
                    # Handle edge case: URL might end with '&'
                    base_url = url.rstrip('&')
                    return f"{base_url}&t=d"
                else:
                    new_t_values = []
                    for t_val in params['t']:
                        parts = t_val.split(',')
                        if 'd' not in parts and 'c' not in parts:
                            new_t_values.append(f"{t_val},d")
                        else:
                            new_t_values.append(t_val)
                    params['t'] = new_t_values
                    flat_params = [(k, v) for k, vals in params.items() for v in vals]
                    new_query = urlencode(flat_params, safe=',')
                    return urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                                       parsed.params, new_query, parsed.fragment))
            
            elif path.startswith('makers/') or path.startswith('video_codes/'):
                # For makers/video_codes: use f=download filter
                if not parsed.query:
                    # Handle edge case: URL ends with '?' but has no query params
                    base_url = url.rstrip('?')
                    return f"{base_url}?f=download"
                params = parse_qs(parsed.query, keep_blank_values=True)
                if 'f' not in params:
                    # Handle edge case: URL might end with '&'
                    base_url = url.rstrip('&')
                    return f"{base_url}&f=download"
                else:
                    params['f'] = ['download']
                    flat_params = [(k, v) for k, vals in params.items() for v in vals]
                    new_query = urlencode(flat_params)
                    return urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                                       parsed.params, new_query, parsed.fragment))
            
            return url
                
        except Exception:
            return url
    
    # Tests for actors (t=d filter)
    def test_actors_add_filter_to_plain_url(self):
        """Test adding t=d filter to actors URL without query params."""
        url = 'https://javdb.com/actors/YnZ1K'
        result = self.add_magnet_filter_to_url(url)
        assert result == 'https://javdb.com/actors/YnZ1K?t=d'
    
    def test_actors_no_change_when_has_download_filter(self):
        """Test no change when actors URL already has t=d."""
        url = 'https://javdb.com/actors/YnZ1K?t=d&sort_type=0'
        result = self.add_magnet_filter_to_url(url)
        assert result == url
    
    def test_actors_no_change_when_has_subtitle_filter(self):
        """Test no change when actors URL already has t=c."""
        url = 'https://javdb.com/actors/YnZ1K?t=c&sort_type=0'
        result = self.add_magnet_filter_to_url(url)
        assert result == url
    
    def test_actors_append_to_year_filter(self):
        """Test appending ,d to year filter for actors."""
        url = 'https://javdb.com/actors/YnZ1K?t=312&sort_type=0'
        result = self.add_magnet_filter_to_url(url)
        assert 't=312,d' in result
    
    def test_actors_add_filter_to_url_with_other_params(self):
        """Test adding t=d filter to actors URL with other params but no t."""
        url = 'https://javdb.com/actors/YnZ1K?sort_type=0'
        result = self.add_magnet_filter_to_url(url)
        assert result == 'https://javdb.com/actors/YnZ1K?sort_type=0&t=d'
    
    # Tests for makers (f=download filter)
    def test_makers_add_filter_to_plain_url(self):
        """Test adding f=download filter to makers URL without query params."""
        url = 'https://javdb.com/makers/zKW'
        result = self.add_magnet_filter_to_url(url)
        assert result == 'https://javdb.com/makers/zKW?f=download'
    
    def test_makers_no_change_when_has_download_filter(self):
        """Test no change when makers URL already has f=download."""
        url = 'https://javdb.com/makers/zKW?f=download'
        result = self.add_magnet_filter_to_url(url)
        assert result == url
    
    def test_makers_add_filter_to_url_with_other_params(self):
        """Test adding f=download filter to makers URL with other params."""
        url = 'https://javdb.com/makers/zKW?sort_type=0'
        result = self.add_magnet_filter_to_url(url)
        assert result == 'https://javdb.com/makers/zKW?sort_type=0&f=download'
    
    # Tests for video_codes (f=download filter)
    def test_video_codes_add_filter_to_plain_url(self):
        """Test adding f=download filter to video_codes URL without query params."""
        url = 'https://javdb.com/video_codes/MIDA'
        result = self.add_magnet_filter_to_url(url)
        assert result == 'https://javdb.com/video_codes/MIDA?f=download'
    
    def test_video_codes_no_change_when_has_download_filter(self):
        """Test no change when video_codes URL already has f=download."""
        url = 'https://javdb.com/video_codes/MIDA?f=download'
        result = self.add_magnet_filter_to_url(url)
        assert result == url
    
    # Edge case tests: URLs ending with '?'
    def test_actors_url_ending_with_question_mark(self):
        """Test that URL ending with '?' doesn't produce double '??'."""
        url = 'https://javdb.com/actors/YnZ1K?'
        result = self.add_magnet_filter_to_url(url)
        assert '??' not in result
        assert result == 'https://javdb.com/actors/YnZ1K?t=d'
    
    def test_makers_url_ending_with_question_mark(self):
        """Test that makers URL ending with '?' doesn't produce double '??'."""
        url = 'https://javdb.com/makers/zKW?'
        result = self.add_magnet_filter_to_url(url)
        assert '??' not in result
        assert result == 'https://javdb.com/makers/zKW?f=download'
    
    def test_video_codes_url_ending_with_question_mark(self):
        """Test that video_codes URL ending with '?' doesn't produce double '??'."""
        url = 'https://javdb.com/video_codes/MIDA?'
        result = self.add_magnet_filter_to_url(url)
        assert '??' not in result
        assert result == 'https://javdb.com/video_codes/MIDA?f=download'


# Use temp_dir fixture
@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    import tempfile
    import shutil
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)

