"""
Unit tests for utils/proxy_pool.py
"""
import pytest
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from utils.proxy_pool import (
    ProxyInfo,
    ProxyPool,
    mask_proxy_url,
    create_proxy_pool_from_config
)


class TestMaskProxyUrl:
    """Tests for mask_proxy_url function"""
    
    def test_mask_credentials_and_ip(self):
        """Test masking credentials and IP address"""
        url = "http://user:pass@123.45.67.89:8080"
        masked = mask_proxy_url(url)
        
        assert "***:***@" in masked
        assert "123.xxx.xxx.89" in masked
        assert "user" not in masked
        assert "pass" not in masked
    
    def test_mask_ip_without_credentials(self):
        """Test masking IP without credentials"""
        url = "http://123.45.67.89:8080"
        masked = mask_proxy_url(url)
        
        assert "123.xxx.xxx.89" in masked
    
    def test_mask_domain_unchanged(self):
        """Test that domain names are not masked"""
        url = "http://proxy.example.com:8080"
        masked = mask_proxy_url(url)
        
        assert "proxy.example.com" in masked
    
    def test_mask_none_url(self):
        """Test masking None URL"""
        masked = mask_proxy_url(None)
        assert masked == 'None'


class TestProxyInfo:
    """Tests for ProxyInfo class"""
    
    @pytest.fixture
    def proxy_info(self):
        """Create a sample ProxyInfo instance"""
        return ProxyInfo(
            http_url="http://proxy.example.com:8080",
            https_url="http://proxy.example.com:8080",
            name="TestProxy"
        )
    
    def test_get_proxies_dict(self, proxy_info):
        """Test getting proxies dictionary for requests"""
        proxies = proxy_info.get_proxies_dict()
        
        assert 'http' in proxies
        assert 'https' in proxies
        assert proxies['http'] == "http://proxy.example.com:8080"
    
    def test_mark_success(self, proxy_info):
        """Test marking proxy as successful"""
        proxy_info.mark_success()
        
        assert proxy_info.successful_requests == 1
        assert proxy_info.total_requests == 1
        assert proxy_info.failures == 0
        assert proxy_info.is_available is True
        assert proxy_info.last_success is not None
    
    def test_mark_failure(self, proxy_info):
        """Test marking proxy as failed"""
        proxy_info.mark_failure(cooldown_seconds=300)
        
        assert proxy_info.failures == 1
        assert proxy_info.total_requests == 1
        assert proxy_info.is_available is False
        assert proxy_info.cooldown_until is not None
    
    def test_is_in_cooldown(self, proxy_info):
        """Test checking cooldown status"""
        assert proxy_info.is_in_cooldown() is False
        
        proxy_info.mark_failure(cooldown_seconds=300)
        assert proxy_info.is_in_cooldown() is True
    
    def test_get_success_rate(self, proxy_info):
        """Test calculating success rate"""
        assert proxy_info.get_success_rate() == 0.0
        
        proxy_info.mark_success()
        assert proxy_info.get_success_rate() == 1.0
        
        proxy_info.mark_failure()
        assert proxy_info.get_success_rate() == 0.5


