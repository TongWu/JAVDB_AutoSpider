"""Tests for the ``MOVIE_CLAIM_ENABLED=auto`` toggle (P2-E + P1-B integration).

Covers the state-machine behaviour of :func:`runtime.state._apply_movie_claim_recommendation`:

- Mounts and unmounts ``state.global_movie_claim_client`` based on the
  registry's ``movie_claim_recommended`` signal in ``auto`` mode.
- Honours operator overrides (``force_on`` / ``off``) without flipping
  the global on registry signals.
- Logs only on state transitions (idempotent edges stay quiet).
- Stays consistent under concurrent access (heartbeat thread + signal
  feeder + atexit hook can all race in production).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import packages.python.javdb_spider.runtime.state as state  # noqa: E402
from packages.python.javdb_platform.movie_claim_client import (  # noqa: E402
    MOVIE_CLAIM_MODE_AUTO,
    MOVIE_CLAIM_MODE_FORCE_ON,
    MOVIE_CLAIM_MODE_OFF,
    MovieClaimClient,
)


def _fake_client() -> MagicMock:
    """Return a MagicMock that quacks like a :class:`MovieClaimClient`.

    No network I/O — the auto-toggle never inspects the client beyond
    "is it None?" so the spec is just enough to satisfy the type check.
    """
    return MagicMock(spec=MovieClaimClient)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Reset all auto-toggle module-level singletons before & after each test."""
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    monkeypatch.setattr(state, "_movie_claim_client_pending", None, raising=False)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_OFF, raising=False)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False, raising=False)
    yield
    monkeypatch.setattr(state, "global_movie_claim_client", None, raising=False)
    monkeypatch.setattr(state, "_movie_claim_client_pending", None, raising=False)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_OFF, raising=False)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False, raising=False)


# ── auto mode — recommended=True / False edge transitions ─────────────────


def test_apply_recommendation_true_mounts_pending_to_global_in_auto(monkeypatch):
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    # Simulate auto-mode having unmounted the global earlier.
    monkeypatch.setattr(state, "global_movie_claim_client", None)

    state._apply_movie_claim_recommendation(True)

    assert state.global_movie_claim_client is pending
    assert state._movie_claim_client_pending is pending  # never closed
    assert state._movie_claim_last_recommended is True


def test_apply_recommendation_false_unmounts_global_in_auto_keeps_pending(monkeypatch):
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    # Optimistic mount: global already points at pending at startup.
    monkeypatch.setattr(state, "global_movie_claim_client", pending)

    state._apply_movie_claim_recommendation(False)

    assert state.global_movie_claim_client is None
    # Pending must survive so a subsequent `True` is a free pointer copy.
    assert state._movie_claim_client_pending is pending
    assert state._movie_claim_last_recommended is False
    pending.close.assert_not_called()


def test_apply_recommendation_idempotent_in_auto(monkeypatch, caplog):
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "global_movie_claim_client", pending)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", True)

    with caplog.at_level(logging.INFO, logger=state.logger.name):
        # Same signal three times in a row → must not log or flap state.
        state._apply_movie_claim_recommendation(True)
        state._apply_movie_claim_recommendation(True)
        state._apply_movie_claim_recommendation(True)

    edge_logs = [
        r for r in caplog.records
        if "movie-claim auto: mounted" in r.getMessage()
        or "movie-claim auto: unmounted" in r.getMessage()
    ]
    assert edge_logs == []
    assert state.global_movie_claim_client is pending


def test_apply_recommendation_logs_each_edge_transition(monkeypatch, caplog):
    """Edge-triggered logging: True→False and False→True each emit one INFO."""
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "global_movie_claim_client", None)
    monkeypatch.setattr(state, "_movie_claim_last_recommended", False)

    with caplog.at_level(logging.INFO, logger=state.logger.name):
        state._apply_movie_claim_recommendation(True)   # mount
        state._apply_movie_claim_recommendation(True)   # idempotent
        state._apply_movie_claim_recommendation(False)  # unmount
        state._apply_movie_claim_recommendation(False)  # idempotent
        state._apply_movie_claim_recommendation(True)   # mount again

    mounted = [
        r for r in caplog.records if "movie-claim auto: mounted" in r.getMessage()
    ]
    unmounted = [
        r for r in caplog.records if "movie-claim auto: unmounted" in r.getMessage()
    ]
    assert len(mounted) == 2
    assert len(unmounted) == 1


# ── force_on mode — signal ignored, idempotent re-mount ───────────────────


