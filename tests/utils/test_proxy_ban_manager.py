"""
Unit tests for utils/proxy_ban_manager.py
"""
import pytest
import os
import csv
import tempfile
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from utils.proxy_ban_manager import (
    ProxyBanRecord,
    ProxyBanManager,
    get_ban_manager
)


class TestProxyBanRecord:
    """Tests for ProxyBanRecord class"""
    
    @pytest.fixture
    def ban_record(self):
        """Create a sample ban record"""
        ban_time = datetime.now()
        unban_time = ban_time + timedelta(days=7)
        return ProxyBanRecord(
            proxy_name="TestProxy",
            ban_time=ban_time,
            unban_time=unban_time,
            proxy_url="http://123.45.67.89:8080"
        )
    
    def test_is_still_banned(self, ban_record):
        """Test checking if proxy is still banned"""
        assert ban_record.is_still_banned() is True
    
    def test_is_not_banned_after_unban_time(self):
        """Test that proxy is not banned after unban time"""
        ban_time = datetime.now() - timedelta(days=10)
        unban_time = ban_time + timedelta(days=7)
        record = ProxyBanRecord("TestProxy", ban_time, unban_time)
        
        assert record.is_still_banned() is False
    
    def test_time_until_unban(self, ban_record):
        """Test calculating time until unban"""
        time_left = ban_record.time_until_unban()
        
        assert time_left.days >= 0
        assert time_left.days <= 7
    
    def test_days_until_unban(self, ban_record):
        """Test calculating days until unban"""
        days_left = ban_record.days_until_unban()
        
        assert days_left >= 0
        assert days_left <= 7
    
    def test_to_dict(self, ban_record):
        """Test converting to dictionary"""
        data = ban_record.to_dict()
        
        assert 'proxy_name' in data
        assert 'ban_time' in data
        assert 'unban_time' in data
        assert data['proxy_name'] == "TestProxy"
    
    def test_to_dict_with_ip(self, ban_record):
        """Test converting to dictionary with IP info"""
        data = ban_record.to_dict_with_ip()
        
        assert 'proxy_url' in data
        assert data['proxy_url'] == "http://123.45.67.89:8080"


class TestProxyBanManager:
    """Tests for ProxyBanManager class"""
    
    @pytest.fixture
    def temp_ban_log(self):
        """Create temporary ban log file"""
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)
    
    @pytest.fixture
    def ban_manager(self, temp_ban_log):
        """Create a ProxyBanManager instance"""
        return ProxyBanManager(ban_log_file=temp_ban_log)
    
    def test_add_ban(self, ban_manager):
        """Test adding a ban record"""
        ban_manager.add_ban("TestProxy", "http://123.45.67.89:8080")
        
        assert ban_manager.is_proxy_banned("TestProxy") is True
    
    def test_is_proxy_not_banned(self, ban_manager):
        """Test checking proxy that is not banned"""
        assert ban_manager.is_proxy_banned("NonExistentProxy") is False
    
    def test_get_banned_proxies(self, ban_manager):
        """Test getting list of banned proxies"""
        ban_manager.add_ban("Proxy1", "http://proxy1.com:8080")
        ban_manager.add_ban("Proxy2", "http://proxy2.com:8080")
        
        banned = ban_manager.get_banned_proxies()
        
        assert len(banned) == 2
    
    def test_ban_persistence(self, temp_ban_log):
        """Test that bans persist across manager instances"""
        # Create first manager and add ban
        manager1 = ProxyBanManager(ban_log_file=temp_ban_log)
        manager1.add_ban("TestProxy", "http://123.45.67.89:8080")
        
        # Create second manager and check ban exists
        manager2 = ProxyBanManager(ban_log_file=temp_ban_log)
        assert manager2.is_proxy_banned("TestProxy") is True
    
    def test_cleanup_expired_bans(self, temp_ban_log):
        """Test that expired bans are cleaned up"""
        manager = ProxyBanManager(ban_log_file=temp_ban_log)
        
        # Add a ban that expired 1 day ago
        ban_time = datetime.now() - timedelta(days=8)
        unban_time = ban_time + timedelta(days=7)
        record = ProxyBanRecord("ExpiredProxy", ban_time, unban_time)
        manager.banned_proxies["ExpiredProxy"] = record
        manager._save_ban_records()
        
        # Reload manager - expired ban should be cleaned up
        manager2 = ProxyBanManager(ban_log_file=temp_ban_log)
        assert manager2.is_proxy_banned("ExpiredProxy") is False
    
    def test_get_ban_summary_no_bans(self, ban_manager):
        """Test getting ban summary when no bans exist"""
        summary = ban_manager.get_ban_summary()
        
        assert "No proxies currently banned" in summary
    
    def test_get_ban_summary_with_bans(self, ban_manager):
        """Test getting ban summary with banned proxies"""
        ban_manager.add_ban("TestProxy", "http://123.45.67.89:8080")
        
        summary = ban_manager.get_ban_summary()
        
        assert "TestProxy" in summary
        assert "Currently banned proxies: 1" in summary
    
    def test_get_ban_summary_with_ip(self, ban_manager):
        """Test getting ban summary with IP information"""
        ban_manager.add_ban("TestProxy", "http://123.45.67.89:8080")
        
        summary = ban_manager.get_ban_summary(include_ip=True)
        
        assert "TestProxy" in summary
        assert "123.45.67.89" in summary
    
    def test_get_cooldown_seconds(self, ban_manager):
        """Test getting cooldown duration in seconds"""
        cooldown = ban_manager.get_cooldown_seconds()
        
        # Should be 8 days in seconds
        assert cooldown == 8 * 24 * 3600
    
    def test_add_ban_already_banned(self, ban_manager):
        """Test adding ban for already banned proxy"""
        ban_manager.add_ban("TestProxy", "http://123.45.67.89:8080")
        
        # Try to add again - should not update
        ban_manager.add_ban("TestProxy", "http://123.45.67.89:8080")
        
        # Should still be banned with original time
        assert ban_manager.is_proxy_banned("TestProxy") is True
    
    def test_expired_ban_checked_on_query(self, temp_ban_log):
        """Test that expired bans are removed when checked"""
        manager = ProxyBanManager(ban_log_file=temp_ban_log)
        
        # Add a ban that will expire
        ban_time = datetime.now() - timedelta(days=8)
        unban_time = ban_time + timedelta(days=7)
        record = ProxyBanRecord("ExpiredProxy", ban_time, unban_time)
        manager.banned_proxies["ExpiredProxy"] = record
        
        # Check if banned - should return False and clean up
        is_banned = manager.is_proxy_banned("ExpiredProxy")
        
        assert is_banned is False
        assert "ExpiredProxy" not in manager.banned_proxies


class TestGetBanManager:
    """Tests for get_ban_manager global instance function"""
    
    def test_get_ban_manager_singleton(self, temp_ban_log):
        """Test that get_ban_manager returns singleton instance"""
        # Reset global instance
        import utils.proxy_ban_manager
        utils.proxy_ban_manager._global_ban_manager = None
        
        manager1 = get_ban_manager(temp_ban_log)
        manager2 = get_ban_manager(temp_ban_log)
        
        # Should be the same instance
        assert manager1 is manager2
