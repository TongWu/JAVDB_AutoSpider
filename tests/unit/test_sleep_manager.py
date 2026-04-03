"""Unit tests for packages/python/javdb_spider/runtime/sleep.py.

Covers the v2 overhaul: piecewise-linear interpolation, per-worker semantics,
penalty overflow, micro-break, throttle tightening, and ban recalculation.
"""

import os
import sys
import time
import threading

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from scripts.spider.runtime.sleep import (
    MovieSleepManager,
    PenaltyTracker,
    DualWindowThrottle,
    TripleWindowThrottle,
    COMPOSITE_MULTIPLIER_CAP,
    ABSOLUTE_MAX_SLEEP,
    COOLDOWN_MAX,
    THROTTLE_MAX_WAIT,
    VOLUME_ANCHORS,
    PENALTY_OVERFLOW_WEIGHT,
    MICRO_BREAK_PROB,
    MICRO_BREAK_EXTRA_MIN,
    MICRO_BREAK_FLOOR,
    _interpolate_multiplier,
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
        import scripts.spider.runtime.sleep as sm
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
        assert len(bases) > 3


# ---------------------------------------------------------------------------
# Interpolation — continuity, anchor matching, monotonicity
# ---------------------------------------------------------------------------


class TestInterpolation:

    def test_anchor_points_exact(self):
        """At every anchor n, multipliers must match exactly."""
        for n, expected_min, expected_max in VOLUME_ANCHORS:
            got_min, got_max = _interpolate_multiplier(n)
            assert got_min == expected_min, f"n={n}: min {got_min} != {expected_min}"
            assert got_max == expected_max, f"n={n}: max {got_max} != {expected_max}"

    def test_monotonically_non_decreasing(self):
        """Multipliers must never decrease as n increases (0..250)."""
        prev_min, prev_max = _interpolate_multiplier(0)
        for n in range(1, 251):
            cur_min, cur_max = _interpolate_multiplier(n)
            assert cur_min >= prev_min - 0.01, (
                f"min_mult decreased at n={n}: {cur_min} < {prev_min}"
            )
            assert cur_max >= prev_max - 0.01, (
                f"max_mult decreased at n={n}: {cur_max} < {prev_max}"
            )
            prev_min, prev_max = cur_min, cur_max

    def test_continuity_no_large_jumps(self):
        """Adjacent n values should differ by at most 0.3 in either multiplier."""
        prev_min, prev_max = _interpolate_multiplier(0)
        for n in range(1, 251):
            cur_min, cur_max = _interpolate_multiplier(n)
            assert abs(cur_min - prev_min) <= 0.35, (
                f"min jump at n={n}: {prev_min} → {cur_min}"
            )
            assert abs(cur_max - prev_max) <= 0.35, (
                f"max jump at n={n}: {prev_max} → {cur_max}"
            )
            prev_min, prev_max = cur_min, cur_max

    def test_below_first_anchor(self):
        """n < 0 (edge case) returns first anchor values."""
        assert _interpolate_multiplier(-5) == (1.00, 1.00)

    def test_above_last_anchor(self):
        """n > 200 returns last anchor values (clamped)."""
        assert _interpolate_multiplier(500) == (6.00, 7.00)

    def test_midpoint_interpolation(self):
        """n=5 is midpoint between anchors (0,1.0,1.0) and (4,1.0,1.15).
        Actually n=2 is midpoint of [0,4)."""
        lo_min, lo_max = 1.00, 1.00
        hi_min, hi_max = 1.00, 1.15
        t = 2 / 4  # n=2, range [0,4)
        expected_min = round(lo_min + t * (hi_min - lo_min), 2)
        expected_max = round(lo_max + t * (hi_max - lo_max), 2)
        got_min, got_max = _interpolate_multiplier(2)
        assert got_min == expected_min
        assert got_max == expected_max


# ---------------------------------------------------------------------------
# Per-worker semantics
# ---------------------------------------------------------------------------


class TestPerWorkerSemantics:

    def test_per_worker_division(self):
        """apply_volume_multiplier(300, 5) → per_worker=60."""
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(300, num_workers=5)
        expected_min, expected_max = _interpolate_multiplier(60)
        assert mgr._volume_min_mult == expected_min
        assert mgr._volume_max_mult == expected_max

    def test_single_worker_default(self):
        """Without num_workers, per_worker=total."""
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(50)
        expected_min, expected_max = _interpolate_multiplier(50)
        assert mgr._volume_min_mult == expected_min
        assert mgr._volume_max_mult == expected_max

    def test_more_workers_lower_multiplier(self):
        """More workers → lower per-worker n → lower multipliers."""
        mgr1 = MovieSleepManager(10.0, 20.0)
        mgr2 = MovieSleepManager(10.0, 20.0)
        mgr1.apply_volume_multiplier(200, num_workers=1)
        mgr2.apply_volume_multiplier(200, num_workers=5)
        assert mgr2._volume_min_mult <= mgr1._volume_min_mult
        assert mgr2._volume_max_mult <= mgr1._volume_max_mult


# ---------------------------------------------------------------------------
# Cap & ceiling
# ---------------------------------------------------------------------------


class TestCapAndCeiling:

    def test_composite_cap_value(self):
        assert COMPOSITE_MULTIPLIER_CAP == 10.0

    def test_absolute_max_sleep_value(self):
        assert ABSOLUTE_MAX_SLEEP == 200.0

    def test_cooldown_max_value(self):
        assert COOLDOWN_MAX == 45.0

    def test_cap_limits_effective_range(self):
        """volume=8.0 + penalty=2.0 → raw=16.0, capped at 10.0 + overflow."""
        pt = PenaltyTracker()
        for _ in range(10):
            pt.record_event()
        mgr = MovieSleepManager(8.0, 25.0, penalty_tracker=pt)
        mgr.apply_volume_multiplier(200)

        eff_min, eff_max = mgr._effective_range()
        base_max_capped = mgr.base_max * COMPOSITE_MULTIPLIER_CAP
        assert eff_max <= base_max_capped + 20, (
            f"eff_max {eff_max} should be near cap"
        )

    def test_get_sleep_time_respects_absolute_max(self):
        """All samples (including micro-breaks) must not exceed ABSOLUTE_MAX_SLEEP."""
        pt = PenaltyTracker()
        for _ in range(10):
            pt.record_event()
        mgr = MovieSleepManager(8.0, 25.0, penalty_tracker=pt)
        mgr.apply_volume_multiplier(200)

        normal_count = 0
        for _ in range(2000):
            t = mgr.get_sleep_time()
            assert t <= ABSOLUTE_MAX_SLEEP, (
                f"Sleep {t} exceeds ABSOLUTE_MAX_SLEEP ({ABSOLUTE_MAX_SLEEP})"
            )
            eff_min, eff_max = mgr._effective_range()
            if t <= eff_max + 1:
                normal_count += 1
        assert normal_count > 1800


# ---------------------------------------------------------------------------
# Penalty overflow
# ---------------------------------------------------------------------------


class TestPenaltyOverflow:

    def test_no_overflow_when_below_cap(self):
        """When raw multiplier < cap, no overflow is added."""
        mgr = MovieSleepManager(8.0, 25.0)
        mgr.apply_volume_multiplier(20)
        eff_min, eff_max = mgr._effective_range()
        assert eff_min == round(mgr.base_min * 1.35, 2)
        assert eff_max == round(mgr.base_max * 1.65, 2)

    def test_overflow_adds_flat_seconds(self):
        """volume max_mult=7.0, penalty=2.0 → raw_max_mult=14.0, overflow=4.0 → +8s."""
        pt = PenaltyTracker()
        for _ in range(4):
            pt.record_event()
        assert pt.get_penalty_factor() == 2.0

        mgr = MovieSleepManager(8.0, 25.0, penalty_tracker=pt)
        mgr.apply_volume_multiplier(200)
        assert mgr._volume_min_mult == 6.0
        assert mgr._volume_max_mult == 7.0

        eff_min, eff_max = mgr._effective_range()

        raw_max = 7.0 * 2.0  # 14.0 (volume max_mult × penalty)
        overflow_max = raw_max - COMPOSITE_MULTIPLIER_CAP  # 4.0
        expected_max = round(
            mgr.base_max * COMPOSITE_MULTIPLIER_CAP
            + overflow_max * PENALTY_OVERFLOW_WEIGHT,
            2,
        )
        assert eff_max == expected_max, (
            f"eff_max={eff_max}, expected={expected_max}"
        )

    def test_penalty_still_effective_at_high_volume(self):
        """Even at high volume tiers, adding penalty events must
        increase the effective range."""
        pt = PenaltyTracker()
        mgr = MovieSleepManager(8.0, 25.0, penalty_tracker=pt)
        mgr.apply_volume_multiplier(200)

        eff_before = mgr._effective_range()
        for _ in range(4):
            pt.record_event()
        eff_after = mgr._effective_range()

        assert eff_after[0] > eff_before[0], "penalty must increase eff_min"
        assert eff_after[1] > eff_before[1], "penalty must increase eff_max"


# ---------------------------------------------------------------------------
# Micro-break
# ---------------------------------------------------------------------------


class TestMicroBreak:

    def test_micro_break_occurs(self):
        """Over many samples, ~4% should be micro-breaks (above eff_max)."""
        mgr = MovieSleepManager(8.0, 25.0)
        eff_min, eff_max = mgr._effective_range()

        micro_count = 0
        total = 5000
        for _ in range(total):
            t = mgr.get_sleep_time()
            if t > eff_max + MICRO_BREAK_EXTRA_MIN - 1:
                micro_count += 1

        ratio = micro_count / total
        assert 0.01 < ratio < 0.10, (
            f"Micro-break ratio {ratio:.3f} outside expected range"
        )

    def test_micro_break_above_eff_max_low_volume(self):
        """At low volume, micro-breaks must be >= MICRO_BREAK_FLOOR."""
        mgr = MovieSleepManager(8.0, 25.0)
        for _ in range(500):
            t = mgr.get_sleep_time()
            eff_min, eff_max = mgr._effective_range()
            if t > eff_max + 5:
                assert t >= MICRO_BREAK_FLOOR - 1, (
                    f"Micro-break {t:.2f} below floor {MICRO_BREAK_FLOOR}"
                )

    def test_micro_break_above_eff_max_high_volume(self):
        """At high volume, micro-breaks must be above eff_max (capped at ABSOLUTE_MAX_SLEEP)."""
        mgr = MovieSleepManager(8.0, 25.0)
        mgr.apply_volume_multiplier(100)
        eff_min, eff_max = mgr._effective_range()

        for _ in range(500):
            t = mgr.get_sleep_time()
            assert t <= ABSOLUTE_MAX_SLEEP, (
                f"Sleep {t} exceeds ABSOLUTE_MAX_SLEEP ({ABSOLUTE_MAX_SLEEP})"
            )
            if t > eff_max + 5:
                expected_floor = min(eff_max + MICRO_BREAK_EXTRA_MIN - 1, ABSOLUTE_MAX_SLEEP)
                assert t >= expected_floor


# ---------------------------------------------------------------------------
# Throttle tighten
# ---------------------------------------------------------------------------


class TestThrottleTighten:

    def test_tighten_above_50(self):
        twt = TripleWindowThrottle()
        assert twt.short_max == 3
        twt.tighten_short_window(50)
        assert twt.short_max == 2

    def test_tighten_respects_low_baseline(self):
        twt = TripleWindowThrottle(short_max=1)
        twt.tighten_short_window(50)
        assert twt.short_max == 1

    def test_tighten_reversible(self):
        twt = TripleWindowThrottle()
        twt.tighten_short_window(50)
        assert twt.short_max == 2
        twt.tighten_short_window(49)
        assert twt.short_max == 3

    def test_no_tighten_below_50(self):
        twt = TripleWindowThrottle()
        twt.tighten_short_window(49)
        assert twt.short_max == 3

    def test_apply_volume_multiplier_triggers_tighten(self):
        twt = TripleWindowThrottle()
        mgr = MovieSleepManager(8.0, 25.0, throttle=twt)
        mgr.apply_volume_multiplier(60)
        assert twt.short_max == 2

    def test_apply_volume_multiplier_no_tighten_low_n(self):
        twt = TripleWindowThrottle()
        mgr = MovieSleepManager(8.0, 25.0, throttle=twt)
        mgr.apply_volume_multiplier(10)
        assert twt.short_max == 3


# ---------------------------------------------------------------------------
# Volume tiers — backward-compatible tests updated for interpolation
# ---------------------------------------------------------------------------


class TestVolumeTiers:

    def test_small_n_no_multiplier(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(3)
        assert mgr._volume_min_mult == _interpolate_multiplier(3)[0]
        assert mgr._volume_max_mult == _interpolate_multiplier(3)[1]

    def test_anchor_60(self):
        """n=60 lies between anchors (50,…) and (100,…); max_mult interpolates to 2.88."""
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(60)
        assert mgr._volume_min_mult == 2.20
        assert mgr._volume_max_mult == 2.88

    def test_anchor_200(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(200)
        assert mgr._volume_min_mult == 6.00
        assert mgr._volume_max_mult == 7.00

    def test_above_max_anchor(self):
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(500)
        assert mgr._volume_min_mult == 6.00
        assert mgr._volume_max_mult == 7.00

    def test_asymmetric_multipliers(self):
        """max_mult should grow faster than min_mult."""
        mgr = MovieSleepManager(10.0, 20.0)
        mgr.apply_volume_multiplier(100)
        assert mgr._volume_max_mult > mgr._volume_min_mult

    def test_volume_multiplier_increases_sleep_range(self):
        """High volume should widen the effective sleep range."""
        mgr = MovieSleepManager(10.0, 20.0)
        before_min, before_max = mgr.sleep_min, mgr.sleep_max
        mgr.apply_volume_multiplier(200)
        assert mgr.sleep_min > before_min
        assert mgr.sleep_max > before_max
