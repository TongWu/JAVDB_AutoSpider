"""Unit tests for scripts/spider/sleep_manager.py.

Tests that verify exact configuration values and thread-safety properties
that are not covered by the broader integration test
(tests/test_spider_integration.py::TestSleepManagerHumanLike).

Redundant classes removed:
  - TestMovieSleepManagerFactors  → covered by TestSleepManagerHumanLike
  - TestMovieSleepManagerDistribution → covered by TestSleepManagerHumanLike
  - TestThreeFactorWorstCase → covered by TestSleepManagerHumanLike
"""

import os
import sys
import time
import threading

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.spider.sleep_manager import (
    MovieSleepManager,
    PenaltyTracker,
    DualWindowThrottle,
    COMPOSITE_MULTIPLIER_CAP,
    ABSOLUTE_MAX_SLEEP,
    THROTTLE_MAX_WAIT,
)


# ---------------------------------------------------------------------------
# PenaltyTracker — exact tier values and thread safety
# ---------------------------------------------------------------------------


class TestPenaltyTracker:

    def test_no_events_returns_1(self):
        pt = PenaltyTracker()
        assert pt.get_penalty_factor() == 1.0

    def test_single_event(self):
        pt = PenaltyTracker()
        pt.record_event()
        assert pt.get_penalty_factor() == 1.30

    def test_two_events(self):
        pt = PenaltyTracker()
        pt.record_event()
        pt.record_event()
        assert pt.get_penalty_factor() == 1.65

    def test_four_events(self):
        pt = PenaltyTracker()
        for _ in range(4):
            pt.record_event()
        assert pt.get_penalty_factor() == 2.00

    def test_recent_event_count(self):
        pt = PenaltyTracker()
        pt.record_event()
        pt.record_event()
        pt.record_event()
        assert pt.recent_event_count() == 3

    def test_thread_safety(self):
        pt = PenaltyTracker()
        errors = []

        def add_events():
            try:
                for _ in range(100):
                    pt.record_event()
                    pt.get_penalty_factor()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_events) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert pt.recent_event_count() == 400


# ---------------------------------------------------------------------------
# DualWindowThrottle — max wait timeout and thread safety
# ---------------------------------------------------------------------------


class TestDualWindowThrottle:

    def test_under_limit_no_wait(self):
        dwt = DualWindowThrottle(short_window_sec=1.0, short_max=5,
                                  long_window_sec=10.0, long_max=100)
        waited = dwt.wait_if_needed()
        assert waited == 0.0

    def test_max_wait_timeout(self):
        dwt = DualWindowThrottle(short_window_sec=600.0, short_max=1,
                                  long_window_sec=600.0, long_max=1)
        dwt.wait_if_needed()  # fills both windows
        import scripts.spider.sleep_manager as sm
        original = sm.THROTTLE_MAX_WAIT
        sm.THROTTLE_MAX_WAIT = 2.0  # override to 2s for fast test
        try:
            start = time.monotonic()
            waited = dwt.wait_if_needed()
            elapsed = time.monotonic() - start
            assert elapsed >= 1.5
            assert elapsed < 5.0
            assert waited >= 1.5
        finally:
            sm.THROTTLE_MAX_WAIT = original

    def test_thread_safety(self):
        dwt = DualWindowThrottle(short_window_sec=0.5, short_max=50,
                                  long_window_sec=10.0, long_max=200)
        errors = []

        def do_requests():
            try:
                for _ in range(20):
                    dwt.wait_if_needed()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_requests) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ---------------------------------------------------------------------------
# MovieSleepManager — session drift
# ---------------------------------------------------------------------------


class TestSessionDrift:

    def test_drift_shifts_base(self):
        """Multiple instances should have slightly different base ranges."""
        bases = set()
        for _ in range(20):
            mgr = MovieSleepManager(10.0, 20.0)
            bases.add(round(mgr.base_min, 2))
        # Drift is +/-0.5 so we expect at least a few distinct values
        assert len(bases) > 3


# ---------------------------------------------------------------------------
# Volume tiers — exact tier boundary values
# ---------------------------------------------------------------------------


class TestVolumeTiers:

    def test_small_n_no_multiplier(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(10)
        assert mgr._volume_min_mult == 1.0
        assert mgr._volume_max_mult == 1.0

    def test_n_50_asymmetric(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(60)
        # N=60 lands in tier (50, 1.15, 1.30)
        assert mgr._volume_min_mult == 1.15
        assert mgr._volume_max_mult == 1.30

    def test_n_over_250_max(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(300)
        assert mgr._volume_min_mult == 2.20
        assert mgr._volume_max_mult == 2.90

    def test_asymmetric_multipliers(self):
        """max_mult should grow faster than min_mult."""
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(150)
        assert mgr._volume_max_mult > mgr._volume_min_mult
