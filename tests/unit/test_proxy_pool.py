"""
Unit tests for utils/infra/proxy_pool.py

Proxy bans are now session-scoped (in-memory only).
"""
import os
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from utils.infra.proxy_pool import (
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
        
        # Credentials should be removed entirely (not shown in output)
        assert 'user' not in result
        assert 'pass' not in result
        assert '@' not in result  # No @ sign since credentials are removed
        
        # Protocol should be preserved
        assert result.startswith('http://')
        
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
        
        # Protocol should be preserved
        assert 'https://' in result
        # Credentials should be removed
        assert 'user' not in result
        assert 'pass' not in result
        assert '@' not in result
        # IP should be masked
        assert '.xxx.xxx.' in result


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
    
    def test_init_default(self):
        """Test default initialization."""
        pool = ProxyPool()
        
        assert len(pool.proxies) == 0
        assert pool.current_index == 0
        assert pool.no_proxy_mode is False
    
    def test_add_proxy(self):
        """Test adding a proxy to the pool."""
        pool = ProxyPool()
        
        pool.add_proxy(http_url="http://proxy:8080", name="test-proxy")
        
        assert len(pool.proxies) == 1
        assert pool.proxies[0].name == "test-proxy"
    
    def test_add_proxy_no_urls(self):
        """Test adding proxy without URLs is skipped."""
        pool = ProxyPool()
        
        pool.add_proxy()  # No URLs
        
        assert len(pool.proxies) == 0
    
    def test_add_proxy_auto_name(self):
        """Test proxy gets auto-generated name."""
        pool = ProxyPool()
        
        pool.add_proxy(http_url="http://proxy1:8080")
        pool.add_proxy(http_url="http://proxy2:8080")
        
        assert pool.proxies[0].name == "Proxy-1"
        assert pool.proxies[1].name == "Proxy-2"
    
    def test_add_proxies_from_list(self):
        """Test adding multiple proxies from list."""
        pool = ProxyPool()
        
        proxy_list = [
            {'http': 'http://proxy1:8080', 'https': 'https://proxy1:8080', 'name': 'Proxy-A'},
            {'http': 'http://proxy2:8080', 'name': 'Proxy-B'}
        ]
        
        pool.add_proxies_from_list(proxy_list)
        
        assert len(pool.proxies) == 2
        assert pool.proxies[0].name == "Proxy-A"
        assert pool.proxies[1].name == "Proxy-B"
    
    def test_enable_disable_no_proxy_mode(self):
        """Test enabling and disabling no-proxy mode."""
        pool = ProxyPool()
        
        pool.enable_no_proxy_mode()
        assert pool.no_proxy_mode is True
        
        pool.disable_no_proxy_mode()
        assert pool.no_proxy_mode is False
    
    def test_get_current_proxy_no_proxy_mode(self):
        """Test get_current_proxy returns None in no-proxy mode."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://proxy:8080")
        pool.enable_no_proxy_mode()
        
        result = pool.get_current_proxy()
        
        assert result is None
    
    def test_get_current_proxy_no_proxies(self):
        """Test get_current_proxy returns None when no proxies."""
        pool = ProxyPool()
        
        result = pool.get_current_proxy()
        
        assert result is None
    
    def test_get_current_proxy(self):
        """Test get_current_proxy returns current proxy."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://proxy1:8080", name="proxy-1")
        
        result = pool.get_current_proxy()
        
        assert result == {'http': 'http://proxy1:8080'}
    
    def test_get_next_proxy_round_robin(self):
        """Test get_next_proxy rotates through proxies."""
        pool = ProxyPool()
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
    
    def test_get_next_proxy_no_proxies(self):
        """Test get_next_proxy returns None when no proxies."""
        pool = ProxyPool()
        
        result = pool.get_next_proxy()
        
        assert result is None
    
    def test_get_current_proxy_name(self):
        """Test get_current_proxy_name returns correct name."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://proxy:8080", name="my-proxy")
        
        result = pool.get_current_proxy_name()
        
        assert result == "my-proxy"
    
    def test_get_current_proxy_name_no_proxy_mode(self):
        """Test get_current_proxy_name in no-proxy mode."""
        pool = ProxyPool()
        pool.enable_no_proxy_mode()
        
        result = pool.get_current_proxy_name()
        
        assert result == "No-Proxy (Direct)"
    
    def test_get_current_proxy_name_no_proxies(self):
        """Test get_current_proxy_name when no proxies."""
        pool = ProxyPool()
        
        result = pool.get_current_proxy_name()
        
        assert result == "None"
    
    def test_mark_success(self):
        """Test mark_success updates current proxy."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://proxy:8080", name="proxy-1")
        
        pool.mark_success()
        
        assert pool.proxies[0].successful_requests == 1
    
    def test_mark_success_no_proxy_mode(self):
        """Test mark_success does nothing in no-proxy mode."""
        pool = ProxyPool()
        pool.enable_no_proxy_mode()
        
        pool.mark_success()  # Should not raise
    
    def test_mark_failure_and_switch(self):
        """Test mark_failure_and_switch switches to next proxy."""
        pool = ProxyPool(max_failures_before_cooldown=2)
        pool.add_proxy(http_url="http://proxy1:8080", name="proxy-1")
        pool.add_proxy(http_url="http://proxy2:8080", name="proxy-2")
        
        # First failure (not yet at max)
        result = pool.mark_failure_and_switch()
        
        assert result is True
        assert pool.current_index == 1  # Switched to proxy-2
    
    def test_mark_failure_and_switch_cooldown(self):
        """Test mark_failure_and_switch puts proxy in cooldown after max failures."""
        pool = ProxyPool(max_failures_before_cooldown=2)
        pool.add_proxy(http_url="http://proxy1:8080", name="proxy-1")
        pool.add_proxy(http_url="http://proxy2:8080", name="proxy-2")
        
        # Set failure count to max
        pool.proxies[0].failures = 2
        
        result = pool.mark_failure_and_switch()
        
        assert result is True
        assert pool.proxies[0].is_available is False  # In cooldown
        assert pool.current_index == 1
    
    def test_mark_failure_and_switch_no_available(self):
        """Test mark_failure_and_switch returns False when no proxy available."""
        pool = ProxyPool(max_failures_before_cooldown=1)
        pool.add_proxy(http_url="http://single-proxy:8080", name="single-test-proxy")
        
        # Ensure proxy was added
        assert len(pool.proxies) == 1, "Proxy should be added"
        
        # Put single proxy in cooldown
        pool.proxies[0].failures = 1
        result = pool.mark_failure_and_switch()
        
        assert result is False
    
    def test_get_statistics_no_proxies(self):
        """Test get_statistics with no proxies."""
        pool = ProxyPool()
        
        stats = pool.get_statistics()
        
        assert stats['total_proxies'] == 0
        assert stats['available_proxies'] == 0
        assert stats['in_cooldown'] == 0
    
    def test_get_statistics_with_proxies(self):
        """Test get_statistics with proxies."""
        pool = ProxyPool()
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
    
    def test_log_statistics(self):
        """Test log_statistics runs without error."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://proxy:8080", name="proxy-1")
        
        # Should not raise
        pool.log_statistics()
    
    def test_get_ban_summary(self):
        """Test get_ban_summary delegates to ban manager."""
        pool = ProxyPool()
        
        summary = pool.get_ban_summary()
        
        assert isinstance(summary, str)
    
    def test_check_cooldowns_recovery(self):
        """Test that proxies recover from cooldown."""
        pool = ProxyPool()
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
    
    def test_all_proxies_in_cooldown(self):
        """Test get_current_proxy when all proxies in cooldown."""
        pool = ProxyPool()
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
    
    def test_create_pool_from_config(self):
        """Test creating pool from configuration."""
        proxy_list = [
            {'http': 'http://proxy1:8080', 'name': 'Proxy-1'},
            {'http': 'http://proxy2:8080', 'name': 'Proxy-2'}
        ]
        
        # Mock the ban manager to avoid shared state
        with patch('utils.infra.proxy_pool.get_ban_manager') as mock_ban_manager:
            mock_ban_manager.return_value = MagicMock()
            mock_ban_manager.return_value.is_proxy_banned.return_value = False
            
            pool = create_proxy_pool_from_config(proxy_list, max_failures=5)
        
        assert len(pool.proxies) == 2
        assert pool.max_failures_before_cooldown == 5


class TestProxyPoolWithBannedProxy:
    """Test cases for ProxyPool interaction with banned proxies (session-scoped)."""
    
    def test_add_banned_proxy_skipped(self):
        """Test that banned proxies are skipped when adding."""
        pool = ProxyPool()
        
        # Add and ban a proxy
        pool.add_proxy(http_url="http://proxy:8080", name="banned-proxy")
        pool.ban_manager.add_ban("banned-proxy")
        
        # Create new pool sharing the same singleton ban manager
        pool2 = ProxyPool()
        pool2.add_proxy(http_url="http://proxy:8080", name="banned-proxy")
        
        # Banned proxy should not be added (same session ban manager)
        assert len(pool2.proxies) == 0


class TestProxyPoolBanProxy:
    """Test cases for ProxyPool.ban_proxy() method."""

    def test_ban_proxy_immediately_bans_and_switches(self):
        """ban_proxy should permanently ban target and switch to next."""
        pool = ProxyPool(max_failures_before_cooldown=5)
        pool.add_proxy(http_url="http://ban-imm1:8080", name="ban-imm-1")
        pool.add_proxy(http_url="http://ban-imm2:8080", name="ban-imm-2")

        result = pool.ban_proxy("ban-imm-1")

        assert result is True
        assert pool.proxies[0].banned is True
        assert pool.proxies[0].is_available is False
        assert pool.current_index == 1

    def test_ban_proxy_records_in_ban_manager(self):
        """ban_proxy should register the ban in the ban manager."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://ban-rec1:8080", name="ban-rec-1")
        pool.add_proxy(http_url="http://ban-rec2:8080", name="ban-rec-2")

        assert not pool.ban_manager.is_proxy_banned("ban-rec-1")
        pool.ban_proxy("ban-rec-1")
        assert pool.ban_manager.is_proxy_banned("ban-rec-1")

    def test_ban_proxy_nonexistent_name(self):
        """ban_proxy with unknown name should return False."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://ban-exist1:8080", name="ban-exist-1")

        result = pool.ban_proxy("nonexistent-ban")

        assert result is False

    def test_ban_proxy_no_other_available(self):
        """ban_proxy on the only proxy should return False."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://ban-solo:8080", name="ban-solo-proxy")

        result = pool.ban_proxy("ban-solo-proxy")

        assert result is False
        assert pool.proxies[0].banned is True
        assert pool.proxies[0].is_available is False

    def test_ban_proxy_by_current_proxy(self):
        """ban_proxy with None should ban the current proxy."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://ban-cur1:8080", name="ban-cur-1")
        pool.add_proxy(http_url="http://ban-cur2:8080", name="ban-cur-2")

        result = pool.ban_proxy(None)

        assert result is True
        assert pool.proxies[0].banned is True
        assert pool.proxies[0].is_available is False
        assert pool.current_index == 1

    def test_banned_proxy_never_recovers(self):
        """Banned proxies must not recover from cooldown checks."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://perm-ban:8080", name="perm-ban-proxy")
        pool.add_proxy(http_url="http://avail:8080", name="avail-proxy")

        pool.ban_proxy("perm-ban-proxy")

        result = pool.get_current_proxy()
        assert result is not None

        assert pool.proxies[0].banned is True
        assert pool.proxies[0].is_available is False

    def test_mark_success_does_not_revive_banned_proxy(self):
        """mark_success on a banned proxy must not reset is_available."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://revive1:8080", name="revive-1")
        pool.add_proxy(http_url="http://revive2:8080", name="revive-2")

        pool.ban_proxy("revive-1")
        pool.proxies[0].mark_success()

        assert pool.proxies[0].banned is True
        assert pool.proxies[0].is_available is False

    def test_get_current_proxy_skips_banned(self):
        """get_current_proxy must never return a banned proxy."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://skip-ban1:8080", name="skip-ban-1")
        pool.add_proxy(http_url="http://skip-ban2:8080", name="skip-ban-2")

        pool.ban_proxy("skip-ban-1")
        # Force is_available back to True to simulate the former bug path
        pool.proxies[0].is_available = True

        result = pool.get_current_proxy()
        assert result == {"http": "http://skip-ban2:8080"}

    def test_get_next_proxy_skips_banned(self):
        """get_next_proxy must never return a banned proxy."""
        pool = ProxyPool()
        pool.add_proxy(http_url="http://skip-next1:8080", name="skip-next-1")
        pool.add_proxy(http_url="http://skip-next2:8080", name="skip-next-2")
        pool.add_proxy(http_url="http://skip-next3:8080", name="skip-next-3")

        pool.ban_proxy("skip-next-2")
        pool.proxies[1].is_available = True

        results = [pool.get_next_proxy() for _ in range(4)]
        banned_dict = {"http": "http://skip-next2:8080"}
        assert banned_dict not in results


