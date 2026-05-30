"""
Unit tests for the proxy ban manager.

Proxy bans are session-scoped (in-memory only); a ban is permanent for the
lifetime of the process.

ADR-041: the proxy ban manager is Rust-Required — the production manager is the
Rust implementation returned by ``get_ban_manager()``. These tests construct that
same Rust ``ProxyBanManager`` class directly and assert its behaviour contract
(add / is-banned / session-permanent / summary / remove). The former
``TestProxyBanRecord`` cases were dropped with the Python ``ProxyBanRecord``.
"""
import os
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# ADR-041: the production ban manager is the Rust implementation returned by
# get_ban_manager(); these tests construct that same Rust class directly.
from javdb.rust_core import RustProxyBanManager as ProxyBanManager
from javdb.proxy.ban_manager import get_ban_manager


@pytest.fixture(autouse=True)
def _clear_rust_ban_manager():
    """ADR-041: clear the process-global Rust ban manager around each test so
    session bans recorded via the singleton don't leak across cases (the Rust
    GLOBAL_BAN_MANAGER OnceCell persists even when the Python wrapper is reset)."""
    def _clear():
        mgr = get_ban_manager()
        for name in list(mgr.get_banned_proxy_names()):
            mgr.remove_ban(name)

    _clear()
    yield
    _clear()


class TestProxyBanManager:
    """Test cases for the (Rust) ban manager — session-scoped, in-memory."""

    def test_init_no_bans(self):
        """A fresh manager starts with no bans."""
        manager = ProxyBanManager()
        assert manager.get_banned_count() == 0

    def test_add_ban(self):
        """Adding a ban records it (with the proxy URL for reporting)."""
        manager = ProxyBanManager()

        manager.add_ban("new-proxy", proxy_url="http://192.168.1.1:8080")

        assert manager.is_proxy_banned("new-proxy")
        record = next(r for r in manager.get_banned_proxies()
                      if r["proxy_name"] == "new-proxy")
        assert record["proxy_url"] == "http://192.168.1.1:8080"

    def test_add_ban_already_banned(self):
        """Re-adding an already-banned proxy is idempotent (still one ban)."""
        manager = ProxyBanManager()

        manager.add_ban("proxy-1")
        manager.add_ban("proxy-1")

        assert manager.is_proxy_banned("proxy-1")
        assert manager.get_banned_count() == 1

    def test_is_proxy_banned_true(self):
        """is_proxy_banned returns True for a banned proxy."""
        manager = ProxyBanManager()

        manager.add_ban("banned-proxy")

        assert manager.is_proxy_banned("banned-proxy") is True

    def test_is_proxy_banned_false(self):
        """is_proxy_banned returns False for a non-banned proxy."""
        manager = ProxyBanManager()

        assert manager.is_proxy_banned("unknown-proxy") is False

    def test_is_proxy_banned_session_permanent(self):
        """Ban is permanent for the session — no expiry."""
        manager = ProxyBanManager()
        manager.add_ban("proxy-1")

        assert manager.is_proxy_banned("proxy-1") is True

    def test_get_banned_proxies(self):
        """get_banned_proxies returns the banned proxies."""
        manager = ProxyBanManager()

        manager.add_ban("proxy-1")
        manager.add_ban("proxy-2")

        banned = manager.get_banned_proxies()

        assert len(banned) == 2
        names = [r["proxy_name"] for r in banned]
        assert "proxy-1" in names
        assert "proxy-2" in names

    def test_get_ban_summary_no_bans(self):
        """get_ban_summary with no banned proxies."""
        manager = ProxyBanManager()

        summary = manager.get_ban_summary(False)

        assert "No proxies currently banned" in summary

    def test_get_ban_summary_with_bans(self):
        """get_ban_summary with a banned proxy."""
        manager = ProxyBanManager()

        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")

        summary = manager.get_ban_summary(False)

        assert "Currently banned proxies: 1" in summary
        assert "proxy-1" in summary
        assert "Banned at:" in summary

    def test_get_ban_summary_with_ip(self):
        """get_ban_summary(include_ip=True) includes the proxy URL."""
        manager = ProxyBanManager()

        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")

        summary = manager.get_ban_summary(True)

        assert "http://192.168.1.1:8080" in summary

    def test_get_ban_summary_without_ip(self):
        """get_ban_summary(include_ip=False) omits the proxy URL."""
        manager = ProxyBanManager()

        manager.add_ban("proxy-1", proxy_url="http://192.168.1.1:8080")

        summary = manager.get_ban_summary(False)

        assert "192.168.1.1" not in summary

    def test_remove_ban(self):
        """remove_ban clears a recorded ban."""
        manager = ProxyBanManager()
        manager.add_ban("proxy-1")
        assert manager.is_proxy_banned("proxy-1") is True

        assert manager.remove_ban("proxy-1") is True
        assert manager.is_proxy_banned("proxy-1") is False

    def test_session_scoped_no_persistence(self):
        """Bans do not persist: a fresh manager starts clean."""
        manager1 = ProxyBanManager()
        manager1.add_ban("proxy-1")
        assert manager1.is_proxy_banned("proxy-1") is True

        manager2 = ProxyBanManager()
        assert manager2.is_proxy_banned("proxy-1") is False


class TestGetBanManager:
    """Test cases for the get_ban_manager singleton accessor."""

    def test_get_ban_manager_creates_singleton(self):
        """get_ban_manager returns the same instance across calls."""
        import javdb.proxy.ban_manager as pbm
        pbm._global_ban_manager = None

        manager1 = get_ban_manager()
        manager2 = get_ban_manager()

        assert manager1 is manager2

        pbm._global_ban_manager = None

    def test_rust_required_guard_raises_without_rust(self, monkeypatch):
        """ADR-041 D4: get_ban_manager raises clearly when the Rust core is absent."""
        import javdb.proxy.ban_manager as pbm
        monkeypatch.setattr(pbm, "RUST_BAN_MANAGER_AVAILABLE", False)
        monkeypatch.setattr(pbm, "_global_ban_manager", None)

        with pytest.raises(RuntimeError, match="requires the Rust core"):
            get_ban_manager()


class TestRemoteBanDispatcher:
    """The cross-runner ban dispatcher function ``_dispatch_remote_ban``.

    ADR-041 made the proxy ban manager Rust-Required; the Rust ``add_ban`` cannot
    reach the Python ``_dispatch_remote_ban`` hook from inside the extension, so
    the former ``ProxyBanManager().add_ban`` → hook wiring tests (which exercised
    the now-removed *Python* ban manager) were dropped. That cross-runner
    dispatch gap on the Rust path is tracked in BFR-009. What remains valid is
    the dispatcher function's own input guarding.
    """

    def setup_method(self):
        from javdb.proxy.ban_manager import (
            set_remote_ban_hook,
            set_remote_unban_hook,
        )
        set_remote_ban_hook(None)
        set_remote_unban_hook(None)

    def teardown_method(self):
        from javdb.proxy.ban_manager import (
            set_remote_ban_hook,
            set_remote_unban_hook,
        )
        set_remote_ban_hook(None)
        set_remote_unban_hook(None)

    def test_remote_hook_skips_empty_proxy_name(self):
        """A falsy name must not reach the hook (which would 4xx)."""
        from javdb.proxy.ban_manager import (
            _dispatch_remote_ban,
            set_remote_ban_hook,
        )
        captured: list = []
        set_remote_ban_hook(lambda name: captured.append(name))
        _dispatch_remote_ban("")
        _dispatch_remote_ban(None)  # type: ignore[arg-type]
        assert captured == []
