"""Randomised movie sleep with adaptive throttling.

Provides human-like request pacing via three cooperating components:

- ``MovieSleepManager`` — per-worker sleep with log-normal distribution and
  a two-factor multiplier (volume * penalty).
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

import math
import random
import threading
import time
from collections import deque

from packages.python.javdb_platform.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Tuned base sleep range (seconds).  These values are calibrated for the
# adaptive two-factor system and should NOT be tweaked by users.
# Override ONLY via env var ``VAR_MOVIE_SLEEP`` for CI/testing/emergency.
# ---------------------------------------------------------------------------
_BASE_MIN = 8
_BASE_MAX = 25

# ---------------------------------------------------------------------------
# Safety caps
# ---------------------------------------------------------------------------
COMPOSITE_MULTIPLIER_CAP = 6.0
ABSOLUTE_MAX_SLEEP = 120.0  # seconds
COOLDOWN_FRACTION = 0.5     # cooldown = eff_min * this fraction
COOLDOWN_MAX = 30.0         # hard ceiling for cooldown durations

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
        self.short_max = short_max
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
    capped at ``COMPOSITE_MULTIPLIER_CAP`` (currently 6.0).

    The sleep value itself is capped at ``ABSOLUTE_MAX_SLEEP`` (120 s).
    """

    # Tiers for N < 250 only (see ``apply_volume_multiplier`` for N ≥ 250).
    VOLUME_TIERS = [
        (20,  1.00, 1.00),
        (50,  1.15, 1.30),
        (100, 1.35, 1.65),
        (150, 1.60, 2.00),
    ]

    def __init__(
        self,
        sleep_min: float,
        sleep_max: float,
        penalty_tracker: PenaltyTracker = None,
        throttle: TripleWindowThrottle = None,
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

        self._rng = random.Random()
        self._force_high = False

    # -- factor setters ----------------------------------------------------

    def apply_volume_multiplier(self, n: int) -> None:
        """Set volume factor based on estimated processing volume *n*."""
        if n >= 500:
            min_mult, max_mult = 4.0, 5.5
        elif n >= 350:
            min_mult, max_mult = 3.0, 4.0
        elif n >= 250:
            min_mult, max_mult = 2.2, 2.9
        else:
            min_mult, max_mult = 1.0, 1.0
            for threshold, m_lo, m_hi in self.VOLUME_TIERS:
                if n < threshold:
                    break
                min_mult, max_mult = m_lo, m_hi

        self._volume_min_mult = min_mult
        self._volume_max_mult = max_mult
        self._recalc_range()

        if min_mult > 1.0 or max_mult > 1.0:
            logger.info(
                "Volume-based sleep adjustment: N=%d → volume_factor %.2fx/%.2fx",
                n, min_mult, max_mult,
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

    def _effective_range(self):
        """Return (eff_min, eff_max) after applying the dynamic penalty."""
        pf = self._penalty_tracker.get_penalty_factor() if self._penalty_tracker else 1.0

        eff_min_mult = min(
            self._volume_min_mult * pf,
            COMPOSITE_MULTIPLIER_CAP,
        )
        eff_max_mult = min(
            self._volume_max_mult * pf,
            COMPOSITE_MULTIPLIER_CAP,
        )
        return (
            round(self.base_min * eff_min_mult, 2),
            round(self.base_max * eff_max_mult, 2),
        )

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

        if self._force_high:
            sleep_time = self._rng.uniform(eff_min + span * 0.7, eff_max)
            self._force_high = False
        elif roll < 0.08:
            sleep_time = self._rng.uniform(eff_min + span * 0.7, eff_max)
        elif roll < 0.15:
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
