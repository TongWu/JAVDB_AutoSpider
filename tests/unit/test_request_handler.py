"""
Unit tests for utils/infra/request_handler.py
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, Mock
import requests

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from utils.infra.request_handler import (
    RequestConfig,
    RequestHandler,
    ProxyHelper,
    ProxyBannedError,
    create_request_handler_from_config,
    create_proxy_helper_from_config
)


_PROXY_RULE_SUBJECTS = [
    pytest.param('request_handler', id='request_handler'),
    pytest.param('proxy_helper', id='proxy_helper'),
]


def _make_proxy_rule_subject(subject_name, proxy_modules):
    if subject_name == 'request_handler':
        return RequestHandler(config=RequestConfig(proxy_modules=proxy_modules))
    return ProxyHelper(proxy_modules=proxy_modules)


class TestRequestConfig:
    """Test cases for RequestConfig class."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = RequestConfig()
        
        assert config.base_url == 'https://javdb.com'
        assert config.cf_bypass_service_port == 8000
        assert config.cf_bypass_enabled is True
        assert config.cf_bypass_max_failures == 3
        assert config.cf_turnstile_cooldown == 10
        assert config.fallback_cooldown == 30
        assert config.javdb_session_cookie is None
        assert config.proxy_http is None
        assert config.proxy_https is None
        assert config.proxy_modules == ['spider']
        assert config.proxy_mode == 'pool'
        assert config.between_attempt_sleep is None
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = RequestConfig(
            base_url='https://custom.com',
            cf_bypass_service_port=9000,
            cf_bypass_enabled=False,
            javdb_session_cookie='my_session',
            proxy_http='http://proxy:8080',
            proxy_mode='pool'
        )
        
        assert config.base_url == 'https://custom.com'
        assert config.cf_bypass_service_port == 9000
        assert config.cf_bypass_enabled is False
        assert config.javdb_session_cookie == 'my_session'
        assert config.proxy_http == 'http://proxy:8080'
        assert config.proxy_mode == 'pool'
    
    def test_post_init_sets_proxy_modules(self):
        """Test that post_init sets default proxy_modules if None."""
        config = RequestConfig(proxy_modules=None)
        
        assert config.proxy_modules == ['spider']


class TestProxySelectionRules:
    """Shared proxy-module selection rules for both request abstractions."""

    @pytest.mark.parametrize("subject_name", _PROXY_RULE_SUBJECTS)
    def test_force_disable_proxy(self, subject_name):
        subject = _make_proxy_rule_subject(subject_name, None)
        assert subject.should_use_proxy_for_module('spider', use_proxy_flag=False) is False

    @pytest.mark.parametrize("subject_name", _PROXY_RULE_SUBJECTS)
    def test_all_modules_auto_mode(self, subject_name):
        subject = _make_proxy_rule_subject(subject_name, ['all'])
        assert subject.should_use_proxy_for_module('spider', use_proxy_flag=None) is True

    @pytest.mark.parametrize("subject_name", _PROXY_RULE_SUBJECTS)
    def test_specific_modules_auto_mode(self, subject_name):
        subject = _make_proxy_rule_subject(subject_name, ['spider', 'qbittorrent'])
        assert subject.should_use_proxy_for_module('spider', use_proxy_flag=None) is True
        assert subject.should_use_proxy_for_module('pikpak', use_proxy_flag=None) is False

    @pytest.mark.parametrize("subject_name", _PROXY_RULE_SUBJECTS)
    def test_empty_module_list_auto_mode(self, subject_name):
        subject = _make_proxy_rule_subject(subject_name, [])
        assert subject.should_use_proxy_for_module('spider', use_proxy_flag=None) is False

    @pytest.mark.parametrize("subject_name", _PROXY_RULE_SUBJECTS)
    def test_force_enable_proxy(self, subject_name):
        subject = _make_proxy_rule_subject(subject_name, [])
        assert subject.should_use_proxy_for_module('pikpak', use_proxy_flag=True) is True


