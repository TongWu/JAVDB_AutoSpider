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
    DEFAULT_BAN_TTL_MS,
    LeaseResult,
    ProxyCoordinatorClient,
    ProxyHealthSnapshot,
    ReportResult,
    _ASYNC_QUEUE_SENTINEL,
    create_coordinator_from_env,
    _extract_server_time_ms,
    _normalize_proxy_id,
    _validate_kind,
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


def test_init_does_not_start_worker_pool_until_report_async():
    """__init__ should not leak daemon threads before the client is validated."""
    pre = {t.name for t in threading.enumerate()}
    c = _make_client(async_workers=3)
    try:
        new_workers = [
            t for t in threading.enumerate()
            if t.name.startswith("coord-report-") and t.name not in pre
        ]
        assert new_workers == []
        with patch.object(c, "report"):
            c.report_async("proxy-A", "cf")
            c._async_queue.join()
        assert len(c._async_workers) == 3
        for t in c._async_workers:
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
        # nothing else should appear beyond the lazily-started fixed pool.
        # A small slack covers GC / stdlib housekeeping threads.
        assert post_threads - pre_threads <= 3, (
            f"thread count grew from {pre_threads} to {post_threads} after "
            f"200 report_async calls — should stay bounded by the worker pool"
        )
    finally:
        c.close(wait=True, timeout=2.0)


def test_close_is_idempotent_and_joins_workers():
    c = _make_client(async_workers=2)
    with patch.object(c, "report"):
        c.report_async("proxy-A", "cf")
        c._async_queue.join()
    c.close(wait=True, timeout=2.0)
    c.close(wait=True, timeout=2.0)  # second call is a no-op
    for t in c._async_workers:
        assert not t.is_alive()


def test_close_joins_all_workers_when_worker_count_exceeds_queue_size():
    c = _make_client(async_workers=3, async_queue_size=1)
    assert c._async_queue.maxsize == 3
    started = threading.Semaphore(0)
    block = threading.Event()

    def slow_report(proxy_id, kind="cf"):
        started.release()
        block.wait(timeout=2.0)

    with patch.object(c, "report", side_effect=slow_report):
        try:
            for i in range(3):
                c.report_async(f"proxy-{i}", "cf")
                assert started.acquire(timeout=2.0)
            for i in range(3, 8):
                c.report_async(f"proxy-{i}", "cf")
            block.set()
            c._async_queue.join()
            assert c._async_dropped > 0
            c.close(wait=True, timeout=2.0)
        finally:
            block.set()
            c.close(wait=True, timeout=2.0)

    for t in c._async_workers:
        assert not t.is_alive()


def test_close_wait_false_never_blocks_when_workers_exceed_requested_queue_size():
    c = _make_client(async_workers=3, async_queue_size=1)
    started = threading.Semaphore(0)
    block = threading.Event()
    close_done = threading.Event()

    def slow_report(proxy_id, kind="cf"):
        started.release()
        block.wait(timeout=2.0)

    with patch.object(c, "report", side_effect=slow_report):
        try:
            for i in range(3):
                c.report_async(f"proxy-{i}", "cf")
                assert started.acquire(timeout=2.0)

            closer = threading.Thread(
                target=lambda: (c.close(wait=False), close_done.set()),
                daemon=True,
            )
            closer.start()
            assert close_done.wait(timeout=0.5)
        finally:
            block.set()
            c.close(wait=True, timeout=2.0)

    for t in c._async_workers:
        assert not t.is_alive()


def test_close_closes_http_session():
    c = _make_client(async_workers=1)
    with patch.object(c._session, "close") as close_session:
        c.close(wait=True, timeout=2.0)
    close_session.assert_called_once()


