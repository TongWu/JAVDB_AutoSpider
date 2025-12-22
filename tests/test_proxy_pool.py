"""
Unit tests for utils/proxy_pool.py
"""
import os
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.proxy_pool import (
    mask_proxy_url,
    ProxyInfo,
    ProxyPool,
    create_proxy_pool_from_config
)


class TestMaskProxyUrl:
    """Test cases for mask_proxy_url function."""
    
    def test_mask_none_url(self):
        """Test masking None URL."""
        result = mask_proxy_url(None)
        assert result == 'None'
    
    def test_mask_empty_url(self):
        """Test masking empty URL."""
        result = mask_proxy_url('')
        assert result == 'None'
    
    def test_mask_url_with_credentials_and_ip(self):
        """Test masking URL with credentials and IP."""
        url = "http://user:pass@123.45.67.89:8080"
        result = mask_proxy_url(url)
        
        # Credentials should be masked
        assert 'user' not in result
        assert 'pass' not in result
        assert '***:***@' in result
        
        # Middle octets of IP should be masked
        assert '123.' in result
        assert '.89:8080' in result
        assert '.45.67.' not in result
        assert '.xxx.xxx.' in result
    
    def test_mask_url_with_ip_no_credentials(self):
        """Test masking URL with IP but no credentials."""
        url = "http://123.45.67.89:8080"
        result = mask_proxy_url(url)
        
        # IP should be partially masked
        assert '123.' in result
        assert '.89:8080' in result
        assert '.xxx.xxx.' in result
    
    def test_mask_url_with_hostname(self):
        """Test masking URL with hostname (not IP)."""
        url = "http://proxy.example.com:8080"
        result = mask_proxy_url(url)
        
        # Hostname should remain unchanged
        assert 'proxy.example.com:8080' in result
    
    def test_mask_https_url(self):
        """Test masking HTTPS URL."""
        url = "https://user:pass@192.168.1.100:8080"
        result = mask_proxy_url(url)
        
        assert 'https://' in result
        assert '***:***@' in result


class TestProxyInfo:
    """Test cases for ProxyInfo class."""
    
    def test_init_default(self):
        """Test default initialization."""
        proxy = ProxyInfo()
        
        assert proxy.http_url is None
        assert proxy.https_url is None
        assert proxy.name == "Unnamed"
        assert proxy.failures == 0
        assert proxy.total_requests == 0
        assert proxy.successful_requests == 0
        assert proxy.is_available is True
    
    def test_init_with_values(self):
        """Test initialization with values."""
        proxy = ProxyInfo(
            http_url="http://proxy:8080",
            https_url="https://proxy:8080",
            name="MyProxy"
        )
        
        assert proxy.http_url == "http://proxy:8080"
        assert proxy.https_url == "https://proxy:8080"
        assert proxy.name == "MyProxy"
    
    def test_get_proxies_dict(self):
        """Test get_proxies_dict returns correct format."""
        proxy = ProxyInfo(
            http_url="http://proxy:8080",
            https_url="https://proxy:8080"
        )
        
        result = proxy.get_proxies_dict()
        
        assert result['http'] == "http://proxy:8080"
        assert result['https'] == "https://proxy:8080"
    
    def test_get_proxies_dict_http_only(self):
        """Test get_proxies_dict with only HTTP."""
        proxy = ProxyInfo(http_url="http://proxy:8080")
        
        result = proxy.get_proxies_dict()
        
        assert result.get('http') == "http://proxy:8080"
        assert 'https' not in result
    
    def test_mark_success(self):
        """Test mark_success updates counters."""
        proxy = ProxyInfo()
        proxy.failures = 2
        
        proxy.mark_success()
        
        assert proxy.successful_requests == 1
        assert proxy.total_requests == 1
        assert proxy.failures == 0
        assert proxy.is_available is True
        assert proxy.cooldown_until is None
        assert proxy.last_success is not None
    
    def test_mark_failure(self):
        """Test mark_failure updates counters and sets cooldown."""
        proxy = ProxyInfo()
        
        proxy.mark_failure(cooldown_seconds=300)
        
        assert proxy.failures == 1
        assert proxy.total_requests == 1
        assert proxy.is_available is False
        assert proxy.cooldown_until is not None
        assert proxy.last_failure is not None
    
    def test_is_in_cooldown_true(self):
        """Test is_in_cooldown returns True when in cooldown."""
        proxy = ProxyInfo()
        proxy.cooldown_until = datetime.now() + timedelta(minutes=5)
        
        assert proxy.is_in_cooldown() is True
    
    def test_is_in_cooldown_false(self):
        """Test is_in_cooldown returns False when not in cooldown."""
        proxy = ProxyInfo()
        
        assert proxy.is_in_cooldown() is False
    
    def test_is_in_cooldown_expired(self):
        """Test is_in_cooldown returns False when cooldown expired."""
        proxy = ProxyInfo()
        proxy.cooldown_until = datetime.now() - timedelta(minutes=5)
        
        assert proxy.is_in_cooldown() is False
    
    def test_get_success_rate_no_requests(self):
        """Test get_success_rate with no requests."""
        proxy = ProxyInfo()
        
        assert proxy.get_success_rate() == 0.0
    
    def test_get_success_rate_with_requests(self):
        """Test get_success_rate with requests."""
        proxy = ProxyInfo()
        proxy.total_requests = 10
        proxy.successful_requests = 8
        
        assert proxy.get_success_rate() == 0.8