class TestRequestHandler:
    """Test cases for RequestHandler class."""
    
    def test_init_default(self):
        """Test default initialization."""
        handler = RequestHandler()
        
        assert handler.proxy_pool is None
        assert handler.config is not None
        assert handler.session is not None
        assert handler.cf_bypass_failure_count == 0
        assert handler.cf_bypass_force_refresh is False
    
    def test_init_with_config(self):
        """Test initialization with custom config."""
        config = RequestConfig(base_url='https://custom.com')
        handler = RequestHandler(config=config)
        
        assert handler.config.base_url == 'https://custom.com'
    
    def test_init_with_proxy_pool(self):
        """Test initialization with proxy pool."""
        mock_pool = MagicMock()
        handler = RequestHandler(proxy_pool=mock_pool)
        
        assert handler.proxy_pool is mock_pool
    
    def test_extract_ip_from_proxy_url(self):
        """Test extract_ip_from_proxy_url."""
        result = RequestHandler.extract_ip_from_proxy_url('http://user:pass@192.168.1.1:8080')
        
        assert result == '192.168.1.1'
    
    def test_extract_ip_from_proxy_url_hostname(self):
        """Test extract_ip_from_proxy_url with hostname."""
        result = RequestHandler.extract_ip_from_proxy_url('http://proxy.example.com:8080')
        
        assert result == 'proxy.example.com'
    
    def test_extract_ip_from_proxy_url_invalid(self):
        """Test extract_ip_from_proxy_url with invalid URL."""
        result = RequestHandler.extract_ip_from_proxy_url('not a url')
        
        # Should return None or handle gracefully
        assert result is None or result == 'not a url'
    
    def test_get_cf_bypass_service_url_local(self):
        """Test get_cf_bypass_service_url without proxy."""
        config = RequestConfig(cf_bypass_service_port=8000)
        handler = RequestHandler(config=config)
        
        result = handler.get_cf_bypass_service_url(proxy_ip=None)
        
        assert result == 'http://127.0.0.1:8000'
    
    def test_get_cf_bypass_service_url_with_proxy(self):
        """Test get_cf_bypass_service_url with proxy IP."""
        config = RequestConfig(cf_bypass_service_port=8000)
        handler = RequestHandler(config=config)
        
        result = handler.get_cf_bypass_service_url(proxy_ip='192.168.1.100')
        
        assert result == 'http://192.168.1.100:8000'
    
    def test_is_cf_bypass_failure_none(self):
        """Test is_cf_bypass_failure with None content."""
        result = RequestHandler.is_cf_bypass_failure(None)
        
        assert result is True
    
    def test_is_cf_bypass_failure_small_with_fail(self):
        """Test is_cf_bypass_failure with small content containing 'fail'."""
        content = '{"status": "fail", "message": "error"}'
        result = RequestHandler.is_cf_bypass_failure(content)
        
        assert result is True
    
    def test_is_cf_bypass_failure_large_content(self):
        """Test is_cf_bypass_failure with large content."""
        content = 'x' * 1500  # Larger than 1000 bytes
        result = RequestHandler.is_cf_bypass_failure(content)
        
        assert result is False
    
    def test_is_cf_bypass_failure_small_no_fail(self):
        """Test is_cf_bypass_failure with small content without 'fail'."""
        content = '{"status": "ok"}'
        result = RequestHandler.is_cf_bypass_failure(content)
        
        assert result is False
    
    def test_reset_cf_bypass_state(self):
        """Test reset_cf_bypass_state."""
        handler = RequestHandler()
        handler.cf_bypass_failure_count = 5
        handler.cf_bypass_force_refresh = True
        
        handler.reset_cf_bypass_state()
        
        assert handler.cf_bypass_failure_count == 0
        assert handler.cf_bypass_force_refresh is False
    
    @patch.object(requests.Session, 'get')
    def test_do_request_success(self, mock_get):
        """Test _do_request successful response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html>Test content</html>'
        mock_response.content = b'<html>Test content</html>'
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        handler = RequestHandler()
        html, error = handler._do_request(
            'http://test.com', 
            {'User-Agent': 'Test'}, 
            None, 
            timeout=30, 
            context_msg='Test'
        )
        
        assert html == '<html>Test content</html>'
        assert error is None
    
    @patch.object(requests.Session, 'get')
    def test_do_request_error(self, mock_get):
        """Test _do_request with error."""
        mock_get.side_effect = requests.RequestException("Connection error")
        
        handler = RequestHandler()
        html, error = handler._do_request(
            'http://test.com', 
            {}, 
            None, 
            timeout=30, 
            context_msg='Test'
        )
        
        assert html is None
        assert error is not None
    
    def test_get_proxies_config_no_proxy(self):
        """Test _get_proxies_config when proxy not used."""
        handler = RequestHandler()
        
        proxies, use_pool = handler._get_proxies_config('spider', use_proxy=False)
        
        assert proxies is None
        assert use_pool is False
    
    def test_get_proxies_config_legacy_proxy(self):
        """Test _get_proxies_config with legacy proxy settings."""
        config = RequestConfig(
            proxy_http='http://proxy:8080',
            proxy_https='https://proxy:8080',
            proxy_mode='legacy',
            proxy_modules=['all']
        )
        handler = RequestHandler(config=config)
        
        proxies, use_pool = handler._get_proxies_config('spider', use_proxy=True)
        
        assert proxies == {'http': 'http://proxy:8080', 'https': 'https://proxy:8080'}
        assert use_pool is False
    
    def test_get_proxies_config_pool_mode(self):
        """Test _get_proxies_config with pool mode."""
        mock_pool = MagicMock()
        mock_pool.get_next_proxy.return_value = {'http': 'http://pool-proxy:8080'}
        
        config = RequestConfig(proxy_mode='pool', proxy_modules=['all'])
        handler = RequestHandler(proxy_pool=mock_pool, config=config)
        
        proxies, use_pool = handler._get_proxies_config('spider', use_proxy=True)
        
        assert proxies == {'http': 'http://pool-proxy:8080'}
        assert use_pool is True
    
    @patch.object(requests.Session, 'get')
    def test_fetch_direct_success(self, mock_get):
        """Test _fetch_direct successful response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html><div class="movie-list">Content</div></html>'
        mock_response.content = mock_response.text.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        html, success, is_turnstile = handler._fetch_direct(
            'http://test.com', None, 'Test'
        )
        
        assert success is True
        assert is_turnstile is False
        assert 'movie-list' in html
    
    @patch.object(requests.Session, 'get')
    def test_fetch_direct_turnstile(self, mock_get):
        """Test _fetch_direct with Turnstile page."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html>Security Verification with turnstile</html>'
        mock_response.content = mock_response.text.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        html, success, is_turnstile = handler._fetch_direct(
            'http://test.com', None, 'Test'
        )
        
        assert success is False
        assert is_turnstile is True
    
    @patch.object(requests.Session, 'get')
    def test_fetch_direct_with_cookie(self, mock_get):
        """Test _fetch_direct with session cookie."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html>Content</html>'
        mock_response.content = mock_response.text.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        config = RequestConfig(javdb_session_cookie='my_session_cookie', use_curl_cffi=False)
        handler = RequestHandler(config=config)
        
        handler._fetch_direct('http://test.com', None, 'Test', use_cookie=True)
        
        # Check that cookie was set in headers
        call_args = mock_get.call_args
        headers = call_args[1]['headers']
        assert '_jdb_session=my_session_cookie' in headers.get('Cookie', '')
    
    @patch.object(RequestHandler, '_fetch_direct')
    def test_get_page_direct_success(self, mock_fetch):
        """Test get_page in direct mode (no CF bypass)."""
        mock_fetch.return_value = ('<html>' + 'x' * 15000 + '</html>', True, False)
        
        handler = RequestHandler()
        result = handler.get_page(
            'http://test.com',
            use_proxy=False,
            use_cf_bypass=False,
            module_name='test'
        )
        
        assert result is not None
        assert len(result) > 10000
    
    @patch.object(RequestHandler, '_fetch_direct')
    def test_get_page_direct_retry_on_turnstile(self, mock_fetch):
        """Test get_page retries on Turnstile detection."""
        # First call returns Turnstile, second succeeds
        mock_fetch.side_effect = [
            ('<html>Security Verification turnstile</html>', False, True),
            ('<html>' + 'x' * 15000 + '</html>', True, False)
        ]
        
        config = RequestConfig(cf_turnstile_cooldown=0)
        handler = RequestHandler(config=config)
        
        result = handler.get_page(
            'http://test.com',
            use_proxy=False,
            use_cf_bypass=False,
            module_name='test',
            max_retries=3
        )
        
        assert result is not None
    
    def test_get_page_cf_bypass_disabled(self):
        """Test get_page when CF bypass is globally disabled."""
        config = RequestConfig(cf_bypass_enabled=False)
        handler = RequestHandler(config=config)
        
        with patch.object(handler, '_get_page_direct') as mock_direct:
            mock_direct.return_value = '<html>content</html>'
            
            handler.get_page(
                'http://test.com',
                use_cf_bypass=True,  # Requested but disabled
                module_name='test'
            )
            
            # Should use direct method since CF bypass is disabled
            mock_direct.assert_called_once()