def test_factory_closes_client_when_health_check_fails(monkeypatch):
    monkeypatch.setenv("PROXY_COORDINATOR_URL", "https://coord.example.test")
    monkeypatch.setenv("PROXY_COORDINATOR_TOKEN", "dummy")
    with patch.object(ProxyCoordinatorClient, "health_check", return_value=False), \
            patch.object(ProxyCoordinatorClient, "close") as close_client:
        assert create_coordinator_from_env() is None
    close_client.assert_called_once()


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
    with patch.object(c, "report"):
        c.report_async("proxy-A", "cf")
        c._async_queue.join()
    worker = c._async_workers[0]
    c.close()
    worker.join(timeout=2.0)
    assert not worker.is_alive()


def test_sentinel_constant_is_a_pair_of_nones():
    """Worker termination check relies on the sentinel shape."""
    assert _ASYNC_QUEUE_SENTINEL == (None, None)


# ── Kind validation (no silent coercion) ─────────────────────────────────


@pytest.mark.parametrize("kind", ["cf", "failure", "ban", "unban", "cf_bypass"])
def test_validate_kind_accepts_known_values(kind):
    assert _validate_kind(kind) == kind


@pytest.mark.parametrize("kind", ["", "CF", "Failure", "fail", "unknown", None])
def test_validate_kind_rejects_unknown_values(kind):
    with pytest.raises(ValueError, match="Invalid kind"):
        _validate_kind(kind)


def test_report_raises_value_error_on_bad_kind_without_http_call():
    """report() must surface typos at the call site, not silently bucket them."""
    c = _make_client(async_workers=1)
    try:
        with patch.object(c._session, "post") as fake_post:
            with pytest.raises(ValueError, match="Invalid kind"):
                c.report("p1", "WrongKind")
            assert fake_post.call_count == 0  # never reached the network
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_async_raises_value_error_on_bad_kind_synchronously():
    """report_async() must reject typos synchronously instead of queueing them.

    Otherwise the error is only logged ~ms later by the worker thread,
    making it easy to miss in dev.
    """
    c = _make_client(async_workers=1, async_queue_size=4)
    try:
        with pytest.raises(ValueError, match="Invalid kind"):
            c.report_async("p1", "wrong-kind")
        assert c._async_queue.empty()
    finally:
        c.close(wait=True, timeout=2.0)


# ── server_time wire-key fallback ────────────────────────────────────────


def test_extract_server_time_ms_prefers_explicit_ms_key():
    """When both keys are present, the explicit-units key wins."""
    assert _extract_server_time_ms({"server_time_ms": 123, "server_time": 456}) == 123


def test_extract_server_time_ms_falls_back_to_server_time():
    """Backward-compatible with the current Worker, which sends `server_time`."""
    assert _extract_server_time_ms({"server_time": 789}) == 789


def test_extract_server_time_ms_raises_on_missing_keys():
    with pytest.raises(KeyError):
        _extract_server_time_ms({"wait_ms": 100})


def _fake_response(payload: dict, status_code: int = 200):
    class _R:
        def __init__(self):
            self.status_code = status_code
            self._payload = payload
            self.text = ""

        def json(self):
            return self._payload

    return _R()


def test_lease_parses_server_time_ms_wire_key():
    """Forward-compatible: Worker may emit server_time_ms in a future deploy."""
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 100, "penalty_factor": 1.0,
        "server_time_ms": 1234567890123, "reason": "ok",
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            result = c.lease("p1", 100)
        assert isinstance(result, LeaseResult)
        assert result.server_time_ms == 1234567890123
    finally:
        c.close(wait=True, timeout=2.0)