class TestProxyPool:
    """Tests for ProxyPool class"""
    
    @pytest.fixture
    def temp_ban_log(self):
        """Create temporary ban log file"""
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)
    
    @pytest.fixture
    def proxy_pool(self, temp_ban_log):
        """Create a ProxyPool instance with temporary ban log"""
        return ProxyPool(
            cooldown_seconds=300,
            max_failures_before_cooldown=3,
            ban_log_file=temp_ban_log
        )
    
    def test_add_proxy(self, proxy_pool):
        """Test adding a proxy to the pool"""
        proxy_pool.add_proxy(
            http_url="http://proxy1.example.com:8080",
            https_url="http://proxy1.example.com:8080",
            name="Proxy1"
        )
        
        assert len(proxy_pool.proxies) == 1
        assert proxy_pool.proxies[0].name == "Proxy1"
    
    def test_add_proxies_from_list(self, proxy_pool):
        """Test adding multiple proxies from list"""
        proxy_list = [
            {
                'http': 'http://proxy1.example.com:8080',
                'https': 'http://proxy1.example.com:8080',
                'name': 'Proxy1'
            },
            {
                'http': 'http://proxy2.example.com:8080',
                'https': 'http://proxy2.example.com:8080',
                'name': 'Proxy2'
            }
        ]
        
        proxy_pool.add_proxies_from_list(proxy_list)
        
        assert len(proxy_pool.proxies) == 2
    
    def test_get_current_proxy(self, proxy_pool):
        """Test getting current proxy"""
        proxy_pool.add_proxy(
            http_url="http://proxy1.example.com:8080",
            name="Proxy1"
        )
        
        current = proxy_pool.get_current_proxy()
        
        assert current is not None
        assert 'http' in current
    
    def test_get_current_proxy_no_proxy_mode(self, proxy_pool):
        """Test getting current proxy in no-proxy mode"""
        proxy_pool.enable_no_proxy_mode()
        
        current = proxy_pool.get_current_proxy()
        assert current is None
    
    def test_mark_success(self, proxy_pool):
        """Test marking current proxy as successful"""
        proxy_pool.add_proxy(
            http_url="http://proxy1.example.com:8080",
            name="Proxy1"
        )
        
        proxy_pool.mark_success()
        
        assert proxy_pool.proxies[0].successful_requests == 1
    
    def test_mark_failure_and_switch(self, proxy_pool):
        """Test marking proxy as failed and switching"""
        proxy_pool.add_proxy(
            http_url="http://proxy1.example.com:8080",
            name="Proxy1"
        )
        proxy_pool.add_proxy(
            http_url="http://proxy2.example.com:8080",
            name="Proxy2"
        )
        
        # First failure
        switched = proxy_pool.mark_failure_and_switch()
        assert switched is True
        assert proxy_pool.current_index == 1
    
    def test_mark_failure_multiple_times_triggers_cooldown(self, proxy_pool):
        """Test that multiple failures trigger cooldown"""
        proxy_pool.add_proxy(
            http_url="http://proxy1.example.com:8080",
            name="Proxy1"
        )
        proxy_pool.add_proxy(
            http_url="http://proxy2.example.com:8080",
            name="Proxy2"
        )
        
        # Fail 3 times to trigger cooldown
        for _ in range(3):
            proxy_pool.mark_failure_and_switch()
        
        # First proxy should be in cooldown
        assert proxy_pool.proxies[0].is_in_cooldown()
    
    def test_get_statistics(self, proxy_pool):
        """Test getting proxy pool statistics"""
        proxy_pool.add_proxy(
            http_url="http://proxy1.example.com:8080",
            name="Proxy1"
        )
        
        stats = proxy_pool.get_statistics()
        
        assert stats['total_proxies'] == 1
        assert stats['available_proxies'] == 1
        assert len(stats['proxies']) == 1
    
    def test_enable_disable_no_proxy_mode(self, proxy_pool):
        """Test enabling and disabling no-proxy mode"""
        assert proxy_pool.no_proxy_mode is False
        
        proxy_pool.enable_no_proxy_mode()
        assert proxy_pool.no_proxy_mode is True
        
        proxy_pool.disable_no_proxy_mode()
        assert proxy_pool.no_proxy_mode is False
    
    def test_get_current_proxy_name(self, proxy_pool):
        """Test getting current proxy name"""
        proxy_pool.add_proxy(
            http_url="http://proxy1.example.com:8080",
            name="Proxy1"
        )
        
        name = proxy_pool.get_current_proxy_name()
        assert name == "Proxy1"
    
    def test_no_proxies_available(self, proxy_pool):
        """Test behavior when no proxies are available"""
        current = proxy_pool.get_current_proxy()
        assert current is None
    
    def test_all_proxies_in_cooldown(self, proxy_pool):
        """Test behavior when all proxies are in cooldown"""
        proxy_pool.add_proxy(
            http_url="http://proxy1.example.com:8080",
            name="Proxy1"
        )
        
        # Force cooldown
        proxy_pool.proxies[0].mark_failure(cooldown_seconds=300)
        
        current = proxy_pool.get_current_proxy()
        assert current is None


class TestCreateProxyPoolFromConfig:
    """Tests for create_proxy_pool_from_config function"""
    
    def test_create_pool_from_config(self):
        """Test creating proxy pool from configuration"""
        proxy_config = [
            {
                'http': 'http://proxy1.example.com:8080',
                'https': 'http://proxy1.example.com:8080',
                'name': 'Proxy1'
            }
        ]
        
        pool = create_proxy_pool_from_config(
            proxy_config,
            cooldown_seconds=300,
            max_failures=3
        )
        
        assert len(pool.proxies) == 1
        assert pool.cooldown_seconds == 300
        assert pool.max_failures_before_cooldown == 3
