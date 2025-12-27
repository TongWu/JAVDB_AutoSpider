"""
Unit tests for utils/request_handler.py
"""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, Mock
import requests

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.request_handler import (
    RequestConfig,
    RequestHandler,
    ProxyHelper,
    create_request_handler_from_config,
    create_proxy_helper_from_config
)


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
        assert config.proxy_modules == ['all']
        assert config.proxy_mode == 'single'
    
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
        
        assert config.proxy_modules == ['all']


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
    
    def test_should_use_proxy_for_module_false_no_flag(self):
        """Test should_use_proxy_for_module returns False when flag is False."""
        handler = RequestHandler()
        
        result = handler.should_use_proxy_for_module('spider_index', use_proxy_flag=False)
        
        assert result is False
    
    def test_should_use_proxy_for_module_all(self):
        """Test should_use_proxy_for_module with 'all' in modules."""
        config = RequestConfig(proxy_modules=['all'])
        handler = RequestHandler(config=config)
        
        result = handler.should_use_proxy_for_module('spider_index', use_proxy_flag=True)
        
        assert result is True
    
    def test_should_use_proxy_for_module_specific(self):
        """Test should_use_proxy_for_module with specific module."""
        config = RequestConfig(proxy_modules=['spider_index', 'spider_detail'])
        handler = RequestHandler(config=config)
        
        assert handler.should_use_proxy_for_module('spider_index', use_proxy_flag=True) is True
        assert handler.should_use_proxy_for_module('spider_other', use_proxy_flag=True) is False
    
    def test_should_use_proxy_for_module_empty_list(self):
        """Test should_use_proxy_for_module with empty module list."""
        config = RequestConfig(proxy_modules=[])
        handler = RequestHandler(config=config)
        
        result = handler.should_use_proxy_for_module('spider_index', use_proxy_flag=True)
        
        assert result is False
    
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
        
        proxies, use_pool = handler._get_proxies_config('spider_index', use_proxy=False)
        
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
        
        proxies, use_pool = handler._get_proxies_config('spider_index', use_proxy=True)
        
        assert proxies == {'http': 'http://proxy:8080', 'https': 'https://proxy:8080'}
        assert use_pool is False
    
    def test_get_proxies_config_pool_mode(self):
        """Test _get_proxies_config with pool mode."""
        mock_pool = MagicMock()
        mock_pool.get_next_proxy.return_value = {'http': 'http://pool-proxy:8080'}
        
        config = RequestConfig(proxy_mode='pool', proxy_modules=['all'])
        handler = RequestHandler(proxy_pool=mock_pool, config=config)
        
        proxies, use_pool = handler._get_proxies_config('spider_index', use_proxy=True)
        
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
        
        handler = RequestHandler()
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
        
        handler = RequestHandler()
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
        
        config = RequestConfig(javdb_session_cookie='my_session_cookie')
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
        assert helper.proxy_modules == ['all']
        assert helper.proxy_mode == 'single'
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
    
    def test_should_use_proxy_for_module_false_no_flag(self):
        """Test should_use_proxy_for_module returns False when flag is False."""
        helper = ProxyHelper()
        
        result = helper.should_use_proxy_for_module('qbittorrent', use_proxy_flag=False)
        
        assert result is False
    
    def test_should_use_proxy_for_module_all(self):
        """Test should_use_proxy_for_module with 'all' in modules."""
        helper = ProxyHelper(proxy_modules=['all'])
        
        result = helper.should_use_proxy_for_module('qbittorrent', use_proxy_flag=True)
        
        assert result is True
    
    def test_should_use_proxy_for_module_specific(self):
        """Test should_use_proxy_for_module with specific module."""
        helper = ProxyHelper(proxy_modules=['qbittorrent', 'pikpak'])
        
        assert helper.should_use_proxy_for_module('qbittorrent', use_proxy_flag=True) is True
        assert helper.should_use_proxy_for_module('other', use_proxy_flag=True) is False
    
    def test_should_use_proxy_for_module_empty_list(self):
        """Test should_use_proxy_for_module with empty module list."""
        helper = ProxyHelper(proxy_modules=[])
        
        result = helper.should_use_proxy_for_module('qbittorrent', use_proxy_flag=True)
        
        assert result is False
    
    def test_get_proxies_dict_no_proxy(self):
        """Test get_proxies_dict when module shouldn't use proxy."""
        helper = ProxyHelper(proxy_modules=['other'])
        
        result = helper.get_proxies_dict('qbittorrent', use_proxy_flag=True)
        
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
        
        handler = RequestHandler()
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
        
        handler = RequestHandler()
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
        
        proxies, use_pool = handler._get_proxies_config('spider_index', use_proxy=True)
        
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
        
        # Should use default ['all']
        result = helper.should_use_proxy_for_module('test', use_proxy_flag=True)
        assert result is True
    
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
            proxy_modules=['spider_index', 'spider_detail'],
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
        assert config.proxy_modules == ['spider_index', 'spider_detail']
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

