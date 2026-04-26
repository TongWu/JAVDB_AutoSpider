"""Tests for MovieSleepManager + PenaltyTracker integration with the
Cloudflare DO proxy coordinator.

Covers:

- ``MovieSleepManager.sleep()`` honours the DO-returned ``wait_ms``.
- ``MovieSleepManager.sleep()`` falls back to the local
  ``time.sleep(t) + throttle.wait_if_needed()`` path when the
  coordinator raises (fail-open) and logs an ERROR.
- ``PenaltyTracker.set_remote_factor`` raises the active factor only
  while the TTL is fresh; expires back to local-only thereafter.
- ``PenaltyTracker.record_event`` triggers ``coordinator.report_async``
  when wired in, and is a no-op for the coordinator otherwise.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.proxy_coordinator_client import (
    CoordinatorUnavailable,
    LeaseResult,
    ReportResult,
)
from packages.python.javdb_spider.runtime.sleep import (
    MovieSleepManager,
    PenaltyTracker,
    TripleWindowThrottle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_lease(wait_ms: int, penalty: float = 1.0, reason: str = "ok") -> LeaseResult:
    return LeaseResult(
        wait_ms=wait_ms,
        penalty_factor=penalty,
        server_time_ms=int(time.time() * 1000),
        reason=reason,
    )


# ---------------------------------------------------------------------------
# MovieSleepManager.sleep() — coordinator path
# ---------------------------------------------------------------------------


class TestSleepWithCoordinator:

    def test_lease_wait_ms_is_honoured(self):
        """When the coordinator returns wait_ms, sleep() must honour it
        instead of using the locally-sampled value."""
        coord = MagicMock()
        coord.lease.return_value = _mk_lease(wait_ms=200)  # 0.2s

        mgr = MovieSleepManager(
            sleep_min=10.0, sleep_max=20.0,  # Local would normally pick 10–20s
            coordinator=coord,
            proxy_id="proxy-X",
        )
        start = time.monotonic()
        elapsed = mgr.sleep()
        actual_wait = time.monotonic() - start

        coord.lease.assert_called_once()
        args, kwargs = coord.lease.call_args
        assert args[0] == "proxy-X"
        assert isinstance(args[1], int)  # intended_sleep_ms
        # Slept ~0.2s, NOT 10s+.
        assert actual_wait < 1.0, f"slept too long: {actual_wait}s"
        assert elapsed == pytest.approx(0.2, abs=0.05)

    def test_intended_sleep_ms_is_int_milliseconds(self):
        """The lease call must receive int milliseconds (DO expects int)."""
        coord = MagicMock()
        coord.lease.return_value = _mk_lease(wait_ms=10)
        mgr = MovieSleepManager(2.0, 3.0, coordinator=coord, proxy_id="p")
        mgr.sleep()
        args, _ = coord.lease.call_args
        intended = args[1]
        assert isinstance(intended, int)
        assert intended >= 0

    def test_remote_penalty_factor_is_propagated_to_tracker(self):
        """LeaseResult.penalty_factor must be cached on PenaltyTracker so
        get_penalty_factor() returns max(local, remote)."""
        tracker = PenaltyTracker()
        coord = MagicMock()
        coord.lease.return_value = _mk_lease(wait_ms=10, penalty=1.65)
        mgr = MovieSleepManager(
            2.0, 3.0,
            penalty_tracker=tracker,
            coordinator=coord,
            proxy_id="p",
            remote_factor_ttl_sec=10.0,
        )

        assert tracker.get_penalty_factor() == 1.0

        mgr.sleep()

        # Remote factor is now cached and overrides the local 1.0.
        assert tracker.get_penalty_factor() == 1.65

    def test_proxy_id_falls_back_to_proxy_label(self):
        """If proxy_id is not given but proxy_label is, lease should be
        called with the label so DO addressing matches log output."""
        coord = MagicMock()
        coord.lease.return_value = _mk_lease(wait_ms=10)
        mgr = MovieSleepManager(
            2.0, 3.0,
            coordinator=coord,
            proxy_label="JP-1",
        )
        mgr.sleep()
        args, _ = coord.lease.call_args
        assert args[0] == "JP-1"

    def test_no_proxy_id_disables_coordinator_path(self):
        """No proxy_id and no proxy_label → never call coordinator.lease."""
        coord = MagicMock()
        mgr = MovieSleepManager(0.01, 0.02, coordinator=coord)
        mgr.sleep()
        coord.lease.assert_not_called()


# ---------------------------------------------------------------------------
# MovieSleepManager.sleep() — fail-open
# ---------------------------------------------------------------------------


class TestSleepFailOpen:

    def test_coordinator_unavailable_falls_back_to_local(self, caplog):
        """A CoordinatorUnavailable from lease must trigger:
        - the original local time.sleep(t) path
        - throttle.wait_if_needed() on the configured throttle
        - an ERROR log message
        """
        coord = MagicMock()
        coord.lease.side_effect = CoordinatorUnavailable("simulated outage")

        # Use a throttle with very loose limits so wait_if_needed returns 0.
        throttle = TripleWindowThrottle(
            short_window_sec=1.0, short_max=99,
            long_window_sec=1.0, long_max=99,
            extra_window_sec=1.0, extra_max=99,
        )
        mgr = MovieSleepManager(
            0.01, 0.02,
            throttle=throttle,
            coordinator=coord,
            proxy_id="p",
        )

        with caplog.at_level("ERROR", logger="packages.python.javdb_spider.runtime.sleep"):
            elapsed = mgr.sleep()

        assert elapsed > 0
        assert any("Coordinator unavailable" in r.message for r in caplog.records), (
            f"no fail-open ERROR log; got: {[r.message for r in caplog.records]}"
        )
        coord.lease.assert_called_once()

    def test_repeated_failures_only_log_first_three(self, caplog):
        """Long outages should not spam ERROR logs forever — only the
        first 3 failures emit ERROR; subsequent are silent."""
        coord = MagicMock()
        coord.lease.side_effect = CoordinatorUnavailable("down")
        mgr = MovieSleepManager(
            0.01, 0.02,
            throttle=None,
            coordinator=coord,
            proxy_id="p",
        )

        with caplog.at_level("ERROR", logger="packages.python.javdb_spider.runtime.sleep"):
            for _ in range(5):
                mgr.sleep()

        error_lines = [r for r in caplog.records if "Coordinator unavailable" in r.message]
        assert len(error_lines) == 3, f"expected 3 ERROR lines, got {len(error_lines)}"

    def test_recovery_resets_failure_counter(self, caplog):
        """After a successful lease, the failure counter resets so the
        next outage is again logged at ERROR (up to 3 more times)."""
        coord = MagicMock()
        coord.lease.side_effect = [
            CoordinatorUnavailable("blip 1"),
            _mk_lease(wait_ms=5),  # recovery
            CoordinatorUnavailable("blip 2"),
        ]
        mgr = MovieSleepManager(
            0.01, 0.02,
            throttle=None,
            coordinator=coord,
            proxy_id="p",
        )

        with caplog.at_level("ERROR", logger="packages.python.javdb_spider.runtime.sleep"):
            mgr.sleep()
            mgr.sleep()
            mgr.sleep()

        error_lines = [r for r in caplog.records if "Coordinator unavailable" in r.message]
        assert len(error_lines) == 2

    def test_other_exceptions_also_fail_open(self):
        """Unexpected exception types (network library bugs, JSON parse,
        etc.) should ALSO fall back rather than abort — no panic in hot path."""
        coord = MagicMock()
        coord.lease.side_effect = RuntimeError("totally unexpected")
        mgr = MovieSleepManager(0.01, 0.02, coordinator=coord, proxy_id="p")
        elapsed = mgr.sleep()
        assert elapsed > 0  # local sleep happened


# ---------------------------------------------------------------------------
# PenaltyTracker.set_remote_factor
# ---------------------------------------------------------------------------


class TestPenaltyTrackerRemoteFactor:

    def test_no_remote_factor_returns_local(self):
        pt = PenaltyTracker()
        assert pt.get_penalty_factor() == 1.0
        pt.record_event()
        assert pt.get_penalty_factor() == 1.30

    def test_remote_factor_overrides_local_when_higher(self):
        pt = PenaltyTracker()
        pt.set_remote_factor(2.0, ttl_sec=10)
        assert pt.get_penalty_factor() == 2.0

    def test_remote_factor_does_not_lower_local(self):
        pt = PenaltyTracker()
        for _ in range(4):
            pt.record_event()
        assert pt.get_penalty_factor() == 2.0  # local already 2.0

        pt.set_remote_factor(1.30, ttl_sec=10)
        # max(local=2.0, remote=1.30) = 2.0
        assert pt.get_penalty_factor() == 2.0

    def test_remote_factor_clamped_to_min_1(self):
        pt = PenaltyTracker()
        pt.set_remote_factor(0.5, ttl_sec=10)
        assert pt.get_penalty_factor() == 1.0

    def test_remote_factor_invalid_input_ignored(self):
        pt = PenaltyTracker()
        pt.set_remote_factor("not a float", ttl_sec=10)  # type: ignore[arg-type]
        pt.set_remote_factor(None, ttl_sec=10)  # type: ignore[arg-type]
        assert pt.get_penalty_factor() == 1.0

    def test_remote_factor_expires_after_ttl(self):
        pt = PenaltyTracker()
        pt.set_remote_factor(1.65, ttl_sec=0.05)
        assert pt.get_penalty_factor() == 1.65
        time.sleep(0.1)
        # Remote stale → falls back to local-only (which is 1.0).
        assert pt.get_penalty_factor() == 1.0

    def test_record_event_triggers_async_report(self):
        coord = MagicMock()
        # Make report_async actually wait so we can join.
        ready = threading.Event()
        coord.report_async.side_effect = lambda *a, **kw: ready.set()
        pt = PenaltyTracker(coordinator=coord, proxy_id="proxy-X")

        pt.record_event()
        assert ready.wait(timeout=2.0)
        coord.report_async.assert_called_once_with("proxy-X", "cf")

    def test_record_event_without_coordinator_is_silent(self):
        pt = PenaltyTracker()
        pt.record_event()  # must not raise
