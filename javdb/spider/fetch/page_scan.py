"""Dynamic page-scan policy for the daily index (ADR-057).

The daily index scan used to stop at a configured ``PAGE_END`` whether the day's
new torrents ended on page 3 or still filled page 10. This policy keeps that
range as a *floor* and decides, page by page, whether the scan should reach past
it — while the pages still carry the site's today/yesterday badges.

Both fetch paths (sequential loop and parallel sliding window) feed the same
policy one observation per page and ask it whether to continue, so the rule
lives in exactly one place. The policy is pure state: no I/O, no logging, and no
knowledge of the phase gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Stop reasons, in the order they are checked. The cap is checked last so that a
# scan whose fresh-free budget ran out *on* the ceiling reports why it really
# stopped rather than warning about a truncation that did not happen.
STOP_END_OF_CONTENT = 'end-of-content'   # the site said there are no more pages
STOP_FLOOR = 'floor'                     # dynamic scan off, or no room to extend
STOP_EXHAUSTED = 'exhausted'             # K consecutive pages with no fresh entries
STOP_UNREADABLE = 'unreadable'           # K consecutive pages we could not read
STOP_CAP = 'cap'                         # hard page ceiling — possible truncation
# Set by the caller, not by the page-by-page rule: the fetch layer ran out of
# usable proxies, so the scan was cut short by infrastructure rather than by
# anything the pages said.
STOP_PROXIES_EXHAUSTED = 'proxies-exhausted'

# The reasons the policy derives from the pages themselves. They describe the
# dynamic rule's own decision, so they mean nothing in a mode where the rule
# does not run (``--all``). Anything outside this set was forced by the caller
# and still happened — reporting must keep it whatever the mode.
POLICY_DERIVED_STOP_REASONS = frozenset({
    STOP_END_OF_CONTENT, STOP_FLOOR, STOP_EXHAUSTED, STOP_UNREADABLE, STOP_CAP,
})


def effective_stop_after(value: int) -> int:
    """The usable K for *value*, which has to be at least 1.

    Both run counters start at 0, so a configured 0 (or a negative) is already
    ``>= stop_after`` the moment the scan reaches the floor: it would stop on the
    first page past ``PAGE_END`` even if that page were full of new torrents, and
    report a clean ``exhausted`` for it. That is precisely the silent truncation
    this ADR exists to remove, arriving through the config rather than the page
    limit, so the value is floored instead of honoured.
    """
    return max(1, value)


@dataclass
class PageScanPolicy:
    """Decides how far past ``floor_page`` the index scan should reach.

    Args:
        floor_page: last page of the configured range (``PAGE_END``). Always
            scanned; the policy only ever adds pages beyond it.
        max_page: hard ceiling (``PAGE_SCAN_MAX``). Reaching it means the fresh
            block may have been truncated, so ``hit_cap`` is exposed for the
            caller to warn about.
        stop_after: how many *consecutive* fresh-free pages end the scan (K).
            Floored at 1 by :func:`effective_stop_after`; ``stop_after_raw``
            keeps what was configured so the caller can warn about it.
        enabled: when False the policy stops at ``floor_page``, reproducing the
            fixed-range behaviour exactly.
    """

    floor_page: int
    max_page: int
    stop_after: int
    enabled: bool

    stop_after_raw: int = field(default=0, init=False)
    _fresh_free_run: int = field(default=0, init=False)
    _unknown_run: int = field(default=0, init=False)
    _stop_reason: Optional[str] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.stop_after_raw = self.stop_after
        self.stop_after = effective_stop_after(self.stop_after)

    def observe(
        self,
        page_num: int,
        *,
        fresh: Optional[int],
        end_of_content: bool = False,
    ) -> None:
        """Record what page *page_num* turned out to contain.

        ``fresh`` is the page's raw today/yesterday badge count, or ``None``
        when the page could not be fetched or parsed. An unknown page neither
        advances nor resets the fresh-free run: reading a transient proxy ban as
        "no fresh entries here" would silently truncate the day's ingestion.

        Unknown pages are counted separately, though. A page we could not read
        is no evidence that the fresh block continues either, so K consecutive
        unreadable pages end the extension — otherwise a proxy outage would
        march the scan all the way to the cap fetching nothing.
        """
        if end_of_content:
            self._stop_reason = STOP_END_OF_CONTENT
            return
        if fresh is None:
            self._unknown_run += 1
            return
        self._unknown_run = 0
        if fresh > 0:
            self._fresh_free_run = 0
        else:
            self._fresh_free_run += 1

    def should_continue_after(self, page_num: int) -> bool:
        """Whether the scan should fetch the page after *page_num*."""
        if self._stop_reason == STOP_END_OF_CONTENT:
            return False

        # The configured range is a floor: nothing below it can end the scan,
        # not even a misconfigured cap.
        if page_num < self.floor_page:
            return True

        if not self.enabled:
            self._stop_reason = STOP_FLOOR
            return False

        # The cap only ever limits the *extension*. A cap configured at or below
        # the floor leaves no extension to limit, so the scan simply ran out of
        # configured range — the fixed-range behaviour, not a truncation.
        effective_max = max(self.max_page, self.floor_page)
        if page_num >= effective_max and effective_max == self.floor_page:
            self._stop_reason = STOP_FLOOR
            return False

        # A stop condition that is already satisfied outranks the ceiling.
        # Landing on the last allowed page with the fresh-free budget spent is a
        # scan that ended on its own terms; reporting `cap` there would warn
        # about a truncation that did not happen.
        if self._fresh_free_run >= self.stop_after:
            self._stop_reason = STOP_EXHAUSTED
            return False

        if self._unknown_run >= self.stop_after:
            self._stop_reason = STOP_UNREADABLE
            return False

        if page_num >= effective_max:
            self._stop_reason = STOP_CAP
            return False

        return True

    def force_stop(self, reason: str) -> None:
        """End the scan for a reason the page observations cannot express.

        The fetch layer uses this when it cannot go on regardless of what the
        pages contain — a dead proxy pool, say. Without it such a run reports no
        stop reason at all and reads as a clean scan that simply found nothing
        more, which is the truncation D9 exists to make visible. An already
        recorded reason wins: whatever stopped the scan first is what stopped it.
        """
        if self._stop_reason is None:
            self._stop_reason = reason

    @property
    def stop_reason(self) -> Optional[str]:
        """Why the scan stopped, or ``None`` while it is still running."""
        return self._stop_reason

    @property
    def hit_cap(self) -> bool:
        """True when the hard ceiling ended the scan — the fresh block may be cut."""
        return self._stop_reason == STOP_CAP
