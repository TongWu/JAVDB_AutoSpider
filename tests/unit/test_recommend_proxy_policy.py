"""Unit tests for W6.B — RecommendProxyPolicy TTL-cached score provider.

Focuses on the caching + timer + shutdown semantics; the underlying
client is mocked so tests stay deterministic.
"""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.recommend_proxy_client import (  # noqa: E402
    ProxyRecommendation,
    RecommendProxyUnavailable,
    RecommendResult,
)
from packages.python.javdb_platform.recommend_proxy_policy import (  # noqa: E402
    RecommendProxyPolicy,
)


def _rec(name: str, score: float) -> ProxyRecommendation:
    return ProxyRecommendation(
        proxy_id=name, score=score, latency_ema_ms=0.0,
        success_count=0, failure_count=0,
        banned=False, requires_cf_bypass=False, available=True,
    )


def _mock_client_returning(*results):
    """Return a mock client whose .recommend(...) cycles through *results*."""
    mock = MagicMock()
    if len(results) == 1:
        mock.recommend.return_value = results[0]
    else:
        mock.recommend.side_effect = list(results)
    return mock


@pytest.fixture
def long_refresh_policy():
    """Policy with a giant refresh interval so the timer never auto-fires.

    Forces the test to drive the cache deterministically via the prime
    refresh in ``start()`` + manual ``_refresh_now`` calls. Avoids
    flaky timer timing.
    """
    client = _mock_client_returning(
        RecommendResult(
            recommendations=[_rec("P-1", 0.9), _rec("P-2", 0.4)],
            queried_proxy_ids=["P-1", "P-2"],
            server_time_ms=1,
        )
    )
    policy = RecommendProxyPolicy(
        client, proxy_ids=["P-1", "P-2"],
        refresh_interval_sec=600.0,  # 10 min, won't fire during the test
        stale_after_sec=300.0,
    )
    yield policy, client
    policy.shutdown()


# ---------------------------------------------------------------------------
# Cache + score_for
# ---------------------------------------------------------------------------


def test_score_for_returns_none_before_start():
    client = _mock_client_returning(RecommendResult())
    policy = RecommendProxyPolicy(client, proxy_ids=["P-1"])
    try:
        assert policy.score_for("P-1") is None
    finally:
        policy.shutdown()


def test_score_for_after_start_returns_cached_score(long_refresh_policy):
    policy, _ = long_refresh_policy
    policy.start()
    assert policy.score_for("P-1") == pytest.approx(0.9)
    assert policy.score_for("P-2") == pytest.approx(0.4)


def test_score_for_unknown_proxy_returns_none(long_refresh_policy):
    policy, _ = long_refresh_policy
    policy.start()
    assert policy.score_for("UNKNOWN-PROXY") is None


def test_score_for_empty_name_returns_none(long_refresh_policy):
    policy, _ = long_refresh_policy
    policy.start()
    assert policy.score_for("") is None


def test_stale_cache_returns_none():
    client = _mock_client_returning(
        RecommendResult(recommendations=[_rec("P-1", 0.7)])
    )
    policy = RecommendProxyPolicy(
        client, proxy_ids=["P-1"],
        refresh_interval_sec=600.0,
        stale_after_sec=0.1,  # 100 ms stale window for tests
    )
    try:
        policy.start()
        assert policy.score_for("P-1") == pytest.approx(0.7)
        time.sleep(0.15)
        assert policy.score_for("P-1") is None
    finally:
        policy.shutdown()


def test_refresh_swap_replaces_cache_atomically():
    client = MagicMock()
    client.recommend.side_effect = [
        RecommendResult(recommendations=[_rec("P-1", 0.2), _rec("P-2", 0.5)]),
        RecommendResult(recommendations=[_rec("P-1", 0.8), _rec("P-3", 0.3)]),
    ]
    policy = RecommendProxyPolicy(
        client, proxy_ids=["P-1", "P-2", "P-3"],
        refresh_interval_sec=600.0,
    )
    try:
        policy.start()
        assert policy.score_for("P-1") == pytest.approx(0.2)
        assert policy.score_for("P-2") == pytest.approx(0.5)
        policy._refresh_now()  # manual second pull
        # P-1 updated, P-3 added, P-2 dropped (no longer in cache).
        assert policy.score_for("P-1") == pytest.approx(0.8)
        assert policy.score_for("P-3") == pytest.approx(0.3)
        assert policy.score_for("P-2") is None
    finally:
        policy.shutdown()


def test_refresh_keeps_previous_cache_on_unavailable():
    client = MagicMock()
    client.recommend.side_effect = [
        RecommendResult(recommendations=[_rec("P-1", 0.6)]),
        RecommendProxyUnavailable("transient"),
    ]
    policy = RecommendProxyPolicy(
        client, proxy_ids=["P-1"],
        refresh_interval_sec=600.0,
    )
    try:
        policy.start()
        assert policy.score_for("P-1") == pytest.approx(0.6)
        policy._refresh_now()  # second call raises → cache preserved
        assert policy.score_for("P-1") == pytest.approx(0.6)
    finally:
        policy.shutdown()


def test_refresh_keeps_previous_cache_on_unexpected_exception():
    client = MagicMock()
    client.recommend.side_effect = [
        RecommendResult(recommendations=[_rec("P-1", 0.6)]),
        RuntimeError("boom"),
    ]
    policy = RecommendProxyPolicy(
        client, proxy_ids=["P-1"],
        refresh_interval_sec=600.0,
    )
    try:
        policy.start()
        assert policy.score_for("P-1") == pytest.approx(0.6)
        policy._refresh_now()
        assert policy.score_for("P-1") == pytest.approx(0.6)
    finally:
        policy.shutdown()


# ---------------------------------------------------------------------------
# Timer / start / shutdown
# ---------------------------------------------------------------------------


def test_start_is_idempotent():
    client = _mock_client_returning(RecommendResult())
    policy = RecommendProxyPolicy(
        client, proxy_ids=["P-1"], refresh_interval_sec=600.0,
    )
    try:
        policy.start()
        policy.start()
        policy.start()
        assert client.recommend.call_count == 1  # only one prime refresh
    finally:
        policy.shutdown()


def test_shutdown_is_idempotent():
    client = _mock_client_returning(RecommendResult())
    policy = RecommendProxyPolicy(
        client, proxy_ids=["P-1"], refresh_interval_sec=600.0,
    )
    policy.start()
    policy.shutdown()
    # Calling twice should not raise.
    policy.shutdown()


def test_constructor_filters_blank_proxy_ids():
    client = _mock_client_returning(RecommendResult())
    policy = RecommendProxyPolicy(
        client, proxy_ids=["", "P-1", "   ", "P-2"],
        refresh_interval_sec=600.0,
    )
    try:
        policy.start()
        # Internal list contains only the cleaned 2 entries.
        assert policy._proxy_ids == ["P-1", "P-2"]
        # The mock client receives the cleaned list.
        client.recommend.assert_called_once()
        call_args = client.recommend.call_args
        assert call_args[0][0] == ["P-1", "P-2"]
    finally:
        policy.shutdown()


def test_refresh_interval_floored_at_5_seconds():
    client = _mock_client_returning(RecommendResult())
    policy = RecommendProxyPolicy(
        client, proxy_ids=["P-1"],
        refresh_interval_sec=0.0,  # caller sends 0; constructor floors to 5
    )
    assert policy._refresh_interval_sec >= 5.0
    policy.shutdown()