class TestProxyHelper:
    """Test cases for ProxyHelper class."""
    
    def test_init_default(self):
        """Test default initialization."""
        helper = ProxyHelper()
        
        assert helper.proxy_pool is None
        assert helper.proxy_modules == ['spider']
        assert helper.proxy_mode == 'pool'
        assert helper.proxy_http is None
        assert helper.proxy_https is None
    
    def test_init_with_values(self):
        """Test initialization with values."""
        mock_pool = MagicMock()
        helper = ProxyHelper(
            proxy_pool=mock_pool,
            proxy_modules=['qbittorrent'],
            proxy_mode='pool',
            proxy_http='http://proxy:8080'
        )
        
        assert helper.proxy_pool is mock_pool
        assert helper.proxy_modules == ['qbittorrent']
        assert helper.proxy_mode == 'pool'
        assert helper.proxy_http == 'http://proxy:8080'
    
    def test_get_proxies_dict_no_proxy(self):
        """Test get_proxies_dict when module shouldn't use proxy."""
        helper = ProxyHelper(proxy_modules=['other'])
        
        result = helper.get_proxies_dict('qbittorrent', use_proxy_flag=None)
        
        assert result is None
    
    def test_get_proxies_dict_pool_mode(self):
        """Test get_proxies_dict with pool mode."""
        mock_pool = MagicMock()
        mock_pool.get_current_proxy.return_value = {'http': 'http://pool-proxy:8080'}
        mock_pool.get_current_proxy_name.return_value = 'Pool-Proxy-1'
        
        helper = ProxyHelper(proxy_pool=mock_pool, proxy_mode='pool', proxy_modules=['all'])
        
        result = helper.get_proxies_dict('qbittorrent', use_proxy_flag=True)
        
        assert result == {'http': 'http://pool-proxy:8080'}
    
    def test_get_proxies_dict_legacy_mode(self):
        """Test get_proxies_dict with legacy proxy settings."""
        helper = ProxyHelper(
            proxy_http='http://proxy:8080',
            proxy_https='https://proxy:8080',
            proxy_modules=['all']
        )
        
        result = helper.get_proxies_dict('qbittorrent', use_proxy_flag=True)
        
        assert result == {'http': 'http://proxy:8080', 'https': 'https://proxy:8080'}
    
    def test_get_current_proxy_name_with_pool(self):
        """Test get_current_proxy_name with proxy pool."""
        mock_pool = MagicMock()
        mock_pool.get_current_proxy_name.return_value = 'My-Proxy'
        
        helper = ProxyHelper(proxy_pool=mock_pool)
        
        result = helper.get_current_proxy_name()
        
        assert result == 'My-Proxy'
    
    def test_get_current_proxy_name_legacy(self):
        """Test get_current_proxy_name with legacy proxy."""
        helper = ProxyHelper(proxy_http='http://proxy:8080')
        
        result = helper.get_current_proxy_name()
        
        assert result == 'Legacy-Proxy'
    
    def test_get_current_proxy_name_none(self):
        """Test get_current_proxy_name with no proxy."""
        helper = ProxyHelper()
        
        result = helper.get_current_proxy_name()
        
        assert result == 'None'
    
    def test_mark_success(self):
        """Test mark_success delegates to proxy pool."""
        mock_pool = MagicMock()
        helper = ProxyHelper(proxy_pool=mock_pool)
        
        helper.mark_success()
        
        mock_pool.mark_success.assert_called_once()
    
    def test_mark_success_no_pool(self):
        """Test mark_success with no proxy pool."""
        helper = ProxyHelper()
        
        # Should not raise
        helper.mark_success()
    
    def test_mark_failure_and_switch(self):
        """Test mark_failure_and_switch delegates to proxy pool."""
        mock_pool = MagicMock()
        mock_pool.mark_failure_and_switch.return_value = True
        helper = ProxyHelper(proxy_pool=mock_pool)
        
        result = helper.mark_failure_and_switch()
        
        assert result is True
        mock_pool.mark_failure_and_switch.assert_called_once()
    
    def test_mark_failure_and_switch_no_pool(self):
        """Test mark_failure_and_switch with no proxy pool."""
        helper = ProxyHelper()
        
        result = helper.mark_failure_and_switch()
        
        assert result is False
    
    def test_get_statistics_with_pool(self):
        """Test get_statistics with proxy pool."""
        mock_pool = MagicMock()
        mock_pool.get_statistics.return_value = {'total_proxies': 2}
        helper = ProxyHelper(proxy_pool=mock_pool)
        
        result = helper.get_statistics()
        
        assert result == {'total_proxies': 2}
    
    def test_get_statistics_legacy(self):
        """Test get_statistics with legacy proxy."""
        helper = ProxyHelper(proxy_http='http://proxy:8080')
        
        result = helper.get_statistics()
        
        assert result['total_proxies'] == 1
        assert result['available_proxies'] == 1
    
    def test_get_statistics_no_proxy(self):
        """Test get_statistics with no proxy."""
        helper = ProxyHelper()
        
        result = helper.get_statistics()
        
        assert result['total_proxies'] == 0