# ─────────────────────────────────────────────────────────────────────────
# P2-D — health-weighted next_proxy selection.
# Verifies the optional ``health_provider`` hook used by the cross-instance
# proxy coordinator: when scores are present, higher-score proxies must be
# picked more often; missing/invalid scores degrade to uniform random;
# without a provider the default round-robin behaviour is preserved.
# ─────────────────────────────────────────────────────────────────────────


class TestHealthWeightedNextProxy:
    def _build_pool(self, names):
        pool = ProxyPool()
        for name in names:
            pool.add_proxy(http_url=f"http://{name}:8080", name=name)
        return pool

    def test_round_robin_preserved_without_health_provider(self):
        pool = self._build_pool(["rr-1", "rr-2", "rr-3"])
        # 3 calls visit each proxy exactly once in round-robin order.
        first_round = [pool.get_next_proxy() for _ in range(3)]
        seen_urls = {next(iter(d.values())) for d in first_round}
        assert seen_urls == {
            "http://rr-1:8080", "http://rr-2:8080", "http://rr-3:8080",
        }

    def test_health_weighted_prefers_higher_scoring_proxies(self):
        """High-score proxy should be picked far more often than low-score."""
        pool = self._build_pool(["good", "bad"])
        scores = {"good": 0.95, "bad": 0.10}
        pool.set_health_provider(lambda name: scores.get(name))

        import random as _r
        _r.seed(42)  # Deterministic for the test.

        picks = {"good": 0, "bad": 0}
        for _ in range(2000):
            d = pool.get_next_proxy()
            url = next(iter(d.values()))
            if "good" in url:
                picks["good"] += 1
            else:
                picks["bad"] += 1
        # With weights ≈ 0.95 / 0.10 (clamped to 0.10 floor), the
        # better proxy gets ~ 0.95 / 1.05 ≈ 90% of the picks.  Allow
        # generous slack for RNG noise.
        assert picks["good"] > 5 * picks["bad"]
        assert picks["bad"] > 0  # Floor must let bad proxy still get traffic.

    def test_unknown_proxies_fall_back_to_neutral_score(self):
        """``provider`` returning None for an unseen proxy must yield neutral."""
        pool = self._build_pool(["seen", "unseen"])
        # Only "seen" has a score; "unseen" returns None → 0.5 baseline.
        pool.set_health_provider(lambda name: 0.5 if name == "seen" else None)

        import random as _r
        _r.seed(0)
        picks = {"seen": 0, "unseen": 0}
        for _ in range(400):
            d = pool.get_next_proxy()
            url = next(iter(d.values()))
            if "unseen" in url:
                picks["unseen"] += 1
            else:
                picks["seen"] += 1
        # With both proxies effectively at 0.5, distribution is roughly
        # uniform.  Allow ±30% slack on a 200/200 expectation.
        assert 100 < picks["seen"] < 300
        assert 100 < picks["unseen"] < 300

    def test_provider_exception_does_not_crash_selection(self):
        pool = self._build_pool(["a", "b"])

        def boom(_name):
            raise RuntimeError("simulated coordinator outage")

        pool.set_health_provider(boom)
        # All scores degrade to 0.5; picks are still legal.
        result = pool.get_next_proxy()
        assert result is not None

    def test_provider_returning_invalid_types_falls_back_to_neutral(self):
        pool = self._build_pool(["a", "b"])

        def weird(name):
            return "not-a-number" if name == "a" else float("nan")

        pool.set_health_provider(weird)
        # Both proxies degrade to 0.5; selection must still succeed.
        result = pool.get_next_proxy()
        assert result is not None

    def test_set_health_provider_to_none_restores_round_robin(self):
        pool = self._build_pool(["x", "y"])
        pool.set_health_provider(lambda name: 1.0 if name == "x" else 0.01)
        # First, weighted: "x" should dominate.
        import random as _r
        _r.seed(123)
        weighted_picks = {"x": 0, "y": 0}
        for _ in range(200):
            d = pool.get_next_proxy()
            url = next(iter(d.values()))
            weighted_picks["x" if "x" in url else "y"] += 1
        assert weighted_picks["x"] > weighted_picks["y"] * 2

        # Now clear the provider; round-robin must resume (alternating).
        pool.set_health_provider(None)
        rr_results = [pool.get_next_proxy() for _ in range(4)]
        urls = [next(iter(d.values())) for d in rr_results]
        # Each proxy appears exactly twice in 4 round-robin picks.
        assert urls.count("http://x:8080") == 2
        assert urls.count("http://y:8080") == 2

    def test_skips_unavailable_proxies_under_weighting(self):
        pool = self._build_pool(["live", "dead"])
        pool.proxies[1].banned = True
        pool.proxies[1].is_available = False
        pool.set_health_provider(lambda name: 0.99 if name == "dead" else 0.01)
        # Even with a much higher weight, the banned proxy MUST never
        # be returned.
        for _ in range(50):
            d = pool.get_next_proxy()
            assert d == {"http": "http://live:8080"}

    def test_golden_no_provider_round_robin_matches_pre_p2d_behaviour(self):
        """Lock the «no coordinator → identical to today» contract.

        With no health_provider installed the iteration order on a
        fresh pool MUST stay fixed round-robin (advance-then-return,
        starting from the proxy AFTER ``current_index=0``), which is
        the sequence the Python ``ProxyPool`` always produced before
        P2-D.  This pins down the fail-open behaviour against
        accidental regressions in the weighted-selection branch.
        """
        pool = self._build_pool(["a", "b", "c"])
        # The pool's pre-P2-D contract advances ``current_index`` first,
        # so the first call returns index 1 (b), then 2 (c), then 0 (a),
        # repeating.  Locking that exact order is the goal.
        results = [pool.get_next_proxy() for _ in range(6)]
        urls = [next(iter(d.values())) for d in results]
        assert urls == [
            "http://b:8080", "http://c:8080", "http://a:8080",
            "http://b:8080", "http://c:8080", "http://a:8080",
        ]
