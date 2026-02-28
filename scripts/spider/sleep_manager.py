"""Randomised movie sleep with adaptive throttling."""

import random
import time

from utils.logging_config import get_logger
from scripts.spider.config_loader import MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX

logger = get_logger(__name__)


class MovieSleepManager:
    """Randomised movie sleep with adaptive throttling.

    Picks a random sleep time in ``[sleep_min, sleep_max]``.  When the chosen
    value falls in the bottom 10 % of the range the *next* call is forced to
    pick from the top 30 % to avoid consecutive short intervals.
    """

    # (threshold, min_multiplier, max_multiplier)
    # 5， 15
    VOLUME_TIERS = [
        (50,  1.0, 1.0),
        (75,  1.5, 2.5),
        (100, 2.0, 4.0),
        (125, 3.0, 5.0),
        (150, 4.0, 6.0),
    ]
    VOLUME_MAX_MULTIPLIER = (5.0, 5.0)

    def __init__(self, sleep_min: float, sleep_max: float):
        self.base_min = float(sleep_min)
        self.base_max = float(sleep_max)
        self.sleep_min = self.base_min
        self.sleep_max = self.base_max
        self._force_high = False

    def apply_volume_multiplier(self, n: int) -> None:
        """Scale sleep range based on estimated processing volume *n*."""
        min_mult, max_mult = 1.0, 1.0
        for threshold, m_lo, m_hi in self.VOLUME_TIERS:
            if n < threshold:
                break
            min_mult, max_mult = m_lo, m_hi
        else:
            if n >= self.VOLUME_TIERS[-1][0]:
                min_mult, max_mult = self.VOLUME_MAX_MULTIPLIER

        self.sleep_min = round(self.base_min * min_mult, 1)
        self.sleep_max = round(self.base_max * max_mult, 1)
        if min_mult > 1.0 or max_mult > 1.0:
            logger.info(
                "Volume-based sleep adjustment: N=%d → sleep range [%.1f, %.1f] "
                "(base [%.1f, %.1f], multipliers %.1fx/%.1fx)",
                n, self.sleep_min, self.sleep_max,
                self.base_min, self.base_max, min_mult, max_mult,
            )

    def get_sleep_time(self) -> float:
        span = self.sleep_max - self.sleep_min
        if span <= 0:
            return self.sleep_min

        if self._force_high:
            low = self.sleep_min + span * 0.7
            sleep_time = random.uniform(low, self.sleep_max)
            self._force_high = False
        else:
            sleep_time = random.uniform(self.sleep_min, self.sleep_max)

        if sleep_time <= self.sleep_min + span * 0.1:
            self._force_high = True

        return round(sleep_time, 1)

    def sleep(self) -> float:
        """Sleep for a random duration and return the chosen time."""
        t = self.get_sleep_time()
        logger.debug("Movie sleep: %.1fs (force_high_next=%s)", t, self._force_high)
        time.sleep(t)
        return t


# Module-level singleton
movie_sleep_mgr = MovieSleepManager(MOVIE_SLEEP_MIN, MOVIE_SLEEP_MAX)
