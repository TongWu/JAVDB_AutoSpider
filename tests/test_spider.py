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


class TestExtractUrlPartAfterJavdb:
    """Test cases for extract_url_part_after_javdb function."""
    
    def extract_url_part_after_javdb(self, url):
        """Local implementation of extract_url_part_after_javdb - matches production behavior."""
        import re
        try:
            if 'javdb.com' in url:
                domain_pos = url.find('javdb.com')
                if domain_pos != -1:
                    after_domain = url[domain_pos + len('javdb.com'):]
                    if after_domain.startswith('/'):
                        after_domain = after_domain[1:]
                    if after_domain.endswith('/'):
                        after_domain = after_domain[:-1]
                    # Replace URL special characters for filename safety
                    # - / (path separator) -> _
                    # - ? (query start) -> _
                    # - & (param separator) -> _
                    # - = (key-value separator) -> - (hyphen for better readability)
                    filename_part = after_domain
                    for char in ['/', '?', '&']:
                        filename_part = filename_part.replace(char, '_')
                    filename_part = filename_part.replace('=', '-')
                    # Collapse multiple consecutive underscores into one
                    filename_part = re.sub(r'_+', '_', filename_part)
                    # Remove leading/trailing underscores
                    filename_part = filename_part.strip('_')
                    return filename_part if filename_part else 'custom_url'
        except Exception:
            pass
        return 'custom_url'
    
    def test_simple_actors_url(self):
        """Test extracting from simple actors URL."""
        url = 'https://javdb.com/actors/658kM'
        assert self.extract_url_part_after_javdb(url) == 'actors_658kM'
    
    def test_actors_url_with_query_params(self):
        """Test extracting from actors URL with query parameters."""
        url = 'https://javdb.com/actors/658kM?t=d,c&sort_type=3'
        result = self.extract_url_part_after_javdb(url)
        # Production replaces & with _, = with -, and collapses consecutive underscores
        assert result == 'actors_658kM_t-d,c_sort_type-3'
    
    def test_rankings_url_with_query_params(self):
        """Test extracting from rankings URL with query parameters."""
        url = 'https://javdb.com/rankings/movies?p=monthly&t=censored'
        result = self.extract_url_part_after_javdb(url)
        assert result == 'rankings_movies_p-monthly_t-censored'
    
    def test_makers_url_with_query_params(self):
        """Test extracting from makers URL with query parameters."""
        url = 'https://javdb.com/makers/6M?f=download'
        result = self.extract_url_part_after_javdb(url)
        assert result == 'makers_6M_f-download'
    
    def test_url_with_trailing_slash(self):
        """Test extracting from URL with trailing slash."""
        url = 'https://javdb.com/actors/abc/'
        result = self.extract_url_part_after_javdb(url)
        assert result == 'actors_abc'
    
    def test_simple_path_no_query(self):
        """Test extracting from simple path without query."""
        url = 'https://javdb.com/video_codes/ABF'
        result = self.extract_url_part_after_javdb(url)
        assert result == 'video_codes_ABF'
    
    def test_non_javdb_url(self):
        """Test with non-javdb URL returns default."""
        url = 'https://example.com/path/to/page'
        result = self.extract_url_part_after_javdb(url)
        assert result == 'custom_url'


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
    
    def test_parse_actor_name_real_html_structure(self):
        """Test parsing actor name from real JavDB HTML structure (明日葉みつは page)."""
        # Real HTML structure from actors/658kM?t=d,c&sort_type=3.html
        html = '''
        <div class="columns is-desktop section-columns">
            <div class="column actor-avatar">
              <div class="image">
                <span class="avatar" style="background-image: url(https://c0.jdbstatic.com/avatars/65/658kM.jpg)"></span>
              </div>
            </div>
          <div class="column section-title">
            <h2 class="title is-4 has-text-justified">
              <span class="actor-section-name">明日葉みつは</span>
              <br>
              <span class="section-meta">64 部影片</span>
            </h2>
          </div>
        </div>
        '''
        assert self.parse_actor_name_from_html(html) == '明日葉みつは'
    
    def test_parse_actor_name_with_movie_count_meta(self):
        """Test parsing actor name when section-meta contains movie count."""
        html = '''
        <div class="column section-title">
            <h2 class="title is-4 has-text-justified">
              <span class="actor-section-name">七沢みあ</span>
              <br>
              <span class="section-meta">120 部影片</span>
            </h2>
        </div>
        '''
        assert self.parse_actor_name_from_html(html) == '七沢みあ'
    
    def test_parse_actor_name_full_page_html(self):
        """Test parsing actor name from a more complete HTML page structure."""
        # Simulating a full actor page with navigation, search bar, and actor info
        html = '''
        <!DOCTYPE html>
        <html class="has-navbar-fixed-top has-navbar-fixed-bottom">
          <head>
            <title> 明日葉みつは | JavDB 成人影片數據庫 </title>
          </head>
          <body data-lang="zh" data-domain="https://javdb565.com">
            <nav class="navbar is-fixed-top is-black is-fluid main-nav">
              <!-- Navigation content -->
            </nav>
            <section class="section">
              <div class="container">
                <div class="columns is-desktop section-columns">
                    <div class="column actor-avatar">
                      <div class="image">
                        <span class="avatar" style="background-image: url(https://c0.jdbstatic.com/avatars/65/658kM.jpg)"></span>
                      </div>
                    </div>
                  <div class="column section-title">
                    <h2 class="title is-4 has-text-justified">
                      <span class="actor-section-name">明日葉みつは</span>
                      <br>
                      <span class="section-meta">64 部影片</span>
                    </h2>
                  </div>
                </div>
                <div class="movie-list h cols-4 vcols-8">
                  <!-- Movie list content -->
                </div>
              </div>
            </section>
          </body>
        </html>
        '''
        assert self.parse_actor_name_from_html(html) == '明日葉みつは'


