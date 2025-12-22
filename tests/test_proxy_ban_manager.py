"""
Unit tests for utils/proxy_ban_manager.py
"""
import os
import sys
import csv
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.proxy_ban_manager import (
    ProxyBanRecord,
    ProxyBanManager,
    get_ban_manager,
    _global_ban_manager
)


class TestProxyBanRecord:
    """Test cases for ProxyBanRecord class."""
    
    def test_init_basic(self):
        """Test basic initialization."""
        ban_time = datetime(2024, 1, 1, 10, 0, 0)
        unban_time = datetime(2024, 1, 8, 10, 0, 0)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time)
        
        assert record.proxy_name == "test-proxy"
        assert record.ban_time == ban_time
        assert record.unban_time == unban_time
        assert record.proxy_url is None
    
    def test_init_with_proxy_url(self):
        """Test initialization with proxy URL."""
        ban_time = datetime(2024, 1, 1, 10, 0, 0)
        unban_time = datetime(2024, 1, 8, 10, 0, 0)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time, 
                                proxy_url="http://user:pass@192.168.1.1:8080")
        
        assert record.proxy_url == "http://user:pass@192.168.1.1:8080"
    
    def test_is_still_banned_true(self):
        """Test is_still_banned returns True when still banned."""
        ban_time = datetime.now() - timedelta(days=1)
        unban_time = datetime.now() + timedelta(days=6)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time)
        
        assert record.is_still_banned() is True
    
    def test_is_still_banned_false(self):
        """Test is_still_banned returns False when ban expired."""
        ban_time = datetime.now() - timedelta(days=10)
        unban_time = datetime.now() - timedelta(days=3)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time)
        
        assert record.is_still_banned() is False
    
    def test_time_until_unban(self):
        """Test time_until_unban calculation."""
        ban_time = datetime.now()
        unban_time = datetime.now() + timedelta(days=7)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time)
        
        time_left = record.time_until_unban()
        # Should be approximately 7 days
        assert time_left.days >= 6
        assert time_left.days <= 7
    
    def test_days_until_unban_positive(self):
        """Test days_until_unban with positive days remaining."""
        ban_time = datetime.now()
        unban_time = datetime.now() + timedelta(days=5, hours=12)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time)
        
        days = record.days_until_unban()
        assert days == 5
    
    def test_days_until_unban_expired(self):
        """Test days_until_unban returns 0 when expired."""
        ban_time = datetime.now() - timedelta(days=10)
        unban_time = datetime.now() - timedelta(days=3)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time)
        
        days = record.days_until_unban()
        assert days == 0
    
    def test_to_dict(self):
        """Test to_dict conversion."""
        ban_time = datetime(2024, 1, 1, 10, 30, 45)
        unban_time = datetime(2024, 1, 8, 10, 30, 45)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time,
                                proxy_url="http://proxy:8080")
        
        result = record.to_dict()
        
        assert result['proxy_name'] == "test-proxy"
        assert result['ban_time'] == "2024-01-01 10:30:45"
        assert result['unban_time'] == "2024-01-08 10:30:45"
        # proxy_url should NOT be in to_dict (no IP for security)
        assert 'proxy_url' not in result
    
    def test_to_dict_with_ip(self):
        """Test to_dict_with_ip conversion."""
        ban_time = datetime(2024, 1, 1, 10, 30, 45)
        unban_time = datetime(2024, 1, 8, 10, 30, 45)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time,
                                proxy_url="http://proxy:8080")
        
        result = record.to_dict_with_ip()
        
        assert result['proxy_name'] == "test-proxy"
        assert result['proxy_url'] == "http://proxy:8080"
    
    def test_to_dict_with_ip_no_url(self):
        """Test to_dict_with_ip when no URL is set."""
        ban_time = datetime(2024, 1, 1, 10, 30, 45)
        unban_time = datetime(2024, 1, 8, 10, 30, 45)
        record = ProxyBanRecord("test-proxy", ban_time, unban_time)
        
        result = record.to_dict_with_ip()
        
        assert result['proxy_url'] == 'N/A'


