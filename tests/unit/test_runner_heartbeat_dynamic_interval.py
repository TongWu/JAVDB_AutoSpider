"""Tests for the dynamic heartbeat cadence + signal feedback (P2-E auto-toggle).

The runner heartbeat loop ticks at one of two cadences depending on the
combination of ``state._movie_claim_mode`` and the cached
``state._movie_claim_last_recommended`` flag:

- ``auto`` + ``recommended=False`` → 15 s (single-runner; tighten the
  worst-case lock-leak window when a peer joins).
- ``auto`` + ``recommended=True`` → 60 s (multi-runner; canonical TTL/5).
- ``force_on`` / ``off`` → 60 s regardless of signal.

The loop also forwards the response's ``movie_claim_recommended`` into
:func:`runtime.state._apply_movie_claim_recommendation` on every
successful heartbeat / re-register; failure paths (network blip,
malformed response) intentionally do NOT update the cached flag — a
single transient hiccup must not unmount an active claim coordinator.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import packages.python.javdb_spider.runtime.state as state  # noqa: E402
from packages.python.javdb_platform.movie_claim_client import (  # noqa: E402
    MOVIE_CLAIM_MODE_AUTO,
    MOVIE_CLAIM_MODE_FORCE_ON,
    MOVIE_CLAIM_MODE_OFF,
)
from packages.python.javdb_platform.runner_registry_client import (  # noqa: E402
    HeartbeatResult,
    RegisterResult,
    RunnerRegistryClient,
    RunnerRegistryUnavailable,
)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_OFF, raising=False)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False, raising=False)
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    monkeypatch.setattr(state, "_movie_claim_client_pending", None, raising=False)
    state._runner_heartbeat_stop.clear()
    yield
    state._runner_heartbeat_stop.set()
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_OFF, raising=False)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False, raising=False)
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    monkeypatch.setattr(state, "_movie_claim_client_pending", None, raising=False)


# ── _next_heartbeat_interval truth table ───────────────────────────────────


def test_interval_auto_recommended_false_returns_single_runner_value(monkeypatch):
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False)
    assert state._next_heartbeat_interval() == state._HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC


def test_interval_auto_recommended_true_returns_multi_runner_value(monkeypatch):
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", True)
    assert state._next_heartbeat_interval() == state._RUNNER_HEARTBEAT_INTERVAL_SEC


@pytest.mark.parametrize("recommended", [True, False])
def test_interval_force_on_always_multi_runner(monkeypatch, recommended):
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_FORCE_ON)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", recommended)
    assert state._next_heartbeat_interval() == state._RUNNER_HEARTBEAT_INTERVAL_SEC


@pytest.mark.parametrize("recommended", [True, False])
def test_interval_off_always_multi_runner(monkeypatch, recommended):
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_OFF)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", recommended)
    assert state._next_heartbeat_interval() == state._RUNNER_HEARTBEAT_INTERVAL_SEC


def test_interval_constants_have_expected_values():
    """Lock the documented contract: 60 s multi-runner, 15 s single-runner."""
    assert state._HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC == 60.0
    assert state._HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC == 15.0
    # Legacy alias preserved.
    assert state._RUNNER_HEARTBEAT_INTERVAL_SEC == state._HEARTBEAT_INTERVAL_MULTI_RUNNER_SEC


# ── heartbeat loop signal feedback ─────────────────────────────────────────


def _make_client_mock() -> MagicMock:
    m = MagicMock(spec=RunnerRegistryClient)
    m.heartbeat.return_value = HeartbeatResult(
        alive=True, server_time_ms=1,
        movie_claim_recommended=True, movie_claim_min_runners=2,
    )
    m.register.return_value = RegisterResult(
        registered=True, server_time_ms=1,
        movie_claim_recommended=True, movie_claim_min_runners=2,
    )
    return m


def _run_loop_until(predicate, *, fake_client, timeout: float = 2.0) -> None:
    """Spin the heartbeat loop on a thread until *predicate* is true or
    *timeout* elapses, then cleanly stop it.  Raises if the predicate
    never becomes true."""
    state._runner_heartbeat_stop.clear()
    thread = threading.Thread(
        target=state._runner_heartbeat_loop,
        args=(fake_client, "h1"),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    state._runner_heartbeat_stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive(), "heartbeat loop hung past stop signal"
    if not predicate():
        raise AssertionError("predicate not satisfied within timeout")


def test_heartbeat_loop_feeds_recommended_signal_into_apply(monkeypatch):
    """Successful heartbeat with recommended=True → ``_apply_movie_claim_recommendation``
    is called with True → cached flag flips."""
    fake_client = _make_client_mock()
    fake_client.heartbeat.return_value = HeartbeatResult(
        alive=True, server_time_ms=1,
        movie_claim_recommended=True, movie_claim_min_runners=2,
    )
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False)
    # Speed the loop up: monkeypatching the legacy alias is enough for
    # the off / force_on / auto+recommended branches; we also patch
    # the single-runner constant so the auto+!recommended path is fast
    # (the loop starts in that branch when `_movie_claim_last_recommended=False`).
    monkeypatch.setattr(state, "_RUNNER_HEARTBEAT_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(state, "_HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC", 0.05)

    _run_loop_until(
        lambda: state._movie_claim_last_recommended is True,
        fake_client=fake_client,
        timeout=2.0,
    )
    assert fake_client.heartbeat.call_count >= 1


def test_heartbeat_loop_does_not_update_last_on_unavailable(monkeypatch):
    """Network blip raising ``RunnerRegistryUnavailable`` must not flip
    the cached recommendation — a transient outage must NOT unmount an
    already-active claim client."""
    fake_client = _make_client_mock()
    fake_client.heartbeat.side_effect = RunnerRegistryUnavailable("net blip")
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", True)
    monkeypatch.setattr(state, "_RUNNER_HEARTBEAT_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(state, "_HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC", 0.05)

    _run_loop_until(
        lambda: fake_client.heartbeat.call_count >= 3,
        fake_client=fake_client,
        timeout=2.0,
    )
    # Cached flag stayed True despite repeated failures.
    assert state._movie_claim_last_recommended is True


def test_heartbeat_loop_does_not_update_last_on_unexpected_exception(monkeypatch):
    """Same as above but for a non-Unavailable exception (defence-in-depth)."""
    fake_client = _make_client_mock()
    fake_client.heartbeat.side_effect = RuntimeError("boom")
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", True)
    monkeypatch.setattr(state, "_RUNNER_HEARTBEAT_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(state, "_HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC", 0.05)

    _run_loop_until(
        lambda: fake_client.heartbeat.call_count >= 2,
        fake_client=fake_client,
        timeout=2.0,
    )
    assert state._movie_claim_last_recommended is True


def test_heartbeat_loop_after_re_register_feeds_recommended_signal(monkeypatch):
    """``alive=False`` → re-register → its ``movie_claim_recommended`` is
    fed into the apply function (so a runner that went stale during a
    cohort-size flip can re-sync)."""
    fake_client = _make_client_mock()
    fake_client.heartbeat.return_value = HeartbeatResult(
        alive=False, server_time_ms=1,
        movie_claim_recommended=False, movie_claim_min_runners=2,
    )
    fake_client.register.return_value = RegisterResult(
        registered=True, server_time_ms=1,
        movie_claim_recommended=True, movie_claim_min_runners=2,
    )
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False)
    monkeypatch.setattr(state, "_RUNNER_HEARTBEAT_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(state, "_HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC", 0.05)

    _run_loop_until(
        lambda: fake_client.register.called and state._movie_claim_last_recommended is True,
        fake_client=fake_client,
        timeout=2.0,
    )
    assert state._movie_claim_last_recommended is True


def test_heartbeat_loop_off_mode_skips_signal_feedback(monkeypatch):
    """In off mode the apply function still records the cached flag
    (for symmetry across modes) but never mounts the global, so the
    loop's behaviour reduces to "tick at 60 s, swallow exceptions"."""
    fake_client = _make_client_mock()
    fake_client.heartbeat.return_value = HeartbeatResult(
        alive=True, server_time_ms=1,
        movie_claim_recommended=True, movie_claim_min_runners=2,
    )
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_OFF)
    monkeypatch.setattr(state, "_RUNNER_HEARTBEAT_INTERVAL_SEC", 0.05)

    _run_loop_until(
        lambda: fake_client.heartbeat.call_count >= 2,
        fake_client=fake_client,
        timeout=2.0,
    )
    # Off mode: global stays None even though cached flag flipped True.
    assert state.global_movie_claim_client is None
    assert state._movie_claim_last_recommended is True


def test_heartbeat_loop_old_worker_response_treats_missing_as_false(monkeypatch):
    """An old Worker that doesn't ship ``movie_claim_recommended`` is
    surfaced as ``False`` by the parser, which correctly unmounts an
    optimistically-mounted auto client (single-runner-safe default)."""
    fake_client = _make_client_mock()
    # Default-constructed HeartbeatResult mimics the parser's fallback
    # for an old Worker (movie_claim_recommended=False).
    fake_client.heartbeat.return_value = HeartbeatResult(
        alive=True, server_time_ms=1,
    )
    pending = MagicMock()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "global_movie_claim_client", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", True)
    monkeypatch.setattr(state, "_RUNNER_HEARTBEAT_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(state, "_HEARTBEAT_INTERVAL_SINGLE_RUNNER_SEC", 0.05)

    _run_loop_until(
        lambda: state.global_movie_claim_client is None,
        fake_client=fake_client,
        timeout=2.0,
    )
    # Old Worker → False → unmount.  Pending preserved for re-mount.
    assert state.global_movie_claim_client is None
    assert state._movie_claim_client_pending is pending
    assert state._movie_claim_last_recommended is False