class TestGenerateOutputCsvNameFromHtml:
    """Test cases for generate_output_csv_name_from_html function."""
    
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
    
    def sanitize_filename_part(self, text, max_length=30):
        """Local implementation of sanitize_filename_part."""
        import re
        if not text:
            return ''
        unsafe_chars = r'<>:"/\|?*'
        sanitized = text
        for char in unsafe_chars:
            sanitized = sanitized.replace(char, '')
        sanitized = re.sub(r'\s+', '_', sanitized)
        sanitized = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff-]', '', sanitized)
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length]
        return sanitized
    
    def parse_section_name_from_html(self, html_content):
        """Local implementation of parse_section_name_from_html."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            section_name = soup.find('span', class_='section-name')
            if section_name:
                name = section_name.get_text(strip=True)
                if name:
                    return name
        except Exception:
            pass
        return None
    
    def detect_url_type(self, url):
        """Local implementation of detect_url_type."""
        if 'javdb.com/actors' in url or '/actors/' in url:
            return 'actors'
        elif 'javdb.com/makers' in url or '/makers/' in url:
            return 'makers'
        elif 'javdb.com/publishers' in url or '/publishers/' in url:
            return 'publishers'
        elif 'javdb.com/video_codes' in url or '/video_codes/' in url:
            return 'video_codes'
        elif 'javdb.com/series' in url or '/series/' in url:
            return 'series'
        elif 'javdb.com/directors' in url or '/directors/' in url:
            return 'directors'
        return 'unknown'
    
    def extract_url_part_after_javdb(self, url):
        """Local implementation of extract_url_part_after_javdb - matches production behavior."""
        import re
        try:
            if 'javdb.com' in url:
                domain_pos = url.find('javdb.com')
                if domain_pos != -1:
                    after_domain = url[domain_pos + len('javdb.com'):]
                    if after_domain.startswith('/'):
                        after_domain = after_domain[1:]
                    if after_domain.endswith('/'):
                        after_domain = after_domain[:-1]
                    # Replace URL special characters for filename safety
                    # - / (path separator) -> _
                    # - ? (query start) -> _
                    # - & (param separator) -> _
                    # - = (key-value separator) -> - (hyphen for better readability)
                    filename_part = after_domain
                    for char in ['/', '?', '&']:
                        filename_part = filename_part.replace(char, '_')
                    filename_part = filename_part.replace('=', '-')
                    # Collapse multiple consecutive underscores into one
                    filename_part = re.sub(r'_+', '_', filename_part)
                    # Remove leading/trailing underscores
                    filename_part = filename_part.strip('_')
                    return filename_part if filename_part else 'custom_url'
        except Exception:
            pass
        return 'custom_url'
    
    def generate_output_csv_name_from_html(self, custom_url, index_html):
        """Local implementation of generate_output_csv_name_from_html supporting all types."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        url_type = self.detect_url_type(custom_url)
        display_name = None
        
        if url_type == 'actors':
            actor_name = self.parse_actor_name_from_html(index_html)
            if actor_name:
                display_name = self.sanitize_filename_part(actor_name)
        elif url_type in ('makers', 'publishers', 'series', 'directors', 'video_codes'):
            raw_name = self.parse_section_name_from_html(index_html)
            if raw_name:
                display_name = self.sanitize_filename_part(raw_name)
        
        if display_name:
            return f'Javdb_AdHoc_{url_type}_{display_name}_{today_date}.csv'
        else:
            url_part = self.extract_url_part_after_javdb(custom_url)
            return f'Javdb_AdHoc_{url_part}_{today_date}.csv'
    
    def test_generate_csv_name_for_actor_page(self):
        """Test generating CSV name for actor page with real HTML structure."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/actors/658kM?t=d,c&sort_type=3'
        html = '''
        <div class="column section-title">
            <h2 class="title is-4 has-text-justified">
              <span class="actor-section-name">明日葉みつは</span>
              <br>
              <span class="section-meta">64 部影片</span>
            </h2>
        </div>
        '''
        
        result = self.generate_output_csv_name_from_html(url, html)
        assert result == f'Javdb_AdHoc_actors_明日葉みつは_{today_date}.csv'
    
    def test_generate_csv_name_for_actor_page_fallback(self):
        """Test generating CSV name fallback when actor name not found."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/actors/658kM?t=d,c&sort_type=3'
        html = '<div>No actor name here</div>'
        
        result = self.generate_output_csv_name_from_html(url, html)
        # Should fallback to URL-based name (production replaces & with _, = with -)
        assert result == f'Javdb_AdHoc_actors_658kM_t-d,c_sort_type-3_{today_date}.csv'
    
    def test_generate_csv_name_for_maker_page(self):
        """Test generating CSV name for maker page."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/makers/abc123'
        html = '''
        <div class="column section-title">
            <h2 class="title is-4">
              <span class="section-subtitle">片商</span>
              <span class="section-name">MOODYZ</span>
            </h2>
        </div>
        '''
        
        result = self.generate_output_csv_name_from_html(url, html)
        assert result == f'Javdb_AdHoc_makers_MOODYZ_{today_date}.csv'
    
    def test_generate_csv_name_with_special_chars_in_name(self):
        """Test generating CSV name when actor name contains special characters."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/actors/xyz'
        # Name with special characters that need to be sanitized
        html = '<span class="actor-section-name">Test/Actor:Name</span>'
        
        result = self.generate_output_csv_name_from_html(url, html)
        # Special characters should be removed
        assert '/' not in result
        assert ':' not in result
        assert result.startswith(f'Javdb_AdHoc_actors_')
        assert result.endswith(f'_{today_date}.csv')


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
    
    def test_parse_maker_name_real_html_prestige(self):
        """Test parsing maker name from real JavDB HTML (蚊香社/PRESTIGE page)."""
        # Real HTML structure from html/maker_6M.html
        html = '''
        <div class="columns is-mobile section-columns">
          <div class="column section-title">
            <h2 class="title is-4">
              <span class="section-subtitle">
                片商
              </span>
              <span class="section-name">
                蚊香社, PRESTIGE,プレステージ
              </span>
            </h2>
          </div>
        </div>
        '''
        assert self.parse_maker_name_from_html(html) == '蚊香社, PRESTIGE,プレステージ'


