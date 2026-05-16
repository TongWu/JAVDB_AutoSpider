"""Unit tests for W6.A.2 — apply W5.4 active signals to runtime.

Surfaces under test:

* :meth:`MovieSleepManager.set_global_factor` + ``_recalc_range``
  composition with the existing volume multiplier.
* :meth:`MovieSleepManager.set_pause_until_ms` + ``_wait_for_pause``.
* :func:`state._apply_active_signals` — heartbeat → runtime propagation,
  including the reconcile-to-empty path (signals expire → defaults
  restored).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.runner_registry_client import (  # noqa: E402
    Signal,
)
from packages.python.javdb_spider.runtime.sleep import (  # noqa: E402
    COMPOSITE_MULTIPLIER_CAP,
    MovieSleepManager,
    PenaltyTracker,
    TripleWindowThrottle,
)


# ---------------------------------------------------------------------------
# MovieSleepManager.set_global_factor + _recalc_range
# ---------------------------------------------------------------------------


def _mgr() -> MovieSleepManager:
    return MovieSleepManager(
        sleep_min=6.0,
        sleep_max=10.0,
        penalty_tracker=PenaltyTracker(),
        throttle=TripleWindowThrottle(),
    )


class TestGlobalFactor:
    def test_default_factor_is_one(self):
        m = _mgr()
        before_min, before_max = m.sleep_min, m.sleep_max
        m.set_global_factor(1.0)
        # _recalc_range rounds to 2 decimals; tolerate sub-cent drift.
        assert m.sleep_min == pytest.approx(before_min, abs=0.02)
        assert m.sleep_max == pytest.approx(before_max, abs=0.02)

    def test_factor_3_triples_range(self):
        m = _mgr()
        base_min, base_max = m.base_min, m.base_max
        m.set_global_factor(3.0)
        assert m.sleep_min == pytest.approx(base_min * 3.0, abs=0.02)
        assert m.sleep_max == pytest.approx(base_max * 3.0, abs=0.02)

    def test_factor_below_one_clamped(self):
        m = _mgr()
        m.set_global_factor(0.1)
        # Clamped to 1.0; sleep range unchanged from baseline.
        assert m.sleep_min == pytest.approx(m.base_min, abs=0.02)

    def test_factor_above_cap_clamped(self):
        m = _mgr()
        m.set_global_factor(9999.0)
        # Capped at COMPOSITE_MULTIPLIER_CAP.
        assert m.sleep_max == pytest.approx(m.base_max * COMPOSITE_MULTIPLIER_CAP, abs=0.02)

    def test_factor_composes_with_volume_multiplier(self):
        m = _mgr()
        # Set a 1.5x volume multiplier first (apply_volume_multiplier
        # interpolates from VOLUME_ANCHORS; we go direct).
        with m._lock:
            m._volume_min_mult = 1.5
            m._volume_max_mult = 1.5
            m._recalc_range()
        intermediate = (m.sleep_min, m.sleep_max)
        m.set_global_factor(2.0)
        # 1.5 * 2.0 = 3.0 → range grows by 2.0x vs intermediate (both clamped
        # to COMPOSITE_MULTIPLIER_CAP if applicable).
        assert m.sleep_max == pytest.approx(m.base_max * min(3.0, COMPOSITE_MULTIPLIER_CAP), abs=0.02)
        assert m.sleep_max > intermediate[1]

    def test_non_numeric_factor_silently_ignored(self):
        m = _mgr()
        before = (m.sleep_min, m.sleep_max)
        m.set_global_factor("not-a-number")  # type: ignore[arg-type]
        after = (m.sleep_min, m.sleep_max)
        assert after == before


# ---------------------------------------------------------------------------
# MovieSleepManager.set_pause_until_ms + _wait_for_pause
# ---------------------------------------------------------------------------


class TestPauseGate:
    def test_pause_until_zero_does_not_wait(self):
        m = _mgr()
        m.set_pause_until_ms(0)
        t0 = time.monotonic()
        waited = m._wait_for_pause()
        assert waited == 0.0
        assert time.monotonic() - t0 < 0.1

    def test_pause_in_past_does_not_wait(self):
        m = _mgr()
        m.set_pause_until_ms(int((time.time() - 60) * 1000))
        waited = m._wait_for_pause()
        assert waited == 0.0

    def test_pause_blocks_for_remaining_duration(self):
        m = _mgr()
        ttl_ms = 200  # 200 ms — fast enough for tests, long enough to measure
        m.set_pause_until_ms(int((time.time() + ttl_ms / 1000.0) * 1000))
        t0 = time.monotonic()
        waited = m._wait_for_pause()
        elapsed = time.monotonic() - t0
        assert waited >= 0.15
        assert elapsed >= 0.15

    def test_clearing_pause_with_zero_resumes(self):
        m = _mgr()
        m.set_pause_until_ms(int((time.time() + 60) * 1000))
        m.set_pause_until_ms(0)
        waited = m._wait_for_pause()
        assert waited == 0.0


# ---------------------------------------------------------------------------
# state._apply_active_signals — full reconciliation
# ---------------------------------------------------------------------------


def _sig(kind: str, **kw) -> Signal:
    return Signal(
        id=kw.pop("id", f"sig-{kind}-{id(kw)}"),
        kind=kind,
        expires_at_ms=kw.pop("expires_at_ms", int((time.time() + 60) * 1000)),
        created_at_ms=kw.pop("created_at_ms", int(time.time() * 1000)),
        reason=kw.pop("reason", None),
        factor=kw.pop("factor", None),
        proxy_id=kw.pop("proxy_id", None),
    )


@pytest.fixture(autouse=True)
def isolate_state():
    """Each test gets a fresh signal-state snapshot to undo after run."""
    import packages.python.javdb_spider.runtime.state as state_mod
    import packages.python.javdb_spider.runtime.sleep as sleep_mod
    saved_bans = set(state_mod._signal_banned_proxies)
    saved_factor = sleep_mod.movie_sleep_mgr._global_factor
    saved_pause = sleep_mod.movie_sleep_mgr._pause_until_ms
    yield
    state_mod._signal_banned_proxies = saved_bans
    sleep_mod.movie_sleep_mgr._global_factor = saved_factor
    sleep_mod.movie_sleep_mgr._pause_until_ms = saved_pause
    sleep_mod.movie_sleep_mgr._recalc_range()


class TestApplyActiveSignals:
    def test_throttle_global_signal_sets_factor(self):
        import packages.python.javdb_spider.runtime.sleep as sleep_mod
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        _apply_active_signals([_sig("throttle_global", factor=2.5)])
        assert sleep_mod.movie_sleep_mgr._global_factor == 2.5

    def test_pause_all_signal_sets_expiry(self):
        import packages.python.javdb_spider.runtime.sleep as sleep_mod
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        exp = int((time.time() + 5) * 1000)
        _apply_active_signals([_sig("pause_all", expires_at_ms=exp)])
        assert sleep_mod.movie_sleep_mgr._pause_until_ms == exp

    def test_ban_proxy_signal_calls_pool_ban(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        import packages.python.javdb_spider.runtime.state as state_mod
        fake_pool = MagicMock()
        with patch.object(state_mod, "global_proxy_pool", fake_pool):
            _apply_active_signals([_sig("ban_proxy", proxy_id="Proxy-3")])
        fake_pool.ban_proxy.assert_called_once_with("Proxy-3")

    def test_ban_proxy_signal_idempotent_across_heartbeats(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        import packages.python.javdb_spider.runtime.state as state_mod
        fake_pool = MagicMock()
        with patch.object(state_mod, "global_proxy_pool", fake_pool):
            sig = _sig("ban_proxy", proxy_id="Proxy-X")
            _apply_active_signals([sig])
            _apply_active_signals([sig])  # second tick same signal
            _apply_active_signals([sig])  # third tick same signal
        # ban_proxy() must NOT be called repeatedly.
        assert fake_pool.ban_proxy.call_count == 1

    def test_ban_proxy_signal_unbanned_when_removed_from_active_set(self):
        """W6.A.2 follow-up — signal TTL expiry triggers local unban."""
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        import packages.python.javdb_spider.runtime.state as state_mod
        fake_pool = MagicMock()
        with patch.object(state_mod, "global_proxy_pool", fake_pool):
            _apply_active_signals([_sig("ban_proxy", proxy_id="Proxy-Y")])
            assert fake_pool.ban_proxy.call_count == 1
            # Signal TTL'd on the Worker → empty list on next heartbeat.
            _apply_active_signals([])
            fake_pool.unban_proxy.assert_called_once_with("Proxy-Y")
        # Reapplying after expiry should ban again (idempotent state-based).
        with patch.object(state_mod, "global_proxy_pool", fake_pool):
            _apply_active_signals([_sig("ban_proxy", proxy_id="Proxy-Y")])
            assert fake_pool.ban_proxy.call_count == 2

    def test_ban_proxy_partial_expiry_unbans_only_removed(self):
        """Two bans, one expires → only that one is unbanned."""
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        import packages.python.javdb_spider.runtime.state as state_mod
        fake_pool = MagicMock()
        with patch.object(state_mod, "global_proxy_pool", fake_pool):
            _apply_active_signals([
                _sig("ban_proxy", proxy_id="Keep", id="A"),
                _sig("ban_proxy", proxy_id="Drop", id="B"),
            ])
            assert fake_pool.ban_proxy.call_count == 2
            # Only "Keep" survives next heartbeat.
            _apply_active_signals([
                _sig("ban_proxy", proxy_id="Keep", id="A"),
            ])
        # Exactly one unban call, for the dropped proxy.
        fake_pool.unban_proxy.assert_called_once_with("Drop")

    def test_empty_signal_list_restores_defaults(self):
        import packages.python.javdb_spider.runtime.sleep as sleep_mod
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        _apply_active_signals([_sig("throttle_global", factor=3.0)])
        assert sleep_mod.movie_sleep_mgr._global_factor == 3.0
        # Now signals expire (Worker drops them from the list).
        _apply_active_signals([])
        assert sleep_mod.movie_sleep_mgr._global_factor == 1.0
        assert sleep_mod.movie_sleep_mgr._pause_until_ms == 0

    def test_multiple_throttle_global_signals_take_max(self):
        import packages.python.javdb_spider.runtime.sleep as sleep_mod
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        _apply_active_signals([
            _sig("throttle_global", factor=1.5, id="A"),
            _sig("throttle_global", factor=4.0, id="B"),
            _sig("throttle_global", factor=2.0, id="C"),
        ])
        assert sleep_mod.movie_sleep_mgr._global_factor == 4.0

    def test_multiple_signal_kinds_apply_simultaneously(self):
        import packages.python.javdb_spider.runtime.sleep as sleep_mod
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        import packages.python.javdb_spider.runtime.state as state_mod
        fake_pool = MagicMock()
        exp = int((time.time() + 30) * 1000)
        with patch.object(state_mod, "global_proxy_pool", fake_pool):
            _apply_active_signals([
                _sig("throttle_global", factor=2.0),
                _sig("pause_all", expires_at_ms=exp),
                _sig("ban_proxy", proxy_id="P-1"),
                _sig("ban_proxy", proxy_id="P-2"),
            ])
        assert sleep_mod.movie_sleep_mgr._global_factor == 2.0
        assert sleep_mod.movie_sleep_mgr._pause_until_ms == exp
        assert fake_pool.ban_proxy.call_count == 2

    def test_malformed_signal_does_not_break_others(self):
        import packages.python.javdb_spider.runtime.sleep as sleep_mod
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        # Mix a malformed entry (None) with valid ones — should not raise.
        _apply_active_signals([
            None,  # malformed
            _sig("throttle_global", factor=2.0),
        ])
        assert sleep_mod.movie_sleep_mgr._global_factor == 2.0

    def test_unknown_signal_kind_is_ignored(self):
        import packages.python.javdb_spider.runtime.sleep as sleep_mod
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        _apply_active_signals([_sig("totally_unknown_kind", factor=99.0)])
        assert sleep_mod.movie_sleep_mgr._global_factor == 1.0
        assert sleep_mod.movie_sleep_mgr._pause_until_ms == 0

    def test_concurrent_calls_safe(self):
        from packages.python.javdb_spider.runtime.state import (
            _apply_active_signals,
        )
        import packages.python.javdb_spider.runtime.state as state_mod
        fake_pool = MagicMock()
        errors: list = []

        def worker():
            try:
                with patch.object(state_mod, "global_proxy_pool", fake_pool):
                    for i in range(20):
                        _apply_active_signals([
                            _sig("ban_proxy", proxy_id=f"P-{i}"),
                        ])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
