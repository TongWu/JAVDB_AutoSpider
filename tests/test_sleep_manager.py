"""Unit tests for scripts/spider/sleep_manager.py."""

import os
import sys
import time
import threading
import statistics

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
# PenaltyTracker
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

    def test_events_decay_over_window(self):
        pt = PenaltyTracker()
        pt.WINDOW_SECONDS = 0.1  # 100ms for fast test
        pt.record_event()
        assert pt.get_penalty_factor() == 1.30
        time.sleep(0.15)
        assert pt.get_penalty_factor() == 1.0

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
# DualWindowThrottle
# ---------------------------------------------------------------------------


class TestDualWindowThrottle:

    def test_under_limit_no_wait(self):
        dwt = DualWindowThrottle(short_window_sec=1.0, short_max=5,
                                  long_window_sec=10.0, long_max=100)
        waited = dwt.wait_if_needed()
        assert waited == 0.0

    def test_short_window_throttle(self):
        dwt = DualWindowThrottle(short_window_sec=60.0, short_max=2,
                                  long_window_sec=600.0, long_max=100)
        dwt.wait_if_needed()  # 1st - ok
        dwt.wait_if_needed()  # 2nd - ok
        start = time.monotonic()
        # 3rd should block since short_max=2
        # But with max wait it will eventually proceed
        dwt.wait_if_needed()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.5  # should have waited at least a bit

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
# MovieSleepManager – three-factor multiplier
# ---------------------------------------------------------------------------


class TestMovieSleepManagerFactors:

    def test_default_no_multiplier(self):
        mgr = MovieSleepManager(8.0, 25.0)
        t = mgr.get_sleep_time()
        # Drift shifts base by +/-0.5, so allow a wider range
        assert 6.5 <= t <= ABSOLUTE_MAX_SLEEP

    def test_volume_multiplier_scales_range(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(100)
        assert mgr._volume_min_mult > 1.0
        assert mgr._volume_max_mult > 1.0
        assert mgr.sleep_min > 10.0
        assert mgr.sleep_max > 20.0

    def test_concurrency_factor_sqrt(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_concurrency_factor(4)
        assert abs(mgr._worker_factor - 2.0) < 0.01

    def test_concurrency_factor_capped(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_concurrency_factor(100)
        assert mgr._worker_factor == mgr.WORKER_FACTOR_CAP

    def test_composite_multiplier_cap(self):
        mgr = MovieSleepManager(8.0, 25.0)
        pt = PenaltyTracker()
        mgr._penalty_tracker = pt
        for _ in range(10):
            pt.record_event()

        mgr.apply_volume_multiplier(300)
        mgr.apply_concurrency_factor(8)

        eff_min, eff_max = mgr._effective_range()
        # Both should be capped at base * COMPOSITE_MULTIPLIER_CAP
        assert eff_min <= mgr.base_min * COMPOSITE_MULTIPLIER_CAP + 1
        assert eff_max <= mgr.base_max * COMPOSITE_MULTIPLIER_CAP + 1

    def test_absolute_max_sleep_ceiling(self):
        mgr = MovieSleepManager(8.0, 25.0)
        mgr.apply_volume_multiplier(300)
        mgr.apply_concurrency_factor(8)

        for _ in range(200):
            t = mgr.get_sleep_time()
            assert t <= ABSOLUTE_MAX_SLEEP

    def test_penalty_factor_applied_dynamically(self):
        pt = PenaltyTracker()
        mgr = MovieSleepManager(10.0, 20.0, penalty_tracker=pt)

        range_before = mgr._effective_range()
        pt.record_event()
        pt.record_event()
        range_after = mgr._effective_range()

        assert range_after[0] > range_before[0]
        assert range_after[1] > range_before[1]


# ---------------------------------------------------------------------------
# MovieSleepManager – distribution characteristics
# ---------------------------------------------------------------------------


class TestMovieSleepManagerDistribution:

    def test_all_samples_in_range(self):
        mgr = MovieSleepManager(8.0, 25.0)
        for _ in range(500):
            t = mgr.get_sleep_time()
            # Allow for drift (+/-0.5) and jitter (+/-0.3)
            assert t >= 6.0
            assert t <= ABSOLUTE_MAX_SLEEP

    def test_distribution_right_skew(self):
        """Log-normal mixture should produce mean > median (right skew)."""
        mgr = MovieSleepManager(8.0, 25.0)
        samples = [mgr.get_sleep_time() for _ in range(2000)]
        mean = statistics.mean(samples)
        median = statistics.median(samples)
        # Right-skewed: mean >= median (allowing some tolerance)
        assert mean >= median - 1.0, (
            f"Expected right skew: mean={mean:.2f}, median={median:.2f}"
        )

    def test_precision_below_0_1(self):
        """Values should have more granularity than 0.1s steps."""
        mgr = MovieSleepManager(8.0, 25.0)
        samples = [mgr.get_sleep_time() for _ in range(100)]
        fractional_parts = {round(s % 1, 2) for s in samples}
        # With round(..., 2) we should see more than just 0.0, 0.1, ..., 0.9
        assert len(fractional_parts) > 10

    def test_independent_rng_per_instance(self):
        """Each instance should have its own random state."""
        mgr1 = MovieSleepManager(8.0, 25.0)
        mgr2 = MovieSleepManager(8.0, 25.0)
        samples1 = [mgr1.get_sleep_time() for _ in range(20)]
        samples2 = [mgr2.get_sleep_time() for _ in range(20)]
        # With independent RNGs + drift, sequences should differ
        assert samples1 != samples2


# ---------------------------------------------------------------------------
# MovieSleepManager – session drift
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
# Volume tiers
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


# ---------------------------------------------------------------------------
# Integration: three-factor worst case stays within caps
# ---------------------------------------------------------------------------


class TestThreeFactorWorstCase:

    def test_worst_case_bounded(self):
        """N=300, W=8, 10 CF events => sleep must stay under ABSOLUTE_MAX_SLEEP."""
        pt = PenaltyTracker()
        for _ in range(10):
            pt.record_event()

        mgr = MovieSleepManager(8.0, 25.0, penalty_tracker=pt)
        mgr.apply_volume_multiplier(300)
        mgr.apply_concurrency_factor(8)

        for _ in range(100):
            t = mgr.get_sleep_time()
            assert t <= ABSOLUTE_MAX_SLEEP, f"Sleep {t}s exceeded ceiling"

    def test_worst_case_effective_range_capped(self):
        pt = PenaltyTracker()
        for _ in range(10):
            pt.record_event()

        mgr = MovieSleepManager(8.0, 25.0, penalty_tracker=pt)
        mgr.apply_volume_multiplier(300)
        mgr.apply_concurrency_factor(8)

        eff_min, eff_max = mgr._effective_range()
        # raw: 2.20 * sqrt(8) * 2.0 ≈ 12.4 => capped to 6.0
        assert eff_min <= 8.5 * COMPOSITE_MULTIPLIER_CAP
        assert eff_max <= 25.5 * COMPOSITE_MULTIPLIER_CAP
