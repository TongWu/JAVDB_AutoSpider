"""Tests for ``ProxyCoordinatorClient.report_async`` thread-pool semantics.

These cover the regression where a fresh daemon thread was spawned per
event during turnstile storms, risking thread / memory exhaustion.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from packages.python.javdb_platform.proxy_coordinator_client import (  # noqa: E402
    CoordinatorUnavailable,
    ProxyCoordinatorClient,
    _ASYNC_QUEUE_SENTINEL,
)


def _make_client(*, async_workers: int = 2, async_queue_size: int = 8) -> ProxyCoordinatorClient:
    """Build a client without hitting the network in __init__."""
    return ProxyCoordinatorClient(
        base_url="https://coord.example.test",
        token="dummy",
        async_workers=async_workers,
        async_queue_size=async_queue_size,
    )


# ── Worker pool lifecycle ────────────────────────────────────────────────


def test_init_starts_fixed_worker_pool():
    """__init__ should start exactly ``async_workers`` daemon threads."""
    pre = {t.name for t in threading.enumerate()}
    c = _make_client(async_workers=3)
    try:
        new_workers = [
            t for t in threading.enumerate()
            if t.name.startswith("coord-report-") and t.name not in pre
        ]
        assert len(new_workers) == 3
        for t in new_workers:
            assert t.daemon is True
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_async_does_not_spawn_per_call_threads():
    """Many report_async calls must NOT inflate the worker count.

    Regression: the old code created a fresh daemon thread per event,
    so under turnstile storms the process accumulated unbounded threads.
    """
    c = _make_client(async_workers=2, async_queue_size=64)
    try:
        # Stub out the synchronous ``report`` so workers drain the queue
        # quickly and we don't depend on the network.
        with patch.object(c, "report"):
            pre_threads = threading.active_count()
            for i in range(200):
                c.report_async(f"proxy-{i}", "cf")
            # Allow workers to drain the queue.
            c._async_queue.join()
            post_threads = threading.active_count()

        # At most the 2 worker threads were already counted in pre_threads;
        # nothing else should appear. A small slack covers GC / stdlib
        # housekeeping threads.
        assert post_threads - pre_threads <= 1, (
            f"thread count grew from {pre_threads} to {post_threads} after "
            f"200 report_async calls — should stay bounded by the worker pool"
        )
    finally:
        c.close(wait=True, timeout=2.0)


def test_close_is_idempotent_and_joins_workers():
    c = _make_client(async_workers=2)
    c.close(wait=True, timeout=2.0)
    c.close(wait=True, timeout=2.0)  # second call is a no-op
    for t in c._async_workers:
        assert not t.is_alive()


# ── Queue dispatch ───────────────────────────────────────────────────────


def test_report_async_dispatches_to_worker():
    c = _make_client()
    received: list = []
    done = threading.Event()

    def fake_report(proxy_id, kind="cf"):
        received.append((proxy_id, kind))
        done.set()

    with patch.object(c, "report", side_effect=fake_report):
        try:
            c.report_async("proxy-A", "cf")
            assert done.wait(timeout=2.0), "worker never picked up the event"
        finally:
            c.close(wait=True, timeout=2.0)

    assert received == [("proxy-A", "cf")]


def test_worker_swallows_coordinator_errors():
    """A failing report() must NOT terminate the worker."""
    c = _make_client(async_workers=1)
    call_count = threading.Semaphore(0)

    def flaky_report(proxy_id, kind="cf"):
        call_count.release()
        raise CoordinatorUnavailable("simulated outage")

    with patch.object(c, "report", side_effect=flaky_report):
        try:
            c.report_async("p1", "cf")
            c.report_async("p2", "failure")
            assert call_count.acquire(timeout=2.0)
            assert call_count.acquire(timeout=2.0)
            # Both events processed, worker still alive.
            assert any(t.is_alive() for t in c._async_workers)
        finally:
            c.close(wait=True, timeout=2.0)


# ── Backpressure ─────────────────────────────────────────────────────────


def test_full_queue_drops_with_warning(caplog):
    """When the queue is full, new events are dropped with a WARNING."""
    c = _make_client(async_workers=1, async_queue_size=2)
    block = threading.Event()
    started = threading.Event()

    def slow_report(proxy_id, kind="cf"):
        # Block the only worker so the queue can fill up deterministically.
        started.set()
        block.wait(timeout=2.0)

    caplog.set_level("WARNING")
    with patch.object(c, "report", side_effect=slow_report):
        try:
            # 1) Worker grabs first event and blocks inside report().
            c.report_async("p0", "cf")
            assert started.wait(timeout=2.0)
            # 2) Fill the queue (size 2) with two more events.
            c.report_async("p1", "cf")
            c.report_async("p2", "cf")
            # 3) Subsequent submits must NOT raise but must drop and warn.
            for i in range(3, 53):
                c.report_async(f"p{i}", "cf")
        finally:
            block.set()
            c.close(wait=True, timeout=2.0)

    drop_warnings = [r for r in caplog.records if "queue full" in r.getMessage()]
    assert len(drop_warnings) >= 1, "expected at least one queue-full warning"
    # Throttle: emit at first drop and every 50th, so ~50 drops → 2 lines.
    assert len(drop_warnings) <= 5
    assert c._async_dropped >= 50


def test_report_async_after_close_is_noop():
    """Calling report_async on a closed client must not raise or enqueue."""
    c = _make_client()
    c.close(wait=True, timeout=2.0)
    # Should not raise, should not block forever.
    c.report_async("p1", "cf")
    # And it should not have been enqueued.
    assert c._async_queue.empty()


# ── Sentinel handling ────────────────────────────────────────────────────


def test_sentinel_terminates_worker_promptly():
    c = _make_client(async_workers=1)
    worker = c._async_workers[0]
    c.close()
    worker.join(timeout=2.0)
    assert not worker.is_alive()


def test_sentinel_constant_is_a_pair_of_nones():
    """Worker termination check relies on the sentinel shape."""
    assert _ASYNC_QUEUE_SENTINEL == (None, None)
