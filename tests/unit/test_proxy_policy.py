"""Unit tests for the identity / selection helpers in proxy_policy (W3.3).

The CLI side of proxy_policy (normalize_proxy_mode, should_proxy_module,
resolve_proxy_override, etc.) is covered in test_parallel_login.py. This
file focuses on the W3.3 additions:

* :func:`normalize_proxy_id` — moved from proxy_coordinator_client._normalize_proxy_id
* :func:`is_proxy_usable` — new predicate that unifies 5 inline checks in
  proxy_pool.py
"""

import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.proxy_policy import (  # noqa: E402
    is_proxy_usable,
    normalize_proxy_id,
)


# ---------------------------------------------------------------------------
# normalize_proxy_id
# ---------------------------------------------------------------------------


class TestNormalizeProxyId:
    def test_trimmed_non_empty_name_used_verbatim(self):
        assert normalize_proxy_id("  Proxy-1  ") == "Proxy-1"

    def test_empty_string_with_fallback_uses_hash(self):
        result = normalize_proxy_id("", fallback_seed="10.0.0.1:8080")
        assert result.startswith("proxy-")
        assert len(result) == len("proxy-") + 16

    def test_whitespace_only_with_fallback_uses_hash(self):
        result = normalize_proxy_id("   ", fallback_seed="10.0.0.1:8080")
        assert result.startswith("proxy-")

    def test_none_with_fallback_uses_hash(self):
        result = normalize_proxy_id(None, fallback_seed="10.0.0.1:8080")
        assert result.startswith("proxy-")

    def test_same_fallback_seed_yields_same_id(self):
        """Determinism — every runner must derive the same DO key."""
        a = normalize_proxy_id(None, fallback_seed="proxy.example.com:8080")
        b = normalize_proxy_id(None, fallback_seed="proxy.example.com:8080")
        assert a == b

    def test_different_fallback_seeds_yield_different_ids(self):
        a = normalize_proxy_id(None, fallback_seed="proxy-a:8080")
        b = normalize_proxy_id(None, fallback_seed="proxy-b:8080")
        assert a != b

    def test_no_input_raises(self):
        with pytest.raises(ValueError, match="proxy_id is empty"):
            normalize_proxy_id(None)

    def test_long_name_truncated_to_256(self):
        long_name = "a" * 500
        assert len(normalize_proxy_id(long_name)) == 256

    def test_coordinator_client_alias_matches(self):
        """The _normalize_proxy_id re-export must alias the same function."""
        from packages.python.javdb_platform.proxy_coordinator_client import (
            _normalize_proxy_id,
        )
        assert _normalize_proxy_id is normalize_proxy_id


# ---------------------------------------------------------------------------
# is_proxy_usable
# ---------------------------------------------------------------------------


class _FakeProxy:
    """Minimal duck-typed stand-in for ProxyInfo / RustProxyInfo.

    is_proxy_usable is intentionally duck-typed so the Rust-backed and
    Python-backed pools can share the predicate; this fake captures only
    the three attributes / method the predicate touches.
    """

    def __init__(self, is_available=True, banned=False, in_cooldown=False):
        self.is_available = is_available
        self.banned = banned
        self._in_cooldown = in_cooldown

    def is_in_cooldown(self):
        return self._in_cooldown


class TestIsProxyUsable:
    def test_all_clear_proxy_is_usable(self):
        assert is_proxy_usable(_FakeProxy()) is True

    def test_unavailable_proxy_not_usable(self):
        assert is_proxy_usable(_FakeProxy(is_available=False)) is False

    def test_banned_proxy_not_usable(self):
        # Banned proxies in the pool always have ``is_available=False`` per
        # the pool's invariant; the predicate still enforces both checks
        # so the contract holds even if that invariant ever drifts.
        assert is_proxy_usable(_FakeProxy(banned=True)) is False

    def test_in_cooldown_proxy_not_usable(self):
        assert is_proxy_usable(_FakeProxy(in_cooldown=True)) is False

    def test_banned_overrides_is_available(self):
        """Defensive: even if invariant broken (banned + available), reject."""
        assert is_proxy_usable(_FakeProxy(is_available=True, banned=True)) is False