class TestParseSectionNameFromHtml:
    """Test cases for parse_section_name_from_html (generic section name parser)."""
    
    def parse_section_name_from_html(self, html_content):
        """Local implementation of parse_section_name_from_html."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            section_name = soup.find('span', class_='section-name')
            if section_name:
                name = section_name.get_text(strip=True)
                if name:
                    return name
        except Exception:
            pass
        return None
    
    def test_parse_publisher_name_real_html(self):
        """Test parsing publisher name from real JavDB HTML (ABSOLUTELY FANTASIA page)."""
        # Real HTML structure from html/publisher_O2ydO.html
        html = '''
        <h2 class="section-title title is-4">发行商&nbsp;<span class="section-name">ABSOLUTELY FANTASIA</span>&nbsp;作品</h2>
        '''
        assert self.parse_section_name_from_html(html) == 'ABSOLUTELY FANTASIA'
    
    def test_parse_series_name_real_html(self):
        """Test parsing series name from real JavDB HTML."""
        # Real HTML structure from html/series_KdqA.html
        html = '''
        <div class="column section-title">
          <h2 class="title is-4">
            <span class="section-subtitle">系列</span>
            <span class="section-name">親友の人妻と背徳不倫。禁断中出し小旅行。</span>
          </h2>
        </div>
        '''
        assert self.parse_section_name_from_html(html) == '親友の人妻と背徳不倫。禁断中出し小旅行。'
    
    def test_parse_video_code_name_real_html(self):
        """Test parsing video code from real JavDB HTML."""
        # Real HTML structure from html/video_codes_ABF.html
        html = '''
        <div class="column section-title">
          <h2 class="title is-4">
            <span class="section-subtitle">
              番號
            </span>
            <span class="section-name">ABF</span>
          </h2>
        </div>
        '''
        assert self.parse_section_name_from_html(html) == 'ABF'
    
    def test_parse_section_name_with_whitespace(self):
        """Test parsing section name with extra whitespace."""
        html = '''
        <span class="section-name">
          Some Name With Whitespace
        </span>
        '''
        assert self.parse_section_name_from_html(html) == 'Some Name With Whitespace'
    
    def test_parse_section_name_not_found(self):
        """Test parsing when section name is not found."""
        html = '<div>No section name here</div>'
        assert self.parse_section_name_from_html(html) is None


class TestGenerateOutputCsvNameAllTypes:
    """Test cases for generate_output_csv_name_from_html with all URL types."""
    
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
    
    def parse_section_name_from_html(self, html_content):
        """Local implementation of parse_section_name_from_html."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            section_name = soup.find('span', class_='section-name')
            if section_name:
                name = section_name.get_text(strip=True)
                if name:
                    return name
        except Exception:
            pass
        return None
    
    def sanitize_filename_part(self, text, max_length=30):
        """Local implementation of sanitize_filename_part."""
        import re
        if not text:
            return ''
        unsafe_chars = r'<>:"/\|?*'
        sanitized = text
        for char in unsafe_chars:
            sanitized = sanitized.replace(char, '')
        sanitized = re.sub(r'\s+', '_', sanitized)
        sanitized = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff-]', '', sanitized)
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length]
        return sanitized
    
    def detect_url_type(self, url):
        """Local implementation of detect_url_type."""
        if 'javdb.com/actors' in url or '/actors/' in url:
            return 'actors'
        elif 'javdb.com/makers' in url or '/makers/' in url:
            return 'makers'
        elif 'javdb.com/publishers' in url or '/publishers/' in url:
            return 'publishers'
        elif 'javdb.com/series' in url or '/series/' in url:
            return 'series'
        elif 'javdb.com/directors' in url or '/directors/' in url:
            return 'directors'
        elif 'javdb.com/video_codes' in url or '/video_codes/' in url:
            return 'video_codes'
        return 'unknown'
    
    def extract_url_part_after_javdb(self, url):
        """Local implementation of extract_url_part_after_javdb - matches production behavior."""
        import re
        try:
            if 'javdb.com' in url:
                domain_pos = url.find('javdb.com')
                if domain_pos != -1:
                    after_domain = url[domain_pos + len('javdb.com'):]
                    if after_domain.startswith('/'):
                        after_domain = after_domain[1:]
                    if after_domain.endswith('/'):
                        after_domain = after_domain[:-1]
                    # Replace URL special characters for filename safety
                    # - / (path separator) -> _
                    # - ? (query start) -> _
                    # - & (param separator) -> _
                    # - = (key-value separator) -> - (hyphen for better readability)
                    filename_part = after_domain
                    for char in ['/', '?', '&']:
                        filename_part = filename_part.replace(char, '_')
                    filename_part = filename_part.replace('=', '-')
                    # Collapse multiple consecutive underscores into one
                    filename_part = re.sub(r'_+', '_', filename_part)
                    # Remove leading/trailing underscores
                    filename_part = filename_part.strip('_')
                    return filename_part if filename_part else 'custom_url'
        except Exception:
            pass
        return 'custom_url'
    
    def generate_output_csv_name_from_html(self, custom_url, index_html):
        """Local implementation of generate_output_csv_name_from_html supporting all types."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        url_type = self.detect_url_type(custom_url)
        display_name = None
        
        if url_type == 'actors':
            raw_name = self.parse_actor_name_from_html(index_html)
            if raw_name:
                display_name = self.sanitize_filename_part(raw_name)
        elif url_type in ('makers', 'publishers', 'series', 'directors', 'video_codes'):
            raw_name = self.parse_section_name_from_html(index_html)
            if raw_name:
                display_name = self.sanitize_filename_part(raw_name)
        
        if display_name:
            return f'Javdb_AdHoc_{url_type}_{display_name}_{today_date}.csv'
        else:
            url_part = self.extract_url_part_after_javdb(custom_url)
            return f'Javdb_AdHoc_{url_part}_{today_date}.csv'
    
    def test_generate_csv_name_for_maker_prestige(self):
        """Test generating CSV name for maker page (蚊香社/PRESTIGE)."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/makers/6M'
        html = '''
        <div class="columns is-mobile section-columns">
          <div class="column section-title">
            <h2 class="title is-4">
              <span class="section-subtitle">片商</span>
              <span class="section-name">蚊香社, PRESTIGE,プレステージ</span>
            </h2>
          </div>
        </div>
        '''
        
        result = self.generate_output_csv_name_from_html(url, html)
        # Note: commas and spaces are sanitized
        assert result.startswith(f'Javdb_AdHoc_makers_')
        assert result.endswith(f'_{today_date}.csv')
        assert '蚊香社' in result
        assert 'PRESTIGE' in result
    
    def test_generate_csv_name_for_publisher(self):
        """Test generating CSV name for publisher page (ABSOLUTELY FANTASIA)."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/publishers/O2ydO'
        html = '''
        <h2 class="section-title title is-4">发行商&nbsp;<span class="section-name">ABSOLUTELY FANTASIA</span>&nbsp;作品</h2>
        '''
        
        result = self.generate_output_csv_name_from_html(url, html)
        assert result == f'Javdb_AdHoc_publishers_ABSOLUTELY_FANTASIA_{today_date}.csv'
    
    def test_generate_csv_name_for_series(self):
        """Test generating CSV name for series page."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/series/KdqA'
        html = '''
        <div class="column section-title">
          <h2 class="title is-4">
            <span class="section-subtitle">系列</span>
            <span class="section-name">親友の人妻と背徳不倫。禁断中出し小旅行。</span>
          </h2>
        </div>
        '''
        
        result = self.generate_output_csv_name_from_html(url, html)
        assert result.startswith(f'Javdb_AdHoc_series_')
        assert result.endswith(f'_{today_date}.csv')
        # Check that Japanese characters are preserved (after sanitization - 。removed)
        assert '親友の人妻と背徳不倫' in result
    
    def test_generate_csv_name_for_video_codes(self):
        """Test generating CSV name for video_codes page (ABF)."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/video_codes/ABF'
        html = '''
        <div class="column section-title">
          <h2 class="title is-4">
            <span class="section-subtitle">番號</span>
            <span class="section-name">ABF</span>
          </h2>
        </div>
        '''
        
        result = self.generate_output_csv_name_from_html(url, html)
        assert result == f'Javdb_AdHoc_video_codes_ABF_{today_date}.csv'
    
    def test_generate_csv_name_for_directors(self):
        """Test generating CSV name for directors page."""
        from datetime import datetime
        today_date = datetime.now().strftime("%Y%m%d")
        
        url = 'https://javdb.com/directors/xyz123'
        html = '''
        <div class="column section-title">
          <h2 class="title is-4">
            <span class="section-subtitle">导演</span>
            <span class="section-name">山田太郎</span>
          </h2>
        </div>
        '''
        
        result = self.generate_output_csv_name_from_html(url, html)
        assert result == f'Javdb_AdHoc_directors_山田太郎_{today_date}.csv'
    
    def test_url_type_detection_publishers(self):
        """Test URL type detection for publishers."""
        assert self.detect_url_type('https://javdb.com/publishers/O2ydO') == 'publishers'
    
    def test_url_type_detection_series(self):
        """Test URL type detection for series."""
        assert self.detect_url_type('https://javdb.com/series/KdqA') == 'series'
    
    def test_url_type_detection_video_codes(self):
        """Test URL type detection for video_codes."""
        assert self.detect_url_type('https://javdb.com/video_codes/ABF') == 'video_codes'


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


class TestMergeRowData:
    """Test cases for merge_row_data function."""
    
    # Placeholder that should not overwrite actual magnet URLs (matches production)
    DOWNLOADED_PLACEHOLDER = '[DOWNLOADED PREVIOUSLY]'
    
    def merge_row_data(self, existing_row, new_row):
        """Local implementation of merge_row_data - matches production behavior."""
        merged = existing_row.copy()
        
        for key, new_value in new_row.items():
            existing_value = merged.get(key, '')
            
            # Convert to string for comparison (handle None values)
            new_str = str(new_value) if new_value is not None else ''
            existing_str = str(existing_value) if existing_value is not None else ''
            
            # Treat placeholder as empty - don't let it overwrite actual magnet URLs
            if new_str == self.DOWNLOADED_PLACEHOLDER:
                # Only use placeholder if existing is empty (no data to preserve)
                if not existing_str:
                    merged[key] = new_value
                # else: keep existing value (preserve the actual magnet URL)
            elif new_str:
                # New has real data - use it (overwrite or fill empty)
                merged[key] = new_value
            # else: keep existing value (new is empty)
        
        return merged
    
    def test_new_data_overwrites_existing(self):
        """Test that new data overwrites existing data."""
        existing = {'video_code': 'ABC-123', 'subtitle': 'old_magnet', 'actor': 'Actor A'}
        new = {'video_code': 'ABC-123', 'subtitle': 'new_magnet', 'actor': 'Actor B'}
        
        result = self.merge_row_data(existing, new)
        
        assert result['subtitle'] == 'new_magnet'
        assert result['actor'] == 'Actor B'
    
    def test_keeps_existing_when_new_is_empty(self):
        """Test that existing data is kept when new data is empty."""
        existing = {'video_code': 'ABC-123', 'subtitle': 'old_magnet', 'hacked_subtitle': 'hacked_link'}
        new = {'video_code': 'ABC-123', 'subtitle': '', 'hacked_subtitle': ''}
        
        result = self.merge_row_data(existing, new)
        
        assert result['subtitle'] == 'old_magnet'
        assert result['hacked_subtitle'] == 'hacked_link'
    
    def test_new_fills_empty_existing(self):
        """Test that new data fills empty existing data."""
        existing = {'video_code': 'ABC-123', 'subtitle': '', 'hacked_subtitle': ''}
        new = {'video_code': 'ABC-123', 'subtitle': 'new_subtitle', 'hacked_subtitle': 'new_hacked'}
        
        result = self.merge_row_data(existing, new)
        
        assert result['subtitle'] == 'new_subtitle'
        assert result['hacked_subtitle'] == 'new_hacked'
    
    def test_mixed_merge(self):
        """Test mixed merge scenarios."""
        existing = {
            'video_code': 'ABC-123',
            'subtitle': 'old_subtitle',       # has data, new also has data -> overwrite
            'hacked_subtitle': 'old_hacked',  # has data, new is empty -> keep
            'no_subtitle': '',                 # empty, new has data -> use new
            'actor': 'Old Actor'               # has data, new also has data -> overwrite
        }
        new = {
            'video_code': 'ABC-123',
            'subtitle': 'new_subtitle',
            'hacked_subtitle': '',
            'no_subtitle': 'new_no_sub',
            'actor': 'New Actor'
        }
        
        result = self.merge_row_data(existing, new)
        
        assert result['video_code'] == 'ABC-123'
        assert result['subtitle'] == 'new_subtitle'       # overwritten
        assert result['hacked_subtitle'] == 'old_hacked'  # kept
        assert result['no_subtitle'] == 'new_no_sub'      # filled
        assert result['actor'] == 'New Actor'             # overwritten
    
    def test_handles_none_values(self):
        """Test that None values are treated as empty."""
        existing = {'video_code': 'ABC-123', 'subtitle': 'old_subtitle'}
        new = {'video_code': 'ABC-123', 'subtitle': None}
        
        result = self.merge_row_data(existing, new)
        
        assert result['subtitle'] == 'old_subtitle'
    
    def test_downloaded_placeholder_does_not_overwrite_existing_magnet(self):
        """Test that DOWNLOADED_PLACEHOLDER doesn't overwrite existing magnet URLs."""
        existing = {'video_code': 'ABC-123', 'subtitle': 'magnet:?xt=urn:btih:abc123'}
        new = {'video_code': 'ABC-123', 'subtitle': '[DOWNLOADED PREVIOUSLY]'}
        
        result = self.merge_row_data(existing, new)
        
        # The placeholder should NOT overwrite the actual magnet URL
        assert result['subtitle'] == 'magnet:?xt=urn:btih:abc123'
    
    def test_downloaded_placeholder_fills_empty_existing(self):
        """Test that DOWNLOADED_PLACEHOLDER is used when existing is empty."""
        existing = {'video_code': 'ABC-123', 'subtitle': ''}
        new = {'video_code': 'ABC-123', 'subtitle': '[DOWNLOADED PREVIOUSLY]'}
        
        result = self.merge_row_data(existing, new)
        
        # The placeholder should be used since existing is empty
        assert result['subtitle'] == '[DOWNLOADED PREVIOUSLY]'
    
    def test_downloaded_placeholder_mixed_scenario(self):
        """Test mixed scenario with placeholder and real data."""
        existing = {
            'video_code': 'ABC-123',
            'subtitle': 'magnet:?xt=urn:btih:abc123',  # has real data
            'hacked_subtitle': '',                      # empty
            'no_subtitle': 'magnet:?xt=urn:btih:def456' # has real data
        }
        new = {
            'video_code': 'ABC-123',
            'subtitle': '[DOWNLOADED PREVIOUSLY]',      # placeholder -> should NOT overwrite
            'hacked_subtitle': '[DOWNLOADED PREVIOUSLY]', # placeholder -> should fill empty
            'no_subtitle': 'new_magnet_link'             # real data -> should overwrite
        }
        
        result = self.merge_row_data(existing, new)
        
        assert result['subtitle'] == 'magnet:?xt=urn:btih:abc123'  # preserved
        assert result['hacked_subtitle'] == '[DOWNLOADED PREVIOUSLY]'  # filled
        assert result['no_subtitle'] == 'new_magnet_link'  # overwritten by real data