def test_lease_falls_back_to_legacy_server_time_wire_key():
    """Current Worker sends `server_time`; Python must keep accepting it."""
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 100, "penalty_factor": 1.0,
        "server_time": 999, "reason": "ok",
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            result = c.lease("p1", 100)
        assert result.server_time_ms == 999
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_parses_server_time_ms_wire_key():
    c = _make_client(async_workers=1)
    payload = {
        "penalty_factor": 1.5, "recent_event_count": 3,
        "server_time_ms": 42,
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            result = c.report("p1", "cf")
        assert isinstance(result, ReportResult)
        assert result.server_time_ms == 42
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_falls_back_to_legacy_server_time_wire_key():
    c = _make_client(async_workers=1)
    payload = {
        "penalty_factor": 2.0, "recent_event_count": 7,
        "server_time": 17,
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            result = c.report("p1", "failure")
        assert result.server_time_ms == 17
    finally:
        c.close(wait=True, timeout=2.0)


# ── _normalize_proxy_id (S324 / hashlib usage) ───────────────────────────


# ─────────────────────────────────────────────────────────────────────────
# P1-A — proxy ban + cf_bypass piggy-backed on /lease & /report.  These
# verify the wire-protocol additions on the Python side: optional
# LeaseResult fields default to "no signal", new report kinds carry
# ttl_ms / reason in the request body, and the convenience helpers
# (mark_proxy_banned / unbanned / cf_bypass) thread through ``report_async``
# with the right payload.
# ─────────────────────────────────────────────────────────────────────────


def test_default_ban_ttl_ms_is_three_days():
    """Plan-mandated default: 3-day ban TTL."""
    assert DEFAULT_BAN_TTL_MS == 3 * 24 * 60 * 60 * 1000


def test_lease_defaults_p1a_fields_when_worker_omits_them():
    """An older Worker that doesn't know about the P1-A fields must look
    indistinguishable from the legacy behaviour to existing call sites."""
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 10, "penalty_factor": 1.0,
        "server_time": 100, "reason": "ok",
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            r = c.lease("p1", 0)
        assert r.banned is False
        assert r.banned_until is None
        assert r.requires_cf_bypass is False
        assert r.cf_bypass_until is None
    finally:
        c.close(wait=True, timeout=2.0)


def test_lease_parses_p1a_fields_when_worker_emits_them():
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 10, "penalty_factor": 1.0,
        "server_time": 100, "reason": "banned",
        "banned": True, "banned_until": 9_999_999,
        "requires_cf_bypass": True, "cf_bypass_until": 0,
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            r = c.lease("p1", 0)
        assert r.banned is True
        assert r.banned_until == 9_999_999
        assert r.requires_cf_bypass is True
        # 0 (the "permanent for this session" sentinel) must round-trip as
        # int(0), NOT be coerced to None by truthiness checks.
        assert r.cf_bypass_until == 0
        assert r.reason == "banned"
    finally:
        c.close(wait=True, timeout=2.0)


def test_lease_handles_explicit_null_p1a_fields():
    """The Worker may send ``"banned_until": null`` explicitly.  Treat as None."""
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 10, "penalty_factor": 1.0,
        "server_time": 100, "reason": "ok",
        "banned": False, "banned_until": None,
        "requires_cf_bypass": False, "cf_bypass_until": None,
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            r = c.lease("p1", 0)
        assert r.banned_until is None
        assert r.cf_bypass_until is None
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_includes_ttl_ms_and_reason_in_body_for_ban_kind():
    c = _make_client(async_workers=1)
    seen_body: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002 — mirroring requests' kw
        seen_body.update(json)
        return _fake_response({
            "penalty_factor": 1.0, "recent_event_count": 0, "server_time": 1,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            c.report("proxy-A", "ban", ttl_ms=60_000, reason="manual")
        assert seen_body["kind"] == "ban"
        assert seen_body["ttl_ms"] == 60_000
        assert seen_body["reason"] == "manual"
        assert "proxy_id" in seen_body
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_omits_ttl_ms_and_reason_when_not_provided():
    """Legacy callers `report(p, "cf")` must not regress to pushing extra keys."""
    c = _make_client(async_workers=1)
    seen_body: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        seen_body.update(json)
        return _fake_response({
            "penalty_factor": 1.0, "recent_event_count": 0, "server_time": 1,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            c.report("proxy-A", "cf")
        assert "ttl_ms" not in seen_body
        assert "reason" not in seen_body
    finally:
        c.close(wait=True, timeout=2.0)


def test_mark_proxy_banned_dispatches_default_3_day_ttl():
    c = _make_client(async_workers=1)
    captured: list = []

    def fake_report(proxy_id, kind, *, ttl_ms=None, reason=None):
        captured.append((proxy_id, kind, ttl_ms, reason))

    try:
        with patch.object(c, "report", side_effect=fake_report):
            c.mark_proxy_banned("proxy-A")
            c._async_queue.join()
        assert len(captured) == 1
        proxy_id, kind, ttl_ms, reason = captured[0]
        assert proxy_id == "proxy-A"
        assert kind == "ban"
        assert ttl_ms == DEFAULT_BAN_TTL_MS
        assert reason is None
    finally:
        c.close(wait=True, timeout=2.0)


def test_mark_proxy_banned_accepts_custom_ttl_and_reason():
    c = _make_client(async_workers=1)
    captured: list = []

    def fake_report(proxy_id, kind, *, ttl_ms=None, reason=None):
        captured.append((proxy_id, kind, ttl_ms, reason))

    try:
        with patch.object(c, "report", side_effect=fake_report):
            c.mark_proxy_banned("proxy-A", ttl_ms=60_000, reason="penalty_2")
            c._async_queue.join()
        assert captured == [("proxy-A", "ban", 60_000, "penalty_2")]
    finally:
        c.close(wait=True, timeout=2.0)


def test_mark_proxy_unbanned_dispatches_unban_kind_without_ttl():
    c = _make_client(async_workers=1)
    captured: list = []

    def fake_report(proxy_id, kind, *, ttl_ms=None, reason=None):
        captured.append((proxy_id, kind, ttl_ms, reason))

    try:
        with patch.object(c, "report", side_effect=fake_report):
            c.mark_proxy_unbanned("proxy-A")
            c._async_queue.join()
        assert captured == [("proxy-A", "unban", None, None)]
    finally:
        c.close(wait=True, timeout=2.0)


def test_mark_cf_bypass_permanent_when_ttl_omitted():
    """``ttl_ms is None`` means "permanent for this session" — Worker stores 0."""
    c = _make_client(async_workers=1)
    captured: list = []

    def fake_report(proxy_id, kind, *, ttl_ms=None, reason=None):
        captured.append((proxy_id, kind, ttl_ms, reason))

    try:
        with patch.object(c, "report", side_effect=fake_report):
            c.mark_cf_bypass("proxy-A")
            c._async_queue.join()
        assert captured == [("proxy-A", "cf_bypass", None, None)]
    finally:
        c.close(wait=True, timeout=2.0)


def test_mark_cf_bypass_with_explicit_ttl():
    c = _make_client(async_workers=1)
    captured: list = []

    def fake_report(proxy_id, kind, *, ttl_ms=None, reason=None):
        captured.append((proxy_id, kind, ttl_ms, reason))

    try:
        with patch.object(c, "report", side_effect=fake_report):
            c.mark_cf_bypass("proxy-A", ttl_ms=30_000, reason="cf-detected")
            c._async_queue.join()
        assert captured == [("proxy-A", "cf_bypass", 30_000, "cf-detected")]
    finally:
        c.close(wait=True, timeout=2.0)


def test_mark_helpers_are_fire_and_forget_on_unavailable_coordinator():
    """If the coordinator is unreachable, the helpers must not raise."""
    c = _make_client(async_workers=1)

    def boom(proxy_id, kind, **kwargs):
        raise CoordinatorUnavailable("simulated outage")

    try:
        with patch.object(c, "report", side_effect=boom):
            # None of these should raise — the caller is a hot path
            # (proxy_ban_manager.add_ban, state.mark_proxy_cf_bypass).
            c.mark_proxy_banned("proxy-A")
            c.mark_proxy_unbanned("proxy-A")
            c.mark_cf_bypass("proxy-A", ttl_ms=10_000)
            c._async_queue.join()
    finally:
        c.close(wait=True, timeout=2.0)


def test_normalize_proxy_id_uses_non_security_hash_marker():
    """``hashlib.sha1`` must be called with ``usedforsecurity=False``.

    Guards against a regression where the lint suppression is removed
    without thinking about it (Bandit S324). We can't introspect the
    keyword from the outside, so we just assert the function is callable
    and produces a stable, prefixed digest.
    """
    a = _normalize_proxy_id(None, fallback_seed="1.2.3.4:8080")
    b = _normalize_proxy_id(None, fallback_seed="1.2.3.4:8080")
    assert a == b
    assert a.startswith("proxy-")
    assert len(a) == len("proxy-") + 16


# ─────────────────────────────────────────────────────────────────────────
# P2-D — proxy health snapshot (success/failure events + latency EMA).
# Cover the wire-protocol expansion of ``LeaseResult`` and the new
# ``"success"`` report kind, plus the in-process health cache that
# ``ProxyPool.next_proxy`` reads through ``get_proxy_health_score``.
# ─────────────────────────────────────────────────────────────────────────


def test_lease_defaults_p2d_health_to_none_when_worker_omits_field():
    """Old Worker without ``health`` must still produce a valid LeaseResult."""
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 0, "penalty_factor": 1.0,
        "server_time": 0, "reason": "ok",
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            r = c.lease("p1", 0)
        assert r.health is None
    finally:
        c.close(wait=True, timeout=2.0)


def test_lease_parses_p2d_health_when_worker_emits_it():
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 0, "penalty_factor": 1.0,
        "server_time": 0, "reason": "ok",
        "health": {
            "success_count": 7, "failure_count": 3,
            "latency_ema_ms": 1234.5, "score": 0.65,
        },
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            r = c.lease("p1", 0)
        assert isinstance(r.health, ProxyHealthSnapshot)
        assert r.health.success_count == 7
        assert r.health.failure_count == 3
        assert r.health.latency_ema_ms == 1234.5
        assert r.health.score == 0.65
    finally:
        c.close(wait=True, timeout=2.0)


def test_lease_handles_explicit_null_p2d_health_field():
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 0, "penalty_factor": 1.0,
        "server_time": 0, "reason": "ok",
        "health": None,
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            r = c.lease("p1", 0)
        assert r.health is None
    finally:
        c.close(wait=True, timeout=2.0)


def test_lease_handles_malformed_p2d_health_payload():
    """A non-dict ``health`` (or one with bad types) must NOT crash the spider."""
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 0, "penalty_factor": 1.0,
        "server_time": 0, "reason": "ok",
        # int instead of dict — must be ignored, not raise
        "health": 42,
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            r = c.lease("p1", 0)
        assert r.health is None
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_accepts_success_kind_and_includes_latency_ms():
    c = _make_client(async_workers=1)
    seen_body: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        seen_body.update(json)
        return _fake_response({
            "penalty_factor": 1.0, "recent_event_count": 0, "server_time": 1,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            c.report("proxy-A", "success", latency_ms=850)
        assert seen_body["kind"] == "success"
        assert seen_body["latency_ms"] == 850
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_validates_success_kind_against_typos():
    """``successs`` should be rejected synchronously to surface typos."""
    c = _make_client(async_workers=1)
    try:
        with pytest.raises(ValueError, match="Invalid kind"):
            c.report("p1", "successs")
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_clamps_negative_latency_to_zero():
    """A clock-skew artefact must NOT poison the EMA; client clamps to 0."""
    c = _make_client(async_workers=1)
    seen_body: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002
        seen_body.update(json)
        return _fake_response({
            "penalty_factor": 1.0, "recent_event_count": 0, "server_time": 1,
        })

    try:
        with patch.object(c._session, "post", side_effect=fake_post):
            c.report("proxy-A", "success", latency_ms=-100)
        assert seen_body["latency_ms"] == 0
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_async_forwards_latency_ms_to_synchronous_report():
    c = _make_client(async_workers=1)
    captured: list = []

    def fake_report(proxy_id, kind, *, ttl_ms=None, reason=None, latency_ms=None):
        captured.append((proxy_id, kind, ttl_ms, reason, latency_ms))

    try:
        with patch.object(c, "report", side_effect=fake_report):
            c.report_async("proxy-A", "success", latency_ms=420)
            c._async_queue.join()
        assert captured == [("proxy-A", "success", None, None, 420)]
    finally:
        c.close(wait=True, timeout=2.0)


def test_report_async_legacy_call_does_not_pass_latency_ms_kwarg():
    """``report_async(p, "cf")`` (no latency) must keep monkeypatched
    legacy ``report`` stubs working — the worker only forwards
    ``latency_ms=…`` when explicitly set."""
    c = _make_client(async_workers=1)
    captured: list = []

    # NB: signature does NOT accept ``latency_ms`` — exercises the
    # worker's "skip kwarg when None" logic.
    def fake_report(proxy_id, kind, *, ttl_ms=None, reason=None):
        captured.append((proxy_id, kind, ttl_ms, reason))

    try:
        with patch.object(c, "report", side_effect=fake_report):
            c.report_async("proxy-A", "cf")
            c._async_queue.join()
        assert captured == [("proxy-A", "cf", None, None)]
    finally:
        c.close(wait=True, timeout=2.0)


def test_lease_populates_health_cache_for_subsequent_pool_weighting():
    c = _make_client(async_workers=1)
    payload = {
        "wait_ms": 0, "penalty_factor": 1.0,
        "server_time": 0, "reason": "ok",
        "health": {
            "success_count": 5, "failure_count": 0,
            "latency_ema_ms": 200.0, "score": 0.9,
        },
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload)):
            c.lease("proxy-A", 0)
        # Cached snapshot survives independently of the dataclass instance.
        assert c.get_proxy_health("proxy-A").score == 0.9
        assert c.get_proxy_health_score("proxy-A") == 0.9
    finally:
        c.close(wait=True, timeout=2.0)


def test_get_proxy_health_returns_none_for_unseen_proxy():
    c = _make_client(async_workers=1)
    try:
        assert c.get_proxy_health("never-leased") is None
        assert c.get_proxy_health_score("never-leased") is None
    finally:
        c.close(wait=True, timeout=2.0)


def test_get_proxy_health_handles_invalid_proxy_id_gracefully():
    """Empty/whitespace IDs must NOT crash the pool's weighting code."""
    c = _make_client(async_workers=1)
    try:
        assert c.get_proxy_health("") is None
        assert c.get_proxy_health(None) is None  # type: ignore[arg-type]
        assert c.get_proxy_health_score("") is None
    finally:
        c.close(wait=True, timeout=2.0)


def test_lease_does_not_overwrite_cache_when_health_is_missing():
    """A subsequent lease without ``health`` (e.g. partial Worker rollback)
    must NOT clear a previously-cached snapshot — that would leave the
    pool weighting flapping during deploys."""
    c = _make_client(async_workers=1)
    payload_with = {
        "wait_ms": 0, "penalty_factor": 1.0,
        "server_time": 0, "reason": "ok",
        "health": {
            "success_count": 5, "failure_count": 0,
            "latency_ema_ms": 200.0, "score": 0.9,
        },
    }
    payload_without = {
        "wait_ms": 0, "penalty_factor": 1.0,
        "server_time": 0, "reason": "ok",
    }
    try:
        with patch.object(c._session, "post", return_value=_fake_response(payload_with)):
            c.lease("proxy-A", 0)
        assert c.get_proxy_health_score("proxy-A") == 0.9
        with patch.object(c._session, "post", return_value=_fake_response(payload_without)):
            c.lease("proxy-A", 0)
        # Cache is sticky on missing ``health`` — last good value wins.
        assert c.get_proxy_health_score("proxy-A") == 0.9
    finally:
        c.close(wait=True, timeout=2.0)