class TestCreateFunctions:
    """Test cases for factory functions."""
    
    def test_create_request_handler_from_config(self):
        """Test create_request_handler_from_config."""
        handler = create_request_handler_from_config(
            base_url='https://custom.com',
            cf_bypass_service_port=9000
        )
        
        assert handler.config.base_url == 'https://custom.com'
        assert handler.config.cf_bypass_service_port == 9000
    
    def test_create_request_handler_from_config_with_pool(self):
        """Test create_request_handler_from_config with proxy pool."""
        mock_pool = MagicMock()
        
        handler = create_request_handler_from_config(proxy_pool=mock_pool)
        
        assert handler.proxy_pool is mock_pool
    
    def test_create_proxy_helper_from_config(self):
        """Test create_proxy_helper_from_config."""
        helper = create_proxy_helper_from_config(
            proxy_modules=['qbittorrent'],
            proxy_mode='pool',
            proxy_http='http://proxy:8080'
        )
        
        assert helper.proxy_modules == ['qbittorrent']
        assert helper.proxy_mode == 'pool'
        assert helper.proxy_http == 'http://proxy:8080'
    
    def test_create_proxy_helper_from_config_with_pool(self):
        """Test create_proxy_helper_from_config with proxy pool."""
        mock_pool = MagicMock()
        
        helper = create_proxy_helper_from_config(proxy_pool=mock_pool)
        
        assert helper.proxy_pool is mock_pool


class TestRequestHandlerBrowserHeaders:
    """Test cases for browser headers in RequestHandler."""
    
    def test_browser_headers_exist(self):
        """Test that BROWSER_HEADERS are defined."""
        assert hasattr(RequestHandler, 'BROWSER_HEADERS')
        assert 'User-Agent' in RequestHandler.BROWSER_HEADERS
        assert 'Accept' in RequestHandler.BROWSER_HEADERS
    
    def test_bypass_headers_empty(self):
        """Test that BYPASS_HEADERS are empty."""
        assert RequestHandler.BYPASS_HEADERS == {}