class TestProxyBanManager:
    """Test cases for ProxyBanManager class."""
    
    def test_init_creates_directory(self, temp_dir):
        """Test that init creates the log directory if not exists."""
        ban_log_file = os.path.join(temp_dir, 'subdir', 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        assert os.path.exists(os.path.dirname(ban_log_file))
    
    def test_init_loads_no_existing_file(self, temp_dir):
        """Test initialization when no ban file exists."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        assert len(manager.banned_proxies) == 0
    
    def test_load_existing_ban_records(self, temp_dir):
        """Test loading existing ban records from CSV."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        
        # Create a ban file with future unban time
        future_unban = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S')
        past_ban = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with open(ban_log_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['proxy_name', 'ban_time', 'unban_time'])
            writer.writeheader()
            writer.writerow({
                'proxy_name': 'proxy-1',
                'ban_time': past_ban,
                'unban_time': future_unban
            })
        
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        assert len(manager.banned_proxies) == 1
        assert 'proxy-1' in manager.banned_proxies
    
    def test_load_and_cleanup_expired_bans(self, temp_dir):
        """Test that expired bans are cleaned up on load."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        
        # Create a ban file with expired unban time
        past_unban = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        past_ban = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%d %H:%M:%S')
        
        with open(ban_log_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['proxy_name', 'ban_time', 'unban_time'])
            writer.writeheader()
            writer.writerow({
                'proxy_name': 'expired-proxy',
                'ban_time': past_ban,
                'unban_time': past_unban
            })
        
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        # Expired ban should be cleaned up
        assert len(manager.banned_proxies) == 0
    
    def test_add_ban(self, temp_dir):
        """Test adding a new ban."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        manager.add_ban("new-proxy", proxy_url="http://192.168.1.1:8080")
        
        assert "new-proxy" in manager.banned_proxies
        record = manager.banned_proxies["new-proxy"]
        assert record.proxy_url == "http://192.168.1.1:8080"
        
        # Verify file was saved
        assert os.path.exists(ban_log_file)
    
    def test_add_ban_already_banned(self, temp_dir):
        """Test adding a ban for already banned proxy."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        manager.add_ban("proxy-1")
        original_ban_time = manager.banned_proxies["proxy-1"].ban_time
        
        # Try to add same proxy again
        manager.add_ban("proxy-1")
        
        # Ban time should not change
        assert manager.banned_proxies["proxy-1"].ban_time == original_ban_time
    
    def test_is_proxy_banned_true(self, temp_dir):
        """Test is_proxy_banned returns True for banned proxy."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        manager.add_ban("banned-proxy")
        
        assert manager.is_proxy_banned("banned-proxy") is True
    
    def test_is_proxy_banned_false(self, temp_dir):
        """Test is_proxy_banned returns False for non-banned proxy."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        assert manager.is_proxy_banned("unknown-proxy") is False
    
    def test_is_proxy_banned_expired(self, temp_dir):
        """Test is_proxy_banned cleans up expired ban and returns False."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        # Manually add an expired ban
        expired_record = ProxyBanRecord(
            "expired-proxy",
            datetime.now() - timedelta(days=10),
            datetime.now() - timedelta(days=3)
        )
        manager.banned_proxies["expired-proxy"] = expired_record
        
        result = manager.is_proxy_banned("expired-proxy")
        
        assert result is False
        assert "expired-proxy" not in manager.banned_proxies
    
    def test_get_banned_proxies(self, temp_dir):
        """Test get_banned_proxies returns list of banned proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        manager.add_ban("proxy-1")
        manager.add_ban("proxy-2")
        
        banned = manager.get_banned_proxies()
        
        assert len(banned) == 2
        names = [r.proxy_name for r in banned]
        assert "proxy-1" in names
        assert "proxy-2" in names
    
    def test_get_ban_summary_no_bans(self, temp_dir):
        """Test get_ban_summary with no banned proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        summary = manager.get_ban_summary()
        
        assert "No proxies currently banned" in summary
    
    def test_get_ban_summary_with_bans(self, temp_dir):
        """Test get_ban_summary with banned proxies."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")
        
        summary = manager.get_ban_summary()
        
        assert "Currently banned proxies: 1" in summary
        assert "proxy-1" in summary
        assert "Banned at:" in summary
        assert "Will unban:" in summary
    
    def test_get_ban_summary_with_ip(self, temp_dir):
        """Test get_ban_summary with IP included."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")
        
        summary = manager.get_ban_summary(include_ip=True)
        
        assert "http://192.168.1.1:8080" in summary
    
    def test_get_ban_summary_without_ip(self, temp_dir):
        """Test get_ban_summary without IP (default)."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")
        
        summary = manager.get_ban_summary(include_ip=False)
        
        # IP should not appear when include_ip=False
        assert "192.168.1.1" not in summary
    
    def test_get_cooldown_seconds(self, temp_dir):
        """Test get_cooldown_seconds returns correct value."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        cooldown = manager.get_cooldown_seconds()
        
        # 8 days in seconds
        expected = 8 * 24 * 3600
        assert cooldown == expected
    
    def test_ban_duration_days(self, temp_dir):
        """Test BAN_DURATION_DAYS constant."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        assert manager.BAN_DURATION_DAYS == 7
    
    def test_load_ban_records_error_handling(self, temp_dir):
        """Test error handling when loading corrupted ban file."""
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        
        # Create a corrupted file
        with open(ban_log_file, 'w', encoding='utf-8') as f:
            f.write("invalid csv content without proper headers\n")
            f.write("more garbage\n")
        
        # Should not raise exception
        manager = ProxyBanManager(ban_log_file=ban_log_file)
        
        # Should have empty banned_proxies due to error
        assert len(manager.banned_proxies) == 0


class TestGetBanManager:
    """Test cases for get_ban_manager function."""
    
    def test_get_ban_manager_creates_singleton(self, temp_dir):
        """Test that get_ban_manager creates a singleton instance."""
        # Reset global
        import utils.proxy_ban_manager as pbm
        pbm._global_ban_manager = None
        
        ban_log_file = os.path.join(temp_dir, 'proxy_bans.csv')
        manager1 = get_ban_manager(ban_log_file)
        manager2 = get_ban_manager(ban_log_file)
        
        # Should be the same instance
        assert manager1 is manager2
        
        # Reset for other tests
        pbm._global_ban_manager = None

