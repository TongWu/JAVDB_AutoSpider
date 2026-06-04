"""Unit tests for :class:`javdb.proxy.selection.ProxySelectionSignal`.

ADR-023 Phase 4 (IMP-ADR023-04, Tasks 1 & 2). The signal is a thin,
fail-open adapter that chains a *primary* health provider (the W5.5
``/recommend_proxy`` policy) in front of a *fallback* callable (the
local coordinator health cache). It is wired into
``ProxyPool.set_health_provider`` — which already owns clamp / NaN
normalization — so the signal MUST return raw values and MUST NEVER
raise into the pool's hot selection path.

These tests use in-file fakes only; they never import the Worker-facing
clients so the suite stays fast and offline.
"""

from __future__ import annotations

import math

import pytest

from javdb.proxy.selection import ProxySelectionSignal


class FakePrimary:
    """Stand-in for :class:`RecommendProxyPolicy`.

    Exposes the optional protocol the signal probes: ``label``,
    ``start()``, ``close()``/``shutdown()`` and ``score_for()``. The real
    policy uses ``shutdown()`` (no ``close()``); a separate fake below
    pins that branch. This one offers ``close()`` so both names are
    exercised across the suite.
    """

    label = "fake-primary"

    def __init__(self, scores=None, *, raises=False):
        self.scores = scores or {}
        self.raises = raises
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def score_for(self, proxy_name):
        if self.raises:
            raise RuntimeError("primary failed")
        return self.scores.get(proxy_name)


class FakePrimaryShutdownOnly:
    """Primary that mirrors the real policy: ``shutdown()``, no ``close()``."""

    def __init__(self, scores=None):
        self.scores = scores or {}
        self.started = False
        self.shutdown_called = False

    def start(self):
        self.started = True

    def shutdown(self):
        self.shutdown_called = True

    def score_for(self, proxy_name):
        return self.scores.get(proxy_name)


class FakePrimaryNoLifecycle:
    """Primary with only ``score_for`` — no start/close/shutdown/label."""

    def __init__(self, scores=None):
        self.scores = scores or {}

    def score_for(self, proxy_name):
        return self.scores.get(proxy_name)


# --- Step 2: primary score wins ------------------------------------------


def test_primary_score_wins_fallback_not_called():
    signal = ProxySelectionSignal(
        primary=FakePrimary({"A": 0.8}),
        fallback_score_for=lambda name: pytest.fail("fallback should not be called"),
    )
    assert signal.score_for("A") == 0.8


def test_primary_score_returned_unchanged():
    """An out-of-range / unusual primary value is returned verbatim.

    The signal must not clamp — ProxyPool owns normalization.
    """
    signal = ProxySelectionSignal(
        primary=FakePrimary({"A": 1.7, "B": -0.3}),
        fallback_score_for=lambda name: pytest.fail("fallback should not be called"),
    )
    assert signal.score_for("A") == 1.7
    assert signal.score_for("B") == -0.3


# --- Step 3: missing/stale primary falls back PER PROXY ------------------


def test_missing_primary_falls_back_per_proxy():
    """Primary has a fresh score for A but None for B.

    The fallback must be consulted only for B, and only its value is
    returned for B. A keeps the primary value with no fallback call.
    """
    fallback_calls = []

    def fallback(name):
        fallback_calls.append(name)
        return {"B": 0.42}.get(name)

    signal = ProxySelectionSignal(
        primary=FakePrimary({"A": 0.9}),  # B absent -> None
        fallback_score_for=fallback,
    )

    assert signal.score_for("A") == 0.9
    assert signal.score_for("B") == 0.42
    # Fallback consulted only for the proxy the primary couldn't score.
    assert fallback_calls == ["B"]


def test_missing_primary_and_missing_fallback_returns_none():
    """Primary None + fallback None -> overall None (pool uses neutral)."""
    signal = ProxySelectionSignal(
        primary=FakePrimary({}),  # everything -> None
        fallback_score_for=lambda name: None,
    )
    assert signal.score_for("Z") is None