class TestRequestHandlerAdvanced:
    """Advanced test cases for RequestHandler."""
    
    @patch.object(requests.Session, 'get')
    def test_fetch_direct_error_handling(self, mock_get):
        """Test _fetch_direct error handling."""
        mock_get.side_effect = requests.RequestException("Connection failed")
        
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        html, success, is_turnstile = handler._fetch_direct(
            'http://test.com', None, 'Test'
        )
        
        assert html is None
        assert success is False
        assert is_turnstile is False
    
    @patch.object(requests.Session, 'get')
    def test_fetch_direct_without_cookie(self, mock_get):
        """Test _fetch_direct without session cookie."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html><div class="movie-list">Content</div></html>'
        mock_response.content = mock_response.text.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        html, success, is_turnstile = handler._fetch_direct(
            'http://test.com', None, 'Test', use_cookie=False
        )
        
        assert success is True
        # Verify no cookie in headers
        call_args = mock_get.call_args
        headers = call_args[1]['headers']
        assert 'Cookie' not in headers or '_jdb_session' not in headers.get('Cookie', '')
    
    def test_get_proxies_config_single_mode(self):
        """Test _get_proxies_config in single proxy mode."""
        config = RequestConfig(
            proxy_http='http://proxy:8080',
            proxy_mode='single',
            proxy_modules=['all']
        )
        handler = RequestHandler(config=config)
        
        proxies, use_pool = handler._get_proxies_config('spider', use_proxy=True)
        
        assert proxies == {'http': 'http://proxy:8080'}
        assert use_pool is False
    
    @patch.object(RequestHandler, '_fetch_direct')
    @patch.object(RequestHandler, '_fetch_with_cf_bypass')
    def test_get_page_with_cf_bypass(self, mock_bypass, mock_direct):
        """Test get_page with CF bypass enabled."""
        mock_bypass.return_value = ('<html>' + 'x' * 15000 + '</html>', True, False)
        
        config = RequestConfig(cf_bypass_enabled=True)
        handler = RequestHandler(config=config)
        
        result = handler.get_page(
            'http://test.com',
            use_proxy=False,
            use_cf_bypass=True,
            module_name='test'
        )
        
        assert result is not None
        mock_bypass.assert_called()
    
    @patch.object(RequestHandler, '_fetch_direct')
    def test_get_page_small_response(self, mock_fetch):
        """Test get_page with small response (potential failure)."""
        mock_fetch.return_value = ('<html>Small</html>', True, False)
        
        handler = RequestHandler()
        result = handler.get_page(
            'http://test.com',
            use_proxy=False,
            use_cf_bypass=False,
            module_name='test'
        )
        
        # When fetch returns success (True), get_page should return the content
        assert result == '<html>Small</html>'
    
    def test_extract_ip_from_proxy_url_empty(self):
        """Test extract_ip_from_proxy_url with empty URL."""
        result = RequestHandler.extract_ip_from_proxy_url('')
        assert result is None
    
    def test_extract_ip_from_proxy_url_none(self):
        """Test extract_ip_from_proxy_url with None."""
        result = RequestHandler.extract_ip_from_proxy_url(None)
        assert result is None


class TestProxyHelperAdvanced:
    """Advanced test cases for ProxyHelper."""
    
    def test_should_use_proxy_for_module_none_modules(self):
        """Test should_use_proxy_for_module with None modules list."""
        helper = ProxyHelper(proxy_modules=None)
        
        # Should use default ['spider']
        assert helper.should_use_proxy_for_module('spider', use_proxy_flag=None) is True
        assert helper.should_use_proxy_for_module('test', use_proxy_flag=None) is False
    
    def test_get_proxies_dict_single_mode(self):
        """Test get_proxies_dict in single proxy mode."""
        helper = ProxyHelper(
            proxy_http='http://proxy:8080',
            proxy_https='https://proxy:8080',
            proxy_mode='single',
            proxy_modules=['all']
        )
        
        result = helper.get_proxies_dict('test', use_proxy_flag=True)
        
        assert result == {'http': 'http://proxy:8080', 'https': 'https://proxy:8080'}
    
    def test_get_proxies_dict_http_only(self):
        """Test get_proxies_dict with only HTTP proxy."""
        helper = ProxyHelper(
            proxy_http='http://proxy:8080',
            proxy_modules=['all']
        )
        
        result = helper.get_proxies_dict('test', use_proxy_flag=True)
        
        assert result == {'http': 'http://proxy:8080'}
    
    def test_mark_success_with_pool(self):
        """Test mark_success with proxy pool."""
        mock_pool = MagicMock()
        helper = ProxyHelper(proxy_pool=mock_pool)
        
        helper.mark_success()
        
        mock_pool.mark_success.assert_called_once()
    
    def test_mark_failure_and_switch_with_pool_in_helper(self):
        """Test mark_failure_and_switch with proxy pool via ProxyHelper."""
        mock_pool = MagicMock()
        mock_pool.mark_failure_and_switch.return_value = True
        helper = ProxyHelper(proxy_pool=mock_pool)
        
        result = helper.mark_failure_and_switch()
        
        assert result is True


class TestRequestConfigAdvanced:
    """Advanced test cases for RequestConfig."""
    
    def test_all_config_options(self):
        """Test RequestConfig with all options."""
        config = RequestConfig(
            base_url='https://custom.com',
            cf_bypass_service_port=9000,
            cf_bypass_enabled=False,
            cf_bypass_max_failures=5,
            cf_turnstile_cooldown=20,
            fallback_cooldown=60,
            javdb_session_cookie='session123',
            proxy_http='http://proxy:8080',
            proxy_https='https://proxy:8080',
            proxy_modules=['spider', 'qbittorrent'],
            proxy_mode='pool'
        )
        
        assert config.base_url == 'https://custom.com'
        assert config.cf_bypass_service_port == 9000
        assert config.cf_bypass_enabled is False
        assert config.cf_bypass_max_failures == 5
        assert config.cf_turnstile_cooldown == 20
        assert config.fallback_cooldown == 60
        assert config.javdb_session_cookie == 'session123'
        assert config.proxy_http == 'http://proxy:8080'
        assert config.proxy_https == 'https://proxy:8080'
        assert config.proxy_modules == ['spider', 'qbittorrent']
        assert config.proxy_mode == 'pool'


class TestCurlCffiCookieHandling:
    """Test cases for curl_cffi session cookie handling."""
    
    def test_no_cookie_clear_without_manual_cookie(self):
        """Test that session cookies are NOT cleared when no manual Cookie header is set.
        
        This ensures server-set cookies (cf_clearance, over18, etc.) persist across requests.
        """
        config = RequestConfig(use_curl_cffi=True)
        handler = RequestHandler(config=config)
        
        # Skip if curl_cffi is not available
        if not handler.use_curl_cffi:
            pytest.skip("curl_cffi not available")
        
        # Set up mock session cookies
        handler.curl_cffi_session.cookies.set('cf_clearance', 'test_clearance_value')
        handler.curl_cffi_session.cookies.set('over18', '1')
        
        # Mock the session.get to avoid actual request
        with patch.object(handler.curl_cffi_session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '<html>Content</html>'
            mock_response.content = b'<html>Content</html>'
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            
            # Request without Cookie header
            headers = {'User-Agent': 'Test'}
            handler._do_request_curl_cffi(
                'http://test.com', headers, None, timeout=30, context_msg='Test'
            )
            
            # Verify session cookies are NOT cleared
            assert handler.curl_cffi_session.cookies.get('cf_clearance') == 'test_clearance_value'
            assert handler.curl_cffi_session.cookies.get('over18') == '1'
    
    def test_cookie_merge_with_manual_cookie(self):
        """Test that session cookies are merged with manual Cookie header.
        
        Manual cookies take priority, session cookies are added if not duplicate.
        """
        config = RequestConfig(use_curl_cffi=True)
        handler = RequestHandler(config=config)
        
        # Skip if curl_cffi is not available
        if not handler.use_curl_cffi:
            pytest.skip("curl_cffi not available")
        
        # Set up mock session cookies
        handler.curl_cffi_session.cookies.set('cf_clearance', 'session_clearance')
        handler.curl_cffi_session.cookies.set('over18', '1')
        handler.curl_cffi_session.cookies.set('__cf_bm', 'bm_token')
        
        # Mock the session.get to capture headers
        with patch.object(handler.curl_cffi_session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '<html>Content</html>'
            mock_response.content = b'<html>Content</html>'
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            
            # Request WITH manual Cookie header
            headers = {'User-Agent': 'Test', 'Cookie': '_jdb_session=my_session_cookie'}
            handler._do_request_curl_cffi(
                'http://test.com', headers, None, timeout=30, context_msg='Test'
            )
            
            # Verify the headers passed to get() contain merged cookies
            call_args = mock_get.call_args
            passed_headers = call_args[1]['headers']
            cookie_header = passed_headers.get('Cookie', '')
            
            # Manual cookie should be present
            assert '_jdb_session=my_session_cookie' in cookie_header
            # Session cookies should be merged in
            assert 'cf_clearance=session_clearance' in cookie_header
            assert 'over18=1' in cookie_header
            assert '__cf_bm=bm_token' in cookie_header
            
            # Session cookies should be cleared after request
            assert handler.curl_cffi_session.cookies.get('cf_clearance') is None
    
    def test_manual_cookie_priority_over_session(self):
        """Test that manual cookies take priority over session cookies with same name."""
        config = RequestConfig(use_curl_cffi=True)
        handler = RequestHandler(config=config)
        
        # Skip if curl_cffi is not available
        if not handler.use_curl_cffi:
            pytest.skip("curl_cffi not available")
        
        # Set up session cookie with same name as manual cookie
        handler.curl_cffi_session.cookies.set('_jdb_session', 'old_session_value')
        handler.curl_cffi_session.cookies.set('cf_clearance', 'clearance_value')
        
        # Mock the session.get to capture headers
        with patch.object(handler.curl_cffi_session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '<html>Content</html>'
            mock_response.content = b'<html>Content</html>'
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            
            # Request WITH manual Cookie header (same name as session cookie)
            headers = {'User-Agent': 'Test', 'Cookie': '_jdb_session=new_session_value'}
            handler._do_request_curl_cffi(
                'http://test.com', headers, None, timeout=30, context_msg='Test'
            )
            
            # Verify the headers
            call_args = mock_get.call_args
            passed_headers = call_args[1]['headers']
            cookie_header = passed_headers.get('Cookie', '')
            
            # Manual cookie should be present (priority)
            assert '_jdb_session=new_session_value' in cookie_header
            # Old session cookie with same name should NOT be duplicated
            assert cookie_header.count('_jdb_session=') == 1
            # Other session cookies should be merged
            assert 'cf_clearance=clearance_value' in cookie_header
    
    def test_empty_session_cookies_with_manual_cookie(self):
        """Test behavior when session has no cookies but manual Cookie is set."""
        config = RequestConfig(use_curl_cffi=True)
        handler = RequestHandler(config=config)
        
        # Skip if curl_cffi is not available
        if not handler.use_curl_cffi:
            pytest.skip("curl_cffi not available")
        
        # Ensure session cookies are empty
        handler.curl_cffi_session.cookies.clear()
        
        # Mock the session.get to capture headers
        with patch.object(handler.curl_cffi_session, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '<html>Content</html>'
            mock_response.content = b'<html>Content</html>'
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            
            # Request WITH manual Cookie header
            headers = {'User-Agent': 'Test', 'Cookie': '_jdb_session=my_session'}
            handler._do_request_curl_cffi(
                'http://test.com', headers, None, timeout=30, context_msg='Test'
            )
            
            # Verify the headers - only manual cookie should be present
            call_args = mock_get.call_args
            passed_headers = call_args[1]['headers']
            cookie_header = passed_headers.get('Cookie', '')
            
            assert cookie_header == '_jdb_session=my_session'


BAN_PAGE_HTML = (
    "\nThe owner of this website has banned your access based on your browser's behaving\n\n"
    "IP: xxx.xxx.xxx.xxx\n\n"
    "基於你的異常行為，管理員禁止了你的訪問，將在3-7日後解除。\n"
    "群組和反饋不受理此問題，請耐心等待解除或更換網絡節點進行訪問。\n"
)


class TestIsBanPage:
    """Test cases for RequestHandler.is_ban_page static method."""

    def test_is_ban_page_with_ban_html(self):
        """Ban page HTML should be detected."""
        assert RequestHandler.is_ban_page(BAN_PAGE_HTML) is True

    def test_is_ban_page_with_chinese_only(self):
        """Chinese-only ban indicator should be detected."""
        assert RequestHandler.is_ban_page("管理員禁止了你的訪問") is True

    def test_is_ban_page_with_english_only(self):
        """English-only ban indicator should be detected."""
        assert RequestHandler.is_ban_page("banned your access") is True

    def test_is_ban_page_with_normal_html(self):
        """Normal HTML should not be flagged."""
        normal_html = '<html><div class="movie-list">Content</div></html>'
        assert RequestHandler.is_ban_page(normal_html) is False

    def test_is_ban_page_with_empty(self):
        """Empty/None inputs should return False."""
        assert RequestHandler.is_ban_page('') is False
        assert RequestHandler.is_ban_page(None) is False


class TestDoRequest403:
    """Test _do_request returns body on HTTP 403."""

    @patch.object(requests.Session, 'get')
    def test_do_request_403_returns_body(self, mock_get):
        """HTTP 403 should return (body, error) so callers can inspect ban HTML."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = BAN_PAGE_HTML
        mock_get.return_value = mock_response

        handler = RequestHandler()
        html, error = handler._do_request(
            'http://test.com', {}, None, timeout=30, context_msg='Test'
        )

        assert html == BAN_PAGE_HTML
        assert error is not None
        assert isinstance(error, requests.HTTPError)