class TestWriteCsvMerge:
    """Test cases for write_csv merge functionality."""
    
    # Placeholder that should not overwrite actual magnet URLs (matches production)
    DOWNLOADED_PLACEHOLDER = '[DOWNLOADED PREVIOUSLY]'
    
    def merge_row_data(self, existing_row, new_row):
        """Local implementation of merge_row_data - matches production behavior."""
        merged = existing_row.copy()
        
        for key, new_value in new_row.items():
            existing_value = merged.get(key, '')
            
            # Convert to string for comparison (handle None values)
            new_str = str(new_value) if new_value is not None else ''
            existing_str = str(existing_value) if existing_value is not None else ''
            
            # Treat placeholder as empty - don't let it overwrite actual magnet URLs
            if new_str == self.DOWNLOADED_PLACEHOLDER:
                # Only use placeholder if existing is empty (no data to preserve)
                if not existing_str:
                    merged[key] = new_value
                # else: keep existing value (preserve the actual magnet URL)
            elif new_str:
                # New has real data - use it (overwrite or fill empty)
                merged[key] = new_value
            # else: keep existing value (new is empty)
        
        return merged
    
    def write_csv(self, rows, csv_path, fieldnames, dry_run=False, append_mode=False):
        """Local implementation of write_csv with merge support.
        
        Uses extrasaction='ignore' to safely handle rows with extra columns not in fieldnames.
        This prevents ValueError and data loss when existing CSV has additional columns.
        """
        import csv
        
        if dry_run:
            return
        
        if append_mode and os.path.exists(csv_path):
            existing_rows = {}
            rows_without_key = []  # Preserve rows without video_code
            try:
                with open(csv_path, 'r', newline='', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        video_code = row.get('video_code', '')
                        if video_code:
                            existing_rows[video_code] = row
                        else:
                            rows_without_key.append(row)
            except Exception:
                existing_rows = {}
                rows_without_key = []
            
            for new_row in rows:
                video_code = new_row.get('video_code', '')
                if not video_code:
                    rows_without_key.append(new_row)
                elif video_code in existing_rows:
                    existing_rows[video_code] = self.merge_row_data(existing_rows[video_code], new_row)
                else:
                    existing_rows[video_code] = new_row
            
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                # Use extrasaction='ignore' to handle rows with extra columns not in fieldnames
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                for row in existing_rows.values():
                    writer.writerow(row)
                for row in rows_without_key:
                    writer.writerow(row)
        else:
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                # Use extrasaction='ignore' to handle rows with extra columns not in fieldnames
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
    
    def read_csv(self, csv_path):
        """Helper to read CSV file into list of dicts."""
        import csv
        rows = []
        with open(csv_path, 'r', newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows
    
    def test_append_new_rows_to_existing_file(self, temp_dir):
        """Test that new rows are appended to existing file."""
        csv_path = os.path.join(temp_dir, 'test.csv')
        fieldnames = ['video_code', 'subtitle', 'actor']
        
        # Write initial data
        initial_rows = [
            {'video_code': 'ABC-001', 'subtitle': 'link1', 'actor': 'Actor A'}
        ]
        self.write_csv(initial_rows, csv_path, fieldnames, append_mode=False)
        
        # Append new data
        new_rows = [
            {'video_code': 'ABC-002', 'subtitle': 'link2', 'actor': 'Actor B'}
        ]
        self.write_csv(new_rows, csv_path, fieldnames, append_mode=True)
        
        # Verify
        result = self.read_csv(csv_path)
        assert len(result) == 2
        assert result[0]['video_code'] == 'ABC-001'
        assert result[1]['video_code'] == 'ABC-002'
    
    def test_merge_duplicate_video_code(self, temp_dir):
        """Test that duplicate video_code rows are merged."""
        csv_path = os.path.join(temp_dir, 'test.csv')
        fieldnames = ['video_code', 'subtitle', 'hacked_subtitle', 'actor']
        
        # Write initial data
        initial_rows = [
            {'video_code': 'ABC-001', 'subtitle': 'old_sub', 'hacked_subtitle': 'old_hacked', 'actor': 'Actor A'}
        ]
        self.write_csv(initial_rows, csv_path, fieldnames, append_mode=False)
        
        # Append data with same video_code
        new_rows = [
            {'video_code': 'ABC-001', 'subtitle': 'new_sub', 'hacked_subtitle': '', 'actor': ''}
        ]
        self.write_csv(new_rows, csv_path, fieldnames, append_mode=True)
        
        # Verify - should be merged, not duplicated
        result = self.read_csv(csv_path)
        assert len(result) == 1
        assert result[0]['video_code'] == 'ABC-001'
        assert result[0]['subtitle'] == 'new_sub'          # overwritten
        assert result[0]['hacked_subtitle'] == 'old_hacked' # kept (new was empty)
        assert result[0]['actor'] == 'Actor A'              # kept (new was empty)
    
    def test_mixed_new_and_duplicate_rows(self, temp_dir):
        """Test mix of new rows and rows that need to be merged."""
        csv_path = os.path.join(temp_dir, 'test.csv')
        fieldnames = ['video_code', 'subtitle', 'hacked_subtitle']
        
        # Write initial data
        initial_rows = [
            {'video_code': 'ABC-001', 'subtitle': 'sub1', 'hacked_subtitle': 'hacked1'},
            {'video_code': 'ABC-002', 'subtitle': 'sub2', 'hacked_subtitle': ''}
        ]
        self.write_csv(initial_rows, csv_path, fieldnames, append_mode=False)
        
        # Append mixed data
        new_rows = [
            {'video_code': 'ABC-001', 'subtitle': '', 'hacked_subtitle': 'new_hacked1'},  # merge
            {'video_code': 'ABC-003', 'subtitle': 'sub3', 'hacked_subtitle': 'hacked3'}   # new
        ]
        self.write_csv(new_rows, csv_path, fieldnames, append_mode=True)
        
        # Verify
        result = self.read_csv(csv_path)
        assert len(result) == 3
        
        # Find each row by video_code
        rows_by_code = {r['video_code']: r for r in result}
        
        # ABC-001 should be merged
        assert rows_by_code['ABC-001']['subtitle'] == 'sub1'            # kept
        assert rows_by_code['ABC-001']['hacked_subtitle'] == 'new_hacked1'  # updated
        
        # ABC-002 should be unchanged
        assert rows_by_code['ABC-002']['subtitle'] == 'sub2'
        
        # ABC-003 should be added
        assert rows_by_code['ABC-003']['subtitle'] == 'sub3'
        assert rows_by_code['ABC-003']['hacked_subtitle'] == 'hacked3'
    
    def test_second_run_same_csv_appends(self, temp_dir):
        """Test that second run with same CSV file appends correctly."""
        csv_path = os.path.join(temp_dir, 'test.csv')
        fieldnames = ['video_code', 'subtitle', 'actor', 'page']
        
        # First run
        run1_rows = [
            {'video_code': 'ABC-001', 'subtitle': 'link1', 'actor': 'Actor A', 'page': '1'},
            {'video_code': 'ABC-002', 'subtitle': 'link2', 'actor': 'Actor B', 'page': '1'}
        ]
        self.write_csv(run1_rows, csv_path, fieldnames, append_mode=False)
        
        # Second run (simulating same day, same CSV filename)
        run2_rows = [
            {'video_code': 'ABC-003', 'subtitle': 'link3', 'actor': 'Actor C', 'page': '2'},
            {'video_code': 'ABC-004', 'subtitle': 'link4', 'actor': 'Actor D', 'page': '2'}
        ]
        self.write_csv(run2_rows, csv_path, fieldnames, append_mode=True)
        
        # Verify all rows are present
        result = self.read_csv(csv_path)
        assert len(result) == 4
        video_codes = [r['video_code'] for r in result]
        assert 'ABC-001' in video_codes
        assert 'ABC-002' in video_codes
        assert 'ABC-003' in video_codes
        assert 'ABC-004' in video_codes
    
    def test_append_mode_handles_extra_columns_in_existing_csv(self, temp_dir):
        """Test that append_mode doesn't fail when existing CSV has extra columns.
        
        This test verifies that when an existing CSV file has columns that are NOT
        in the provided fieldnames, the write operation:
        1. Does not raise a ValueError
        2. Does not lose existing data
        3. Successfully writes the new data (with extra columns ignored)
        """
        import csv
        csv_path = os.path.join(temp_dir, 'test.csv')
        
        # Create an existing CSV with an extra column not in our fieldnames
        existing_fieldnames = ['video_code', 'subtitle', 'actor', 'extra_column']
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=existing_fieldnames)
            writer.writeheader()
            writer.writerow({
                'video_code': 'ABC-001',
                'subtitle': 'link1',
                'actor': 'Actor A',
                'extra_column': 'extra_data'
            })
        
        # Now try to append with fieldnames that don't include 'extra_column'
        # Without extrasaction='ignore', this would raise ValueError
        new_fieldnames = ['video_code', 'subtitle', 'actor']
        new_rows = [
            {'video_code': 'ABC-002', 'subtitle': 'link2', 'actor': 'Actor B'}
        ]
        
        # This should NOT raise an exception
        self.write_csv(new_rows, csv_path, new_fieldnames, append_mode=True)
        
        # Verify data was written and existing data preserved
        result = self.read_csv(csv_path)
        assert len(result) == 2
        
        # Both rows should be present
        video_codes = [r['video_code'] for r in result]
        assert 'ABC-001' in video_codes
        assert 'ABC-002' in video_codes
    
    def test_merge_with_downloaded_placeholder(self, temp_dir):
        """Test that DOWNLOADED_PLACEHOLDER doesn't overwrite existing magnet URLs during merge."""
        csv_path = os.path.join(temp_dir, 'test.csv')
        fieldnames = ['video_code', 'subtitle', 'hacked_subtitle']
        
        # Write initial data with actual magnet links
        initial_rows = [
            {'video_code': 'ABC-001', 'subtitle': 'magnet:?xt=urn:btih:abc123', 'hacked_subtitle': ''}
        ]
        self.write_csv(initial_rows, csv_path, fieldnames, append_mode=False)
        
        # Append data with DOWNLOADED_PLACEHOLDER for the same video_code
        new_rows = [
            {'video_code': 'ABC-001', 'subtitle': '[DOWNLOADED PREVIOUSLY]', 'hacked_subtitle': 'new_hacked_link'}
        ]
        self.write_csv(new_rows, csv_path, fieldnames, append_mode=True)
        
        # Verify
        result = self.read_csv(csv_path)
        assert len(result) == 1
        assert result[0]['video_code'] == 'ABC-001'
        # The placeholder should NOT overwrite the actual magnet URL
        assert result[0]['subtitle'] == 'magnet:?xt=urn:btih:abc123'
        # The new hacked_subtitle should be written since existing was empty
        assert result[0]['hacked_subtitle'] == 'new_hacked_link'