def test_force_on_mode_ignores_recommendation_false(monkeypatch):
    """``force_on`` keeps the global mounted regardless of the signal —
    the legacy P1-B contract.  Operators picking this mode opt out of
    the auto-toggle entirely."""
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_FORCE_ON)
    monkeypatch.setattr(state, "global_movie_claim_client", pending)

    state._apply_movie_claim_recommendation(False)

    # Stays mounted even though the registry says "single runner".
    assert state.global_movie_claim_client is pending
    # Cached value still updated so heartbeat-interval helper stays
    # uniform across modes.
    assert state._movie_claim_last_recommended is False


def test_force_on_mode_ignores_recommendation_true(monkeypatch):
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_FORCE_ON)
    monkeypatch.setattr(state, "global_movie_claim_client", pending)

    state._apply_movie_claim_recommendation(True)

    assert state.global_movie_claim_client is pending
    assert state._movie_claim_last_recommended is True


def test_force_on_mode_remounts_if_global_was_blanked(monkeypatch, caplog):
    """Defensive: if some future caller blanks the global, force_on
    re-mounts on the next signal."""
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_FORCE_ON)
    monkeypatch.setattr(state, "global_movie_claim_client", None)

    with caplog.at_level(logging.INFO, logger=state.logger.name):
        state._apply_movie_claim_recommendation(True)

    assert state.global_movie_claim_client is pending
    force_logs = [
        r for r in caplog.records
        if "movie-claim force_on: mounted" in r.getMessage()
    ]
    assert len(force_logs) == 1


# ── off mode — global stays None always ────────────────────────────────────


def test_off_mode_never_mounts_even_when_recommended_true(monkeypatch):
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_OFF)
    monkeypatch.setattr(state, "global_movie_claim_client", None)

    state._apply_movie_claim_recommendation(True)

    assert state.global_movie_claim_client is None
    # Cached value still reflects the registry signal so observers can
    # diff "what mode did we resolve to" vs "what would registry have
    # chosen" if they care.
    assert state._movie_claim_last_recommended is True


def test_off_mode_keeps_global_none_under_oscillating_signal(monkeypatch):
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_OFF)
    monkeypatch.setattr(state, "global_movie_claim_client", None)

    for signal in (True, False, True, True, False):
        state._apply_movie_claim_recommendation(signal)
        assert state.global_movie_claim_client is None


# ── last_recommended is updated regardless of mode ─────────────────────────


@pytest.mark.parametrize("mode", [
    MOVIE_CLAIM_MODE_AUTO,
    MOVIE_CLAIM_MODE_FORCE_ON,
    MOVIE_CLAIM_MODE_OFF,
])
def test_apply_recommendation_updates_last_recommended_regardless_of_mode(
    monkeypatch, mode,
):
    monkeypatch.setattr(state, "_movie_claim_client_pending", _fake_client())
    monkeypatch.setattr(state, "_movie_claim_mode", mode)
    state._apply_movie_claim_recommendation(True)
    assert state._movie_claim_last_recommended is True
    state._apply_movie_claim_recommendation(False)
    assert state._movie_claim_last_recommended is False


# ── thread safety ──────────────────────────────────────────────────────────


def test_apply_recommendation_thread_safe_under_concurrent_calls(monkeypatch):
    """8 threads alternating True/False must never leave the global in an
    inconsistent state.

    Concrete invariants we can assert without blocking on the order:

    - ``_movie_claim_last_recommended`` must equal the last applied signal
      (True or False) — both paths are valid outcomes of the race;
    - ``global_movie_claim_client`` must agree with ``_movie_claim_last_recommended``
      under auto mode (True → pending, False → None).  The lock makes
      this hold even though the threads race.
    """
    pending = _fake_client()
    monkeypatch.setattr(state, "_movie_claim_client_pending", pending)
    monkeypatch.setattr(state, "_movie_claim_mode", MOVIE_CLAIM_MODE_AUTO)
    monkeypatch.setattr(state, "global_movie_claim_client", None)

    barrier = threading.Barrier(8)
    iterations = 200

    def worker(seed: bool) -> None:
        barrier.wait()
        signal = seed
        for _ in range(iterations):
            state._apply_movie_claim_recommendation(signal)
            signal = not signal

    threads = [
        threading.Thread(target=worker, args=(i % 2 == 0,))
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    for t in threads:
        assert not t.is_alive(), "worker hung"

    # Final state must agree (lock held during the read+write of
    # both fields → no torn snapshot).
    if state._movie_claim_last_recommended:
        assert state.global_movie_claim_client is pending
    else:
        assert state.global_movie_claim_client is None