class TestProxyBanDetection:
    """Test ProxyBannedError raise/catch flow."""

    @patch.object(RequestHandler, '_do_request_curl_cffi')
    @patch.object(RequestHandler, '_do_request')
    def test_fetch_direct_raises_proxy_banned_error(self, mock_do, mock_curl):
        """_fetch_direct should raise ProxyBannedError when ban page is detected."""
        mock_curl.return_value = (None, Exception("skip"))
        mock_do.return_value = (BAN_PAGE_HTML, requests.HTTPError("403"))

        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        with pytest.raises(ProxyBannedError) as exc_info:
            handler._fetch_direct('http://test.com', None, 'Test', proxy_name='proxy-1')

        assert exc_info.value.proxy_name == 'proxy-1'
        assert 'ban page' in exc_info.value.reason

    @patch.object(RequestHandler, '_do_request_curl_cffi')
    @patch.object(RequestHandler, '_do_request')
    def test_fetch_direct_normal_403_no_ban(self, mock_do, mock_curl):
        """403 with non-ban HTML should NOT raise ProxyBannedError."""
        non_ban_html = '<html>Access Denied</html>'
        mock_curl.return_value = (None, Exception("skip"))
        mock_do.return_value = (non_ban_html, requests.HTTPError("403"))

        handler = RequestHandler(config=RequestConfig(use_curl_cffi=False))
        html, success, is_turnstile = handler._fetch_direct(
            'http://test.com', None, 'Test', proxy_name='proxy-1'
        )
        assert html == non_ban_html
        assert success is False

    @patch.object(RequestHandler, '_get_page_direct')
    def test_get_page_catches_proxy_banned_and_bans(self, mock_direct):
        """get_page should catch ProxyBannedError, call ban_proxy, and re-raise."""
        mock_direct.side_effect = ProxyBannedError('proxy-1', 'ban page detected')

        mock_pool = MagicMock()
        mock_pool.get_current_proxy_name.return_value = 'proxy-1'

        config = RequestConfig(cf_bypass_enabled=False)
        handler = RequestHandler(proxy_pool=mock_pool, config=config)

        with pytest.raises(ProxyBannedError):
            handler.get_page(
                'http://test.com', use_proxy=True, module_name='spider'
            )

        mock_pool.ban_proxy.assert_called_once_with('proxy-1')


