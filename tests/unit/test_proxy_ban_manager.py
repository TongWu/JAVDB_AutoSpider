"""
Unit tests for utils/proxy_ban_manager.py

Proxy bans are now session-scoped (in-memory only).
A ban is permanent for the lifetime of the process.
"""
import os
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
        record = ProxyBanRecord("test-proxy", ban_time)
        
        assert record.proxy_name == "test-proxy"
        assert record.ban_time == ban_time
        assert record.proxy_url is None
    
    def test_init_with_proxy_url(self):
        """Test initialization with proxy URL."""
        ban_time = datetime(2024, 1, 1, 10, 0, 0)
        record = ProxyBanRecord("test-proxy", ban_time,
                                proxy_url="http://user:pass@192.168.1.1:8080")
        
        assert record.proxy_url == "http://user:pass@192.168.1.1:8080"
    
    def test_to_dict(self):
        """Test to_dict conversion."""
        ban_time = datetime(2024, 1, 1, 10, 30, 45)
        record = ProxyBanRecord("test-proxy", ban_time,
                                proxy_url="http://proxy:8080")
        
        result = record.to_dict()
        
        assert result['proxy_name'] == "test-proxy"
        assert result['ban_time'] == "2024-01-01 10:30:45"
        assert 'proxy_url' not in result
    
    def test_to_dict_with_ip(self):
        """Test to_dict_with_ip conversion."""
        ban_time = datetime(2024, 1, 1, 10, 30, 45)
        record = ProxyBanRecord("test-proxy", ban_time,
                                proxy_url="http://proxy:8080")
        
        result = record.to_dict_with_ip()
        
        assert result['proxy_name'] == "test-proxy"
        assert result['proxy_url'] == "http://proxy:8080"
    
    def test_to_dict_with_ip_no_url(self):
        """Test to_dict_with_ip when no URL is set."""
        ban_time = datetime(2024, 1, 1, 10, 30, 45)
        record = ProxyBanRecord("test-proxy", ban_time)
        
        result = record.to_dict_with_ip()
        
        assert result['proxy_url'] == 'N/A'


class TestProxyBanManager:
    """Test cases for ProxyBanManager class (session-scoped, in-memory)."""
    
    def test_init_no_bans(self):
        """Test initialization starts with no bans."""
        manager = ProxyBanManager()
        assert len(manager.banned_proxies) == 0
    
    def test_add_ban(self):
        """Test adding a new ban."""
        manager = ProxyBanManager()
        
        manager.add_ban("new-proxy", proxy_url="http://192.168.1.1:8080")
        
        assert "new-proxy" in manager.banned_proxies
        record = manager.banned_proxies["new-proxy"]
        assert record.proxy_url == "http://192.168.1.1:8080"
    
    def test_add_ban_already_banned(self):
        """Test adding a ban for already banned proxy."""
        manager = ProxyBanManager()
        
        manager.add_ban("proxy-1")
        original_ban_time = manager.banned_proxies["proxy-1"].ban_time
        
        manager.add_ban("proxy-1")
        
        assert manager.banned_proxies["proxy-1"].ban_time == original_ban_time
    
    def test_is_proxy_banned_true(self):
        """Test is_proxy_banned returns True for banned proxy."""
        manager = ProxyBanManager()
        
        manager.add_ban("banned-proxy")
        
        assert manager.is_proxy_banned("banned-proxy") is True
    
    def test_is_proxy_banned_false(self):
        """Test is_proxy_banned returns False for non-banned proxy."""
        manager = ProxyBanManager()
        
        assert manager.is_proxy_banned("unknown-proxy") is False
    
    def test_is_proxy_banned_session_permanent(self):
        """Ban is permanent for the session — no expiry."""
        manager = ProxyBanManager()
        manager.add_ban("proxy-1")

        assert manager.is_proxy_banned("proxy-1") is True
    
    def test_get_banned_proxies(self):
        """Test get_banned_proxies returns list of banned proxies."""
        manager = ProxyBanManager()
        
        manager.add_ban("proxy-1")
        manager.add_ban("proxy-2")
        
        banned = manager.get_banned_proxies()
        
        assert len(banned) == 2
        names = [r.proxy_name for r in banned]
        assert "proxy-1" in names
        assert "proxy-2" in names
    
    def test_get_ban_summary_no_bans(self):
        """Test get_ban_summary with no banned proxies."""
        manager = ProxyBanManager()
        
        summary = manager.get_ban_summary()
        
        assert "No proxies currently banned" in summary
    
    def test_get_ban_summary_with_bans(self):
        """Test get_ban_summary with banned proxies."""
        manager = ProxyBanManager()
        
        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")
        
        summary = manager.get_ban_summary()
        
        assert "Currently banned proxies: 1" in summary
        assert "proxy-1" in summary
        assert "Banned at:" in summary
    
    def test_get_ban_summary_with_ip(self):
        """Test get_ban_summary with IP included."""
        manager = ProxyBanManager()
        
        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")
        
        summary = manager.get_ban_summary(include_ip=True)
        
        assert "http://192.168.1.1:8080" in summary
    
    def test_get_ban_summary_without_ip(self):
        """Test get_ban_summary without IP (default)."""
        manager = ProxyBanManager()
        
        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")
        
        summary = manager.get_ban_summary(include_ip=False)
        
        assert "192.168.1.1" not in summary

    def test_session_scoped_no_persistence(self):
        """Test that bans do not persist: a new manager starts clean."""
        manager1 = ProxyBanManager()
        manager1.add_ban("proxy-1")
        assert manager1.is_proxy_banned("proxy-1") is True

        manager2 = ProxyBanManager()
        assert manager2.is_proxy_banned("proxy-1") is False