def test_no_fallback_provided_returns_primary_or_none():
    """With no fallback callable, a missing primary score yields None."""
    signal = ProxySelectionSignal(primary=FakePrimary({"A": 0.5}))
    assert signal.score_for("A") == 0.5
    assert signal.score_for("missing") is None


# --- Step 4: exceptions fail open ----------------------------------------


def test_primary_raises_fallback_returns_score():
    """(a) primary raises, fallback returns a score -> that score."""
    signal = ProxySelectionSignal(
        primary=FakePrimary(raises=True),
        fallback_score_for=lambda name: 0.33,
    )
    assert signal.score_for("A") == 0.33


def test_primary_raises_fallback_raises_returns_none():
    """(b) primary raises, fallback raises -> None (fully fail-open)."""

    def fallback(name):
        raise RuntimeError("fallback failed too")

    signal = ProxySelectionSignal(
        primary=FakePrimary(raises=True),
        fallback_score_for=fallback,
    )
    assert signal.score_for("A") is None


def test_score_for_never_raises_with_no_providers():
    """Empty signal (no primary, no fallback) returns None, never raises."""
    signal = ProxySelectionSignal()
    assert signal.score_for("anything") is None


def test_nan_from_primary_returned_unchanged():
    """The signal must NOT reject NaN — ProxyPool normalizes it.

    A primary that yields NaN should have that NaN passed straight
    through (the fallback is not consulted because NaN is not None).
    """
    signal = ProxySelectionSignal(
        primary=FakePrimary({"A": float("nan")}),
        fallback_score_for=lambda name: pytest.fail("fallback should not be called"),
    )
    result = signal.score_for("A")
    assert isinstance(result, float)
    assert math.isnan(result)


# --- Step 5: lifecycle & label observable --------------------------------


def test_start_delegates_to_primary():
    primary = FakePrimary({"A": 0.8})
    signal = ProxySelectionSignal(primary=primary)
    signal.start()
    assert primary.started is True


def test_start_no_primary_is_noop():
    """start() with no primary must not raise."""
    signal = ProxySelectionSignal()
    signal.start()  # no exception


def test_close_delegates_to_primary_close():
    primary = FakePrimary({"A": 0.8})
    signal = ProxySelectionSignal(primary=primary)
    signal.close()
    assert primary.closed is True


def test_close_delegates_to_primary_shutdown_when_no_close():
    """When the primary has only shutdown() (the real policy), close()
    must call shutdown()."""
    primary = FakePrimaryShutdownOnly({"A": 0.8})
    signal = ProxySelectionSignal(primary=primary)
    signal.start()
    signal.close()
    assert primary.started is True
    assert primary.shutdown_called is True


def test_close_no_lifecycle_methods_is_noop():
    """Primary lacking close/shutdown: close() fails open silently."""
    primary = FakePrimaryNoLifecycle({"A": 0.8})
    signal = ProxySelectionSignal(primary=primary)
    signal.close()  # no exception


def test_label_is_stable_and_describes_chain():
    """label is deterministic for injected fakes and reflects the chain."""
    primary = FakePrimary({"A": 0.8})
    signal = ProxySelectionSignal(
        primary=primary,
        fallback_score_for=lambda name: None,
        label="fake-primary+fake-fallback",
    )
    assert signal.label == "fake-primary+fake-fallback"
    # Stable across reads.
    assert signal.label == "fake-primary+fake-fallback"


def test_label_default_when_not_supplied():
    """A signal with providers but no explicit label still exposes a
    non-empty string label."""
    signal = ProxySelectionSignal(
        primary=FakePrimary({"A": 0.8}),
        fallback_score_for=lambda name: None,
    )
    assert isinstance(signal.label, str)
    assert signal.label != ""