class TestCfBypassBanThreshold:
    """Test CF bypass consecutive failure auto-ban via ProxyBannedError."""

    def _make_handler(self, ban_threshold=4, max_failures=2):
        mock_pool = MagicMock()
        mock_pool.get_current_proxy_name.return_value = 'test-proxy'
        config = RequestConfig(
            cf_bypass_enabled=True,
            cf_bypass_max_failures=max_failures,
            cf_bypass_ban_threshold=ban_threshold,
            fallback_cooldown=0,
            between_attempt_sleep=lambda: 0.0,
        )
        handler = RequestHandler(proxy_pool=mock_pool, config=config)
        return handler, mock_pool

    @patch.object(RequestHandler, '_fetch_direct')
    @patch.object(RequestHandler, '_fetch_with_cf_bypass')
    def test_below_threshold_returns_none(self, mock_bypass, mock_direct):
        """Failures below ban_threshold should return None, not raise."""
        mock_bypass.return_value = (None, False, False)
        mock_direct.return_value = (None, False, False)
        handler, _ = self._make_handler(ban_threshold=6)

        result = handler.get_page(
            'http://test.com', use_proxy=False,
            use_cf_bypass=True, module_name='test', max_retries=1,
        )
        assert result is None
        assert handler.cf_bypass_failure_count < 6

    @patch.object(RequestHandler, '_fetch_direct')
    @patch.object(RequestHandler, '_fetch_with_cf_bypass')
    def test_reaching_threshold_raises_proxy_banned(self, mock_bypass, mock_direct):
        """Once cf_bypass_failure_count >= ban_threshold, ProxyBannedError is raised."""
        mock_bypass.return_value = (None, False, False)
        mock_direct.return_value = (None, False, False)
        handler, mock_pool = self._make_handler(ban_threshold=4)
        handler.cf_bypass_failure_count = 3

        with pytest.raises(ProxyBannedError) as exc_info:
            handler.get_page(
                'http://test.com', use_proxy=False,
                use_cf_bypass=True, module_name='test', max_retries=1,
            )
        assert 'consecutive' in exc_info.value.reason
        mock_pool.ban_proxy.assert_called()

    @patch.object(RequestHandler, '_fetch_with_cf_bypass')
    def test_success_resets_counter(self, mock_bypass):
        """A successful CF bypass should reset cf_bypass_failure_count to 0."""
        mock_bypass.return_value = ('<html>' + 'x' * 15000 + '</html>', True, False)
        handler, _ = self._make_handler(ban_threshold=4)
        handler.cf_bypass_failure_count = 3

        result = handler.get_page(
            'http://test.com', use_proxy=False,
            use_cf_bypass=True, module_name='test', max_retries=1,
        )
        assert result is not None
        assert handler.cf_bypass_failure_count == 0

    def test_default_ban_threshold(self):
        """Default cf_bypass_ban_threshold should be 6."""
        config = RequestConfig()
        assert config.cf_bypass_ban_threshold == 6


class TestPauseBetweenAttempts:
    """Test _pause_between_attempts with and without injected callable."""

    def test_uses_injected_callable(self):
        call_count = 0

        def fake_sleep():
            nonlocal call_count
            call_count += 1
            return 0.0

        handler = RequestHandler(
            config=RequestConfig(between_attempt_sleep=fake_sleep)
        )
        handler._pause_between_attempts(legacy_seconds=30)
        assert call_count == 1

    @patch('packages.python.javdb_platform.request_handler.time.sleep')
    def test_legacy_fallback(self, mock_sleep):
        handler = RequestHandler(config=RequestConfig(between_attempt_sleep=None))
        handler._pause_between_attempts(legacy_seconds=5)
        mock_sleep.assert_called_once_with(5)

    @patch('packages.python.javdb_platform.request_handler.time.sleep')
    def test_legacy_zero_skips_sleep(self, mock_sleep):
        handler = RequestHandler(config=RequestConfig(between_attempt_sleep=None))
        handler._pause_between_attempts(legacy_seconds=0)
        mock_sleep.assert_not_called()

    @patch.object(RequestHandler, '_fetch_direct')
    def test_cf_fallback_calls_injected_sleep(self, mock_fetch):
        """CF bypass fallback steps should invoke injected sleep, not time.sleep."""
        call_count = 0

        def counting_sleep():
            nonlocal call_count
            call_count += 1
            return 0.0

        mock_fetch.return_value = (None, False, False)
        config = RequestConfig(
            cf_bypass_enabled=True,
            fallback_cooldown=30,
            between_attempt_sleep=counting_sleep,
        )
        handler = RequestHandler(config=config)

        with patch.object(handler, '_fetch_with_cf_bypass',
                          return_value=('<html>small</html>', False, False)):
            handler.get_page(
                'http://test.com',
                use_proxy=False,
                use_cf_bypass=True,
                module_name='test',
                max_retries=1,
            )

        assert call_count >= 1, "injected sleep should be called during CF fallback"

    @patch.object(RequestHandler, '_fetch_direct')
    def test_turnstile_retry_calls_injected_sleep(self, mock_fetch):
        """Turnstile retry in _get_page_direct should use injected sleep."""
        call_count = 0

        def counting_sleep():
            nonlocal call_count
            call_count += 1
            return 0.0

        mock_fetch.side_effect = [
            ('<html>Security Verification turnstile</html>', False, True),
            ('<html>' + 'x' * 15000 + '</html>', True, False),
        ]
        config = RequestConfig(
            cf_turnstile_cooldown=10,
            between_attempt_sleep=counting_sleep,
        )
        handler = RequestHandler(config=config)

        result = handler.get_page(
            'http://test.com',
            use_proxy=False,
            use_cf_bypass=False,
            module_name='test',
            max_retries=3,
        )

        assert result is not None
        assert call_count >= 1, "injected sleep should be called on Turnstile retry"


