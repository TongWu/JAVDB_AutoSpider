"""Randomised movie sleep with adaptive throttling.

Provides human-like request pacing via three cooperating components:

- ``MovieSleepManager`` — per-worker sleep with piecewise-linear volume
  multiplier, log-normal distribution, penalty overflow, and micro-break
  sampling for human-like pacing.
- ``PenaltyTracker`` — shared across workers; tracks recent CF/failure events
  and computes a dynamic penalty factor that decays over time.
- ``TripleWindowThrottle`` — per-worker rate limiter; enforces short-window
  burst, medium-window budget, and a long (30 min) rolling cap.  Each proxy
  worker owns an independent instance because proxy IPs are independent.
  ``DualWindowThrottle`` is an alias for backward compatibility.

Thread-safe via ``threading.Lock``.  **Not** compatible with
``multiprocessing`` — if the concurrency model ever changes, these classes
must be adapted to use ``multiprocessing.Manager`` or similar.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections import deque
from typing import Tuple

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tuned base sleep range (seconds).  These values are calibrated for the
# adaptive two-factor system and should NOT be tweaked by users.
# Override ONLY via env var ``VAR_MOVIE_SLEEP`` for CI/testing/emergency.
# ---------------------------------------------------------------------------
_BASE_MIN = 6
_BASE_MAX = 20

# ---------------------------------------------------------------------------
# Safety caps
# ---------------------------------------------------------------------------
COMPOSITE_MULTIPLIER_CAP = 10.0
ABSOLUTE_MAX_SLEEP = 200.0  # seconds
COOLDOWN_FRACTION = 0.5     # cooldown = eff_min * this fraction
COOLDOWN_MAX = 45.0         # hard ceiling for cooldown durations
PENALTY_OVERFLOW_WEIGHT = 2.0  # extra seconds per 1.0 overflow above cap

# ---------------------------------------------------------------------------
# Micro-break (human-like long pause) — relative to current eff_max
# ---------------------------------------------------------------------------
MICRO_BREAK_PROB = 0.04
MICRO_BREAK_EXTRA_MIN = 30.0
MICRO_BREAK_EXTRA_MAX = 120.0
MICRO_BREAK_FLOOR = 60.0

# ---------------------------------------------------------------------------
# Volume anchors — piecewise-linear interpolation (per-worker count)
# ---------------------------------------------------------------------------
VOLUME_ANCHORS: list = [
    (0,    1.00,  1.00),
    (4,    1.00,  1.15),
    (10,   1.15,  1.30),
    (20,   1.35,  1.65),
    (30,   1.60,  2.00),
    (40,   1.80,  2.30),
    (50,   2.00,  2.60),
    (60,   2.20,  2.90),
    (70,   2.40,  3.20),
    (80,   2.70,  3.60),
    (90,   3.00,  4.00),
    (100,  3.50,  4.50),
    (150,  5.00,  6.00),
    (200,  7.00,  8.00),
]


def _interpolate_multiplier(n: int) -> Tuple[float, float]:
    """Return (min_mult, max_mult) via piecewise-linear interpolation."""
    if n <= VOLUME_ANCHORS[0][0]:
        return VOLUME_ANCHORS[0][1], VOLUME_ANCHORS[0][2]
    if n >= VOLUME_ANCHORS[-1][0]:
        return VOLUME_ANCHORS[-1][1], VOLUME_ANCHORS[-1][2]
    for i in range(len(VOLUME_ANCHORS) - 1):
        lo_n, lo_min, lo_max = VOLUME_ANCHORS[i]
        hi_n, hi_min, hi_max = VOLUME_ANCHORS[i + 1]
        if lo_n <= n < hi_n:
            t = (n - lo_n) / (hi_n - lo_n)
            return (
                round(lo_min + t * (hi_min - lo_min), 2),
                round(lo_max + t * (hi_max - lo_max), 2),
            )
    return VOLUME_ANCHORS[-1][1], VOLUME_ANCHORS[-1][2]

# ---------------------------------------------------------------------------
# PenaltyTracker
# ---------------------------------------------------------------------------


class PenaltyTracker:
    """Track CF / failure events and compute a dynamic penalty factor.

    Events older than ``WINDOW_SECONDS`` are automatically discarded.
    """

    WINDOW_SECONDS = 300  # 5 minutes
    TIERS = [(1, 1.30), (2, 1.65), (4, 2.00)]

    def __init__(self):
        self._events: deque = deque()
        self._lock = threading.Lock()

    def record_event(self) -> None:
        with self._lock:
            self._events.append(time.monotonic())

    def get_penalty_factor(self) -> float:
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS
        with self._lock:
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            count = len(self._events)
        factor = 1.0
        for threshold, f in self.TIERS:
            if count >= threshold:
                factor = f
        return factor

    def recent_event_count(self) -> int:
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS
        with self._lock:
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            return len(self._events)


# ---------------------------------------------------------------------------
# TripleWindowThrottle (DualWindowThrottle is an alias)
# ---------------------------------------------------------------------------

THROTTLE_MAX_WAIT = 60.0  # seconds – hard ceiling on additional blocking


class TripleWindowThrottle:
    """Enforce three rolling-window request limits (burst, medium, long).

    Defaults: 30s/3, 300s/30, 1800s/200.  Tests may pass custom windows;
    *extra* window should be >= *long* window >= *short* window for sane
    purge behaviour.
    """

    def __init__(
        self,
        short_window_sec: float = 30.0,
        short_max: int = 3,
        long_window_sec: float = 300.0,
        long_max: int = 30,
        extra_window_sec: float = 1800.0,
        extra_max: int = 200,
    ):
        self._timestamps: deque = deque()
        self._lock = threading.Lock()
        self.short_window = short_window_sec
        self._base_short_max = int(short_max)
        self.short_max = self._base_short_max
        self.long_window = long_window_sec
        self.long_max = long_max
        self.extra_window = extra_window_sec
        self.extra_max = extra_max

    def _purge(self, now: float) -> None:
        oldest_keep = now - self.extra_window
        while self._timestamps and self._timestamps[0] < oldest_keep:
            self._timestamps.popleft()

    def wait_if_needed(self) -> float:
        """Block until all three windows have capacity.

        Returns total seconds spent waiting.  Never waits longer than
        ``THROTTLE_MAX_WAIT``.
        """
        waited = 0.0
        while waited < THROTTLE_MAX_WAIT:
            now = time.monotonic()
            with self._lock:
                self._purge(now)
                short_count = sum(
                    1 for t in self._timestamps if t >= now - self.short_window
                )
                long_count = sum(
                    1 for t in self._timestamps if t >= now - self.long_window
                )
                extra_count = len(self._timestamps)
                if (
                    short_count < self.short_max
                    and long_count < self.long_max
                    and extra_count < self.extra_max
                ):
                    self._timestamps.append(now)
                    return waited
            pause = random.uniform(1.0, 3.0)
            time.sleep(pause)
            waited += pause

        logger.warning(
            "TripleWindowThrottle: max wait (%.0fs) exceeded, proceeding",
            THROTTLE_MAX_WAIT,
        )
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            self._timestamps.append(now)
        return waited

    def tighten_short_window(self, per_worker_n: int) -> None:
        """Adjust short-window burst limit from volume; restores toward constructor baseline."""
        if per_worker_n >= 50:
            # High-volume cap (legacy 2) but never raise above or below baseline inappropriately
            self.short_max = min(self._base_short_max, 2)
        else:
            self.short_max = self._base_short_max


# Backward compatibility: existing imports and tests use this name.
DualWindowThrottle = TripleWindowThrottle


# ---------------------------------------------------------------------------
# Module-level shared instances
# ---------------------------------------------------------------------------
penalty_tracker = PenaltyTracker()
triple_window_throttle = TripleWindowThrottle()
dual_window_throttle = triple_window_throttle


# ---------------------------------------------------------------------------
# MovieSleepManager
# ---------------------------------------------------------------------------


class MovieSleepManager:
    """Human-like movie sleep with two-factor adaptive throttling.

    ``effective_multiplier = volume_factor * penalty_factor``
    capped at ``COMPOSITE_MULTIPLIER_CAP``.  When the raw product exceeds
    the cap, the overflow is converted to a flat additive bonus
    (``PENALTY_OVERFLOW_WEIGHT`` seconds per 1.0 overflow) so that CF
    penalty feedback remains effective even at high volume tiers.

    A 4 % micro-break probability injects occasional long pauses whose
    range is *relative* to the current ``eff_max``, staying above normal
    sleep regardless of volume tier.
    """

    def __init__(
        self,
        sleep_min: float,
        sleep_max: float,
        penalty_tracker: PenaltyTracker | None = None,
        throttle: TripleWindowThrottle | None = None,
    ):
        drift = random.uniform(-0.5, 0.5)
        self.base_min = max(1.0, float(sleep_min) + drift)
        self.base_max = float(sleep_max) + drift
        self.sleep_min = self.base_min
        self.sleep_max = self.base_max

        self._volume_min_mult = 1.0
        self._volume_max_mult = 1.0

        self._penalty_tracker = penalty_tracker
        self._throttle = throttle

        self._lock = threading.Lock()
        self._rng = random.Random()
        self._force_high = False
        self._last_per_worker_n = 0
        self._last_volume_total = 0

    # -- factor setters ----------------------------------------------------

    @property
    def last_volume_total(self) -> int:
        """Total count last passed to :meth:`apply_volume_multiplier` (for rescaling workers)."""
        with self._lock:
            return self._last_volume_total

    def apply_volume_multiplier(self, total: int, num_workers: int = 1, *, quiet: bool = False) -> None:
        """Set volume factor based on per-worker processing volume.

        When *quiet* is ``True`` the per-instance INFO log is suppressed.
        Callers that update many workers in a loop should pass
        ``quiet=True`` and emit a single summary log themselves.
        """
        n = max(1, -(-total // max(1, num_workers)))
        min_mult, max_mult = _interpolate_multiplier(n)

        with self._lock:
            self._volume_min_mult = min_mult
            self._volume_max_mult = max_mult
            self._last_per_worker_n = n
            self._last_volume_total = int(total)
            self._recalc_range()

        if self._throttle and hasattr(self._throttle, 'tighten_short_window'):
            self._throttle.tighten_short_window(n)

        if not quiet and (min_mult > 1.0 or max_mult > 1.0):
            logger.info(
                "Volume-based sleep adjustment: total=%d, workers=%d, "
                "per_worker=%d → volume_factor %.2fx/%.2fx, "
                "sleep range [%.2f, %.2f] (base [%.2f, %.2f])",
                total, num_workers, n, min_mult, max_mult,
                self.sleep_min, self.sleep_max,
                self.base_min, self.base_max,
            )

    def _recalc_range(self) -> None:
        """Recompute effective sleep_min/max from base + static factors.

        The dynamic ``penalty_factor`` is applied at sampling time so that
        it tracks real-time CF events.
        """
        eff_min_mult = min(self._volume_min_mult, COMPOSITE_MULTIPLIER_CAP)
        eff_max_mult = min(self._volume_max_mult, COMPOSITE_MULTIPLIER_CAP)
        self.sleep_min = round(self.base_min * eff_min_mult, 2)
        self.sleep_max = round(self.base_max * eff_max_mult, 2)

    # -- sampling ----------------------------------------------------------

    def _effective_range(self) -> Tuple[float, float]:
        """Return (eff_min, eff_max) after applying the dynamic penalty.

        When ``volume * penalty`` exceeds ``COMPOSITE_MULTIPLIER_CAP``,
        the overflow is converted to an additive bonus so that CF penalty
        feedback is never fully absorbed by the cap.
        """
        pf = self._penalty_tracker.get_penalty_factor() if self._penalty_tracker else 1.0

        with self._lock:
            vol_min = self._volume_min_mult
            vol_max = self._volume_max_mult

        raw_min_mult = vol_min * pf
        raw_max_mult = vol_max * pf

        eff_min_mult = min(raw_min_mult, COMPOSITE_MULTIPLIER_CAP)
        eff_max_mult = min(raw_max_mult, COMPOSITE_MULTIPLIER_CAP)

        overflow_min = max(0.0, raw_min_mult - COMPOSITE_MULTIPLIER_CAP)
        overflow_max = max(0.0, raw_max_mult - COMPOSITE_MULTIPLIER_CAP)

        eff_min = round(self.base_min * eff_min_mult + overflow_min * PENALTY_OVERFLOW_WEIGHT, 2)
        eff_max = round(self.base_max * eff_max_mult + overflow_max * PENALTY_OVERFLOW_WEIGHT, 2)
        return eff_min, eff_max

    def _human_like_delay(self, lo: float, hi: float) -> float:
        """Sample from a truncated log-normal distribution in [lo, hi]."""
        span = hi - lo
        mu = math.log(lo + span * 0.35)
        sigma = 0.4

        for _ in range(50):
            val = self._rng.lognormvariate(mu, sigma)
            if lo <= val <= hi:
                return val

        return self._rng.triangular(lo, hi, lo + span * 0.35)

    def get_sleep_time(self) -> float:
        """Return a human-like sleep duration (seconds)."""
        eff_min, eff_max = self._effective_range()
        span = eff_max - eff_min
        if span <= 0:
            return min(eff_min, ABSOLUTE_MAX_SLEEP)

        roll = self._rng.random()

        # Honor _force_high before micro-break so the flag is always consumed
        # in one call (micro-break would otherwise return early with it still set).
        if self._force_high:
            sleep_time = self._rng.uniform(eff_min + span * 0.7, eff_max)
            self._force_high = False
        elif roll < MICRO_BREAK_PROB:
            break_lo = max(MICRO_BREAK_FLOOR, eff_max + MICRO_BREAK_EXTRA_MIN)
            break_hi = eff_max + MICRO_BREAK_EXTRA_MAX
            return min(round(self._rng.uniform(break_lo, break_hi), 2), ABSOLUTE_MAX_SLEEP)
        elif roll < 0.08 + MICRO_BREAK_PROB:
            sleep_time = self._rng.uniform(eff_min + span * 0.7, eff_max)
        elif roll < 0.15 + MICRO_BREAK_PROB:
            sleep_time = self._rng.uniform(eff_min, eff_min + span * 0.15)
            self._force_high = True
        else:
            sleep_time = self._human_like_delay(eff_min, eff_max)

        jitter = self._rng.uniform(-0.3, 0.3)
        sleep_time = max(eff_min, min(eff_max, sleep_time + jitter))

        return min(round(sleep_time, 2), ABSOLUTE_MAX_SLEEP)

    def get_cooldown(self) -> float:
        """Return an adaptive cooldown duration (seconds).

        Derived from the lower bound of the effective sleep range so that
        cooldowns scale with penalty factor (CF events) and volume.
        Used for CF/fallback/login retry delays instead of fixed config
        values.
        """
        eff_min, _ = self._effective_range()
        return min(round(eff_min * COOLDOWN_FRACTION, 2), COOLDOWN_MAX)

    def sleep(self) -> float:
        """Sleep for a human-like duration, then pass through the throttle.

        Returns the total time spent (sleep + any throttle wait).
        """
        t = self.get_sleep_time()
        pf = self._penalty_tracker.get_penalty_factor() if self._penalty_tracker else 1.0
        logger.debug(
            "Movie sleep: %.2fs (penalty=%.2f, force_high_next=%s)",
            t, pf, self._force_high,
        )
        time.sleep(t)

        throttle_wait = 0.0
        if self._throttle:
            throttle_wait = self._throttle.wait_if_needed()
            if throttle_wait > 0:
                logger.debug("Throttle additional wait: %.1fs", throttle_wait)

        return t + throttle_wait


def _resolve_base_range():
    """Return (min, max) using hardcoded defaults unless config overrides."""
    from packages.python.javdb_spider.runtime.config import MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX
    _min = MOVIE_SLEEP_MIN if MOVIE_SLEEP_MIN is not None else _BASE_MIN
    _max = MOVIE_SLEEP_MAX if MOVIE_SLEEP_MAX is not None else _BASE_MAX
    if MOVIE_SLEEP_MIN is not None or MOVIE_SLEEP_MAX is not None:
        logger.info(
            "Sleep base range overridden via config: [%s, %s]", _min, _max,
        )
    return float(_min), float(_max)


_resolved_min, _resolved_max = _resolve_base_range()

# Module-level singleton
movie_sleep_mgr = MovieSleepManager(
    _resolved_min,
    _resolved_max,
    penalty_tracker=penalty_tracker,
    throttle=triple_window_throttle,
)