class TestGetBanManager:
    """Test cases for get_ban_manager function."""
    
    def test_get_ban_manager_creates_singleton(self):
        """Test that get_ban_manager creates a singleton instance."""
        import utils.proxy_ban_manager as pbm
        pbm._global_ban_manager = None
        
        manager1 = get_ban_manager()
        manager2 = get_ban_manager()
        
        assert manager1 is manager2
        
        pbm._global_ban_manager = None


class TestRemoteBanHook:
    """P1-A — cross-runner ban dispatcher (``set_remote_ban_hook``).

    These verify (a) the hook fires once per *newly* recorded ban,
    (b) repeats are deduped (no over-amplification of report_async),
    (c) hook exceptions are swallowed, and (d) ``set_remote_ban_hook(None)``
    cleanly disables the integration so the local manager keeps the
    pre-coordinator behaviour.
    """

    def setup_method(self):
        # Defensive: every test starts with the hook cleared so cross-test
        # leaks (a previous test forgetting to unregister) can't mask bugs.
        from packages.python.javdb_platform.proxy_ban_manager import (
            set_remote_ban_hook,
            set_remote_unban_hook,
        )
        set_remote_ban_hook(None)
        set_remote_unban_hook(None)

    def teardown_method(self):
        from packages.python.javdb_platform.proxy_ban_manager import (
            set_remote_ban_hook,
            set_remote_unban_hook,
        )
        set_remote_ban_hook(None)
        set_remote_unban_hook(None)

    def test_add_ban_fires_remote_hook_exactly_once(self):
        from packages.python.javdb_platform.proxy_ban_manager import (
            set_remote_ban_hook,
        )
        captured: list = []
        set_remote_ban_hook(lambda name: captured.append(name))

        manager = ProxyBanManager()
        manager.add_ban("proxy-A")
        assert captured == ["proxy-A"]

    def test_add_ban_does_not_fire_for_already_banned_proxy(self):
        from packages.python.javdb_platform.proxy_ban_manager import (
            set_remote_ban_hook,
        )
        captured: list = []
        set_remote_ban_hook(lambda name: captured.append(name))

        manager = ProxyBanManager()
        manager.add_ban("proxy-A")
        manager.add_ban("proxy-A")
        manager.add_ban("proxy-A")
        # Only the first add fires the hook; subsequent calls are dedup'd.
        assert captured == ["proxy-A"]

    def test_add_ban_swallows_hook_exceptions(self):
        from packages.python.javdb_platform.proxy_ban_manager import (
            set_remote_ban_hook,
        )

        def boom(name):
            raise RuntimeError("simulated coordinator outage")

        set_remote_ban_hook(boom)
        manager = ProxyBanManager()
        # Must NOT raise — the local ban must commit even when the hook fails.
        manager.add_ban("proxy-A")
        assert manager.is_proxy_banned("proxy-A") is True

    def test_clearing_remote_hook_restores_local_only_behaviour(self):
        from packages.python.javdb_platform.proxy_ban_manager import (
            set_remote_ban_hook,
        )
        captured: list = []
        set_remote_ban_hook(lambda name: captured.append(name))
        set_remote_ban_hook(None)  # explicit clear

        manager = ProxyBanManager()
        manager.add_ban("proxy-A")
        assert captured == []
        assert manager.is_proxy_banned("proxy-A") is True

    def test_remote_hook_skips_empty_proxy_name(self):
        """Defensive: a falsy name must not reach the hook (which would 4xx)."""
        from packages.python.javdb_platform.proxy_ban_manager import (
            _dispatch_remote_ban,
            set_remote_ban_hook,
        )
        captured: list = []
        set_remote_ban_hook(lambda name: captured.append(name))
        _dispatch_remote_ban("")
        _dispatch_remote_ban(None)  # type: ignore[arg-type]
        assert captured == []