# ─────────────────────────────────────────────────────────────────────────
# P2-D — on_request_complete callback (per-attempt success/failure +
# latency).  Verifies that target-site requests through ``_do_request``
# emit telemetry events while local CF-bypass requests do NOT poison the
# proxy-quality metric.
# ─────────────────────────────────────────────────────────────────────────


class TestRequestCompleteCallback:
    @patch.object(requests.Session, 'get')
    def test_emits_success_with_latency_when_report_health_enabled(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html>ok</html>'
        mock_response.content = b'<html>ok</html>'
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )
        handler._do_request(
            'http://target.com', {}, None,
            timeout=30, context_msg='Test',
            proxy_name='proxy-X', report_health=True,
        )
        assert len(events) == 1
        proxy_name, kind, latency_ms = events[0]
        assert proxy_name == 'proxy-X'
        assert kind == 'success'
        assert latency_ms >= 0

    @patch.object(requests.Session, 'get')
    def test_emits_failure_on_request_exception(self, mock_get):
        mock_get.side_effect = requests.RequestException("simulated outage")
        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )
        handler._do_request(
            'http://target.com', {}, None,
            timeout=30, context_msg='Test',
            proxy_name='proxy-X', report_health=True,
        )
        assert len(events) == 1
        assert events[0][1] == 'failure'

    @patch.object(requests.Session, 'get')
    def test_emits_failure_on_403_forbidden(self, mock_get):
        """A 403 carries a body but is treated as a failure for health metrics."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '<html>banned</html>'
        mock_response.content = b'<html>banned</html>'
        mock_get.return_value = mock_response

        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )
        handler._do_request(
            'http://target.com', {}, None,
            timeout=30, context_msg='Test',
            proxy_name='proxy-X', report_health=True,
        )
        assert len(events) == 1
        assert events[0][1] == 'failure'

    @patch.object(requests.Session, 'get')
    def test_does_not_emit_when_report_health_false(self, mock_get):
        """CF-bypass-service calls (report_health=False) must NOT emit events."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html>ok</html>'
        mock_response.content = b'<html>ok</html>'
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )
        handler._do_request(
            'http://target.com', {}, None,
            timeout=30, context_msg='Test',
            # Default report_health=False → silent.
        )
        assert events == []

    @patch.object(requests.Session, 'get')
    def test_callback_exception_does_not_break_request(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html>ok</html>'
        mock_response.content = b'<html>ok</html>'
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        def boom(*_args):
            raise RuntimeError("simulated coordinator outage")

        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=boom,
        )
        # Must NOT raise.
        html, error = handler._do_request(
            'http://target.com', {}, None,
            timeout=30, context_msg='Test',
            proxy_name='proxy-X', report_health=True,
        )
        assert html == '<html>ok</html>'
        assert error is None

    @patch.object(requests.Session, 'get')
    def test_fetch_direct_propagates_proxy_name_and_health_flag(self, mock_get):
        """``_fetch_direct`` is the request hot path — make sure it threads
        through real proxy_name + report_health=True so the telemetry pipeline
        actually fires for spider-issued target-site requests."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html><div class="movie-list">x</div></html>'
        mock_response.content = mock_response.text.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )
        handler._fetch_direct(
            'http://javdb.com/v/abc', None, 'Test',
            proxy_name='proxy-Y',
        )
        assert len(events) == 1
        assert events[0][0] == 'proxy-Y'
        assert events[0][1] == 'success'

    @patch.object(requests.Session, 'get')
    def test_fetch_direct_does_not_report_sentinel_none_proxy(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html><div class="movie-list">x</div></html>'
        mock_response.content = mock_response.text.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )
        handler._fetch_direct(
            'http://javdb.com/v/abc', None, 'Test',
            proxy_name='None',
        )
        assert events == []

    @patch.object(requests.Session, 'get')
    def test_fetch_direct_does_not_report_turnstile_as_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html>Security Verification turnstile</html>'
        mock_response.content = mock_response.text.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )
        html, success, is_turnstile = handler._fetch_direct(
            'http://javdb.com/v/abc', None, 'Test',
            proxy_name='proxy-Y',
        )

        assert html == mock_response.text
        assert success is False
        assert is_turnstile is True
        assert events == []

    @patch.object(requests.Session, 'get')
    def test_fetch_direct_reports_ban_page_as_failure(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html>banned your access</html>'
        mock_response.content = mock_response.text.encode()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )

        with pytest.raises(ProxyBannedError):
            handler._fetch_direct(
                'http://javdb.com/v/abc', None, 'Test',
                proxy_name='proxy-Y',
            )
        assert len(events) == 1
        assert events[0][0] == 'proxy-Y'
        assert events[0][1] == 'failure'

    @patch.object(requests.Session, 'get')
    def test_negative_latency_is_clamped_to_zero_at_callback(self, mock_get):
        """Defence-in-depth — clock skew (monotonic going backwards is
        impossible but the callback still validates) must produce a
        non-negative latency_ms."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = 'ok'
        mock_response.content = b'ok'
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        events = []
        handler = RequestHandler(
            config=RequestConfig(use_curl_cffi=False),
            on_request_complete=lambda *args: events.append(args),
        )
        # Direct call to the helper bypasses the timing measurement.
        handler._record_request_complete('proxy-X', 'success', -50)
        assert events == [('proxy-X', 'success', 0)]