class TestProxyPool:
    """Test cases for ProxyPool class."""
    
    def test_init_default(self, temp_dir):
        """Test default initialization."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        assert len(pool.proxies) == 0
        assert pool.current_index == 0
        assert pool.no_proxy_mode is False
    
    def test_add_proxy(self, temp_dir):
        """Test adding a proxy to the pool."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        pool.add_proxy(http_url="http://proxy:8080", name="test-proxy")
        
        assert len(pool.proxies) == 1
        assert pool.proxies[0].name == "test-proxy"
    
    def test_add_proxy_no_urls(self, temp_dir):
        """Test adding proxy without URLs is skipped."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        pool.add_proxy()  # No URLs
        
        assert len(pool.proxies) == 0
    
    def test_add_proxy_auto_name(self, temp_dir):
        """Test proxy gets auto-generated name."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        pool.add_proxy(http_url="http://proxy1:8080")
        pool.add_proxy(http_url="http://proxy2:8080")
        
        assert pool.proxies[0].name == "Proxy-1"
        assert pool.proxies[1].name == "Proxy-2"
    
    def test_add_proxies_from_list(self, temp_dir):
        """Test adding multiple proxies from list."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        proxy_list = [
            {'http': 'http://proxy1:8080', 'https': 'https://proxy1:8080', 'name': 'Proxy-A'},
            {'http': 'http://proxy2:8080', 'name': 'Proxy-B'}
        ]
        
        pool.add_proxies_from_list(proxy_list)
        
        assert len(pool.proxies) == 2
        assert pool.proxies[0].name == "Proxy-A"
        assert pool.proxies[1].name == "Proxy-B"
    
    def test_enable_disable_no_proxy_mode(self, temp_dir):
        """Test enabling and disabling no-proxy mode."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        pool.enable_no_proxy_mode()
        assert pool.no_proxy_mode is True
        
        pool.disable_no_proxy_mode()
        assert pool.no_proxy_mode is False
    
    def test_get_current_proxy_no_proxy_mode(self, temp_dir):
        """Test get_current_proxy returns None in no-proxy mode."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://proxy:8080")
        pool.enable_no_proxy_mode()
        
        result = pool.get_current_proxy()
        
        assert result is None
    
    def test_get_current_proxy_no_proxies(self, temp_dir):
        """Test get_current_proxy returns None when no proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        result = pool.get_current_proxy()
        
        assert result is None
    
    def test_get_current_proxy(self, temp_dir):
        """Test get_current_proxy returns current proxy."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://proxy1:8080", name="proxy-1")
        
        result = pool.get_current_proxy()
        
        assert result == {'http': 'http://proxy1:8080'}
    
    def test_get_next_proxy_round_robin(self, temp_dir):
        """Test get_next_proxy rotates through proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://proxy1:8080", name="proxy-1")
        pool.add_proxy(http_url="http://proxy2:8080", name="proxy-2")
        pool.add_proxy(http_url="http://proxy3:8080", name="proxy-3")
        
        # Get next proxies in round-robin fashion
        result1 = pool.get_next_proxy()
        result2 = pool.get_next_proxy()
        result3 = pool.get_next_proxy()
        result4 = pool.get_next_proxy()  # Should wrap around
        
        # First call starts at index 0, rotates to 1
        assert result1 == {'http': 'http://proxy2:8080'}
        assert result2 == {'http': 'http://proxy3:8080'}
        assert result3 == {'http': 'http://proxy1:8080'}
        assert result4 == {'http': 'http://proxy2:8080'}
    
    def test_get_next_proxy_no_proxies(self, temp_dir):
        """Test get_next_proxy returns None when no proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        result = pool.get_next_proxy()
        
        assert result is None
    
    def test_get_current_proxy_name(self, temp_dir):
        """Test get_current_proxy_name returns correct name."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://proxy:8080", name="my-proxy")
        
        result = pool.get_current_proxy_name()
        
        assert result == "my-proxy"
    
    def test_get_current_proxy_name_no_proxy_mode(self, temp_dir):
        """Test get_current_proxy_name in no-proxy mode."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.enable_no_proxy_mode()
        
        result = pool.get_current_proxy_name()
        
        assert result == "No-Proxy (Direct)"
    
    def test_get_current_proxy_name_no_proxies(self, temp_dir):
        """Test get_current_proxy_name when no proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        result = pool.get_current_proxy_name()
        
        assert result == "None"
    
    def test_mark_success(self, temp_dir):
        """Test mark_success updates current proxy."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://proxy:8080", name="proxy-1")
        
        pool.mark_success()
        
        assert pool.proxies[0].successful_requests == 1
    
    def test_mark_success_no_proxy_mode(self, temp_dir):
        """Test mark_success does nothing in no-proxy mode."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.enable_no_proxy_mode()
        
        pool.mark_success()  # Should not raise
    
    def test_mark_failure_and_switch(self, temp_dir):
        """Test mark_failure_and_switch switches to next proxy."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file, max_failures_before_cooldown=2)
        pool.add_proxy(http_url="http://proxy1:8080", name="proxy-1")
        pool.add_proxy(http_url="http://proxy2:8080", name="proxy-2")
        
        # First failure (not yet at max)
        result = pool.mark_failure_and_switch()
        
        assert result is True
        assert pool.current_index == 1  # Switched to proxy-2
    
    def test_mark_failure_and_switch_cooldown(self, temp_dir):
        """Test mark_failure_and_switch puts proxy in cooldown after max failures."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file, max_failures_before_cooldown=2)
        pool.add_proxy(http_url="http://proxy1:8080", name="proxy-1")
        pool.add_proxy(http_url="http://proxy2:8080", name="proxy-2")
        
        # Set failure count to max
        pool.proxies[0].failures = 2
        
        result = pool.mark_failure_and_switch()
        
        assert result is True
        assert pool.proxies[0].is_available is False  # In cooldown
        assert pool.current_index == 1
    
    def test_mark_failure_and_switch_no_available(self, temp_dir):
        """Test mark_failure_and_switch returns False when no proxy available."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans_single.csv')
        pool = ProxyPool(ban_log_file=ban_log_file, max_failures_before_cooldown=1)
        pool.add_proxy(http_url="http://single-proxy:8080", name="single-test-proxy")
        
        # Ensure proxy was added
        assert len(pool.proxies) == 1, "Proxy should be added"
        
        # Put single proxy in cooldown
        pool.proxies[0].failures = 1
        result = pool.mark_failure_and_switch()
        
        assert result is False
    
    def test_get_statistics_no_proxies(self, temp_dir):
        """Test get_statistics with no proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        stats = pool.get_statistics()
        
        assert stats['total_proxies'] == 0
        assert stats['available_proxies'] == 0
        assert stats['in_cooldown'] == 0
    
    def test_get_statistics_with_proxies(self, temp_dir):
        """Test get_statistics with proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans_stats.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://stats-proxy1:8080", name="stats-proxy-1")
        pool.add_proxy(http_url="http://stats-proxy2:8080", name="stats-proxy-2")
        
        # Ensure both proxies were added
        assert len(pool.proxies) == 2, "Both proxies should be added"
        
        # Put one in cooldown
        pool.proxies[1].is_available = False
        pool.proxies[1].cooldown_until = datetime.now() + timedelta(hours=1)
        
        stats = pool.get_statistics()
        
        assert stats['total_proxies'] == 2
        assert stats['available_proxies'] == 1
        assert stats['in_cooldown'] == 1
        assert len(stats['proxies']) == 2
    
    def test_log_statistics(self, temp_dir):
        """Test log_statistics runs without error."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://proxy:8080", name="proxy-1")
        
        # Should not raise
        pool.log_statistics()
    
    def test_get_ban_summary(self, temp_dir):
        """Test get_ban_summary delegates to ban manager."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans_summary.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        summary = pool.get_ban_summary()
        
        # The summary might show bans from other tests or no bans
        assert isinstance(summary, str)
    
    def test_check_cooldowns_recovery(self, temp_dir):
        """Test that proxies recover from cooldown."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans_recovery.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://recovery-proxy:8080", name="recovery-test-proxy")
        
        # Ensure proxy was added
        assert len(pool.proxies) == 1, "Proxy should be added"
        
        # Put in cooldown that's already expired
        pool.proxies[0].is_available = False
        pool.proxies[0].cooldown_until = datetime.now() - timedelta(seconds=1)
        
        # Getting proxy should trigger cooldown check
        result = pool.get_current_proxy()
        
        assert result is not None
        assert pool.proxies[0].is_available is True
    
    def test_all_proxies_in_cooldown(self, temp_dir):
        """Test get_current_proxy when all proxies in cooldown."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        pool.add_proxy(http_url="http://proxy1:8080", name="proxy-1")
        pool.add_proxy(http_url="http://proxy2:8080", name="proxy-2")
        
        # Put all in cooldown
        for proxy in pool.proxies:
            proxy.is_available = False
            proxy.cooldown_until = datetime.now() + timedelta(hours=1)
        
        result = pool.get_current_proxy()
        
        assert result is None


class TestCreateProxyPoolFromConfig:
    """Test cases for create_proxy_pool_from_config function."""
    
    def test_create_pool_from_config(self, temp_dir):
        """Test creating pool from configuration."""
        proxy_list = [
            {'http': 'http://proxy1:8080', 'name': 'Proxy-1'},
            {'http': 'http://proxy2:8080', 'name': 'Proxy-2'}
        ]
        
        # Mock the ban manager to avoid file creation in random location
        with patch('utils.proxy_pool.get_ban_manager') as mock_ban_manager:
            mock_ban_manager.return_value = MagicMock()
            mock_ban_manager.return_value.is_proxy_banned.return_value = False
            
            pool = create_proxy_pool_from_config(proxy_list, cooldown_seconds=600, max_failures=5)
        
        assert len(pool.proxies) == 2
        assert pool.cooldown_seconds == 600
        assert pool.max_failures_before_cooldown == 5


class TestProxyPoolWithBannedProxy:
    """Test cases for ProxyPool interaction with banned proxies."""
    
    def test_add_banned_proxy_skipped(self, temp_dir):
        """Test that banned proxies are skipped when adding."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        pool = ProxyPool(ban_log_file=ban_log_file)
        
        # First add and ban a proxy
        pool.add_proxy(http_url="http://proxy:8080", name="banned-proxy")
        pool.ban_manager.add_ban("banned-proxy")
        
        # Create new pool and try to add the banned proxy
        pool2 = ProxyPool(ban_log_file=ban_log_file)
        pool2.add_proxy(http_url="http://proxy:8080", name="banned-proxy")
        
        # Banned proxy should not be added
        assert len(pool2.proxies) == 0

