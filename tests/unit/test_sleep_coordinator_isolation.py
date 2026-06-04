"""Regression: the module-global ``movie_sleep_mgr`` must not leak a coordinator.

Background: the no-runtime proxy-pool setup path
(``_setup_proxy_coordinator_legacy``) injects a live ``ProxyCoordinatorClient``
into the module-global ``movie_sleep_mgr`` and used to leave it there. Under
full-suite ordering that stale coordinator was copied into a later test's fresh
runtime by ``ensure_sleep_runtime``, breaking
``test_spider_runtime_explicit_callers.py::test_runtime_proxy_coordinator_injects_runtime_sleep``
(it saw the leaked client instead of its own MagicMock).

The autouse ``_reset_global_sleep_coordinator`` fixture in ``tests/conftest.py``
snapshots and restores the coordinator around every test. These two ordered
tests prove that guarantee: the first dirties the global, the second — which
runs immediately after — must see it restored. Remove the fixture and the
second test fails.
"""
import pytest

from javdb.spider.runtime import sleep as sleep_module

# A stand-in coordinator object. Identity comparison is enough; the manager
# never calls into it during these tests.
_SENTINEL = object()

# Set True by the dirtying test so the restoration test can confirm it actually
# ran first. Without this guard, running the restoration test in isolation (the
# global is clean either way) would pass vacuously and hide a broken
# ``_reset_global_sleep_coordinator`` fixture.
_dirtied = False


def test_dirtied_global_coordinator_is_visible_within_the_test():
    """Simulate the historical leak by injecting a sentinel coordinator."""
    global _dirtied
    sleep_module.movie_sleep_mgr.set_coordinator(_SENTINEL, proxy_id="leaked-proxy")
    assert sleep_module.movie_sleep_mgr._coordinator is _SENTINEL
    assert sleep_module.movie_sleep_mgr.has_coordinator() is True
    _dirtied = True


def test_global_coordinator_restored_after_dirtying_test():
    """The autouse fixture must have undone the previous test's mutation."""
    if not _dirtied:
        pytest.skip(
            "requires the dirtying test to run first (full-file order); "
            "asserting in isolation would pass vacuously"
        )
    assert sleep_module.movie_sleep_mgr._coordinator is not _SENTINEL
    assert sleep_module.movie_sleep_mgr._proxy_id != "leaked-proxy"
