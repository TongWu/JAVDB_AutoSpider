"""Unit tests for the SessionLifecycle transition authority (ADR-019).

These tests are pure: no real database, Rust extension, or network. The
``transition`` dispatch tests monkeypatch the ``_db_reports`` primitives so the
SQL layer is never exercised.
"""

from __future__ import annotations

import itertools

import pytest

from javdb.storage.sessions import lifecycle
from javdb.storage.sessions.lifecycle import (
    COMMITTED,
    FAILED,
    FINALIZING,
    IN_PROGRESS,
    IllegalTransition,
    SessionState,
    can_transition,
    transition,
)

_STATUSES = (IN_PROGRESS, FINALIZING, COMMITTED, FAILED)

# The authoritative legal graph (excluding idempotent same-state edges).
_LEGAL_EDGES = {
    (IN_PROGRESS, FINALIZING),
    (IN_PROGRESS, COMMITTED),
    (IN_PROGRESS, FAILED),
    (FINALIZING, COMMITTED),
    (FINALIZING, FAILED),
}


# ── Pure can_transition matrix ────────────────────────────────────────────


def test_can_transition_full_matrix():
    """Every (frm, to) over the 4 statuses matches the legal graph + idempotency."""
    for frm, to in itertools.product(_STATUSES, _STATUSES):
        expected = (frm == to) or ((frm, to) in _LEGAL_EDGES)
        assert can_transition(frm, to) is expected, f"{frm} -> {to}"


def test_can_transition_known_legal_edges_true():
    for frm, to in _LEGAL_EDGES:
        assert can_transition(frm, to) is True


def test_can_transition_same_state_is_idempotent_true():
    for status in _STATUSES:
        assert can_transition(status, status) is True


def test_can_transition_illegal_terminal_edges_false():
    # The two latent data-corruption edges ADR-019 closes.
    assert can_transition(COMMITTED, FAILED) is False
    assert can_transition(FAILED, COMMITTED) is False
    # Other illegal edges out of terminal states.
    assert can_transition(COMMITTED, IN_PROGRESS) is False
    assert can_transition(COMMITTED, FINALIZING) is False
    assert can_transition(FAILED, IN_PROGRESS) is False
    assert can_transition(FAILED, FINALIZING) is False
    # No backward edge into in_progress.
    assert can_transition(FINALIZING, IN_PROGRESS) is False


def test_can_transition_none_source_false():
    for to in _STATUSES:
        assert can_transition(None, to) is False


def test_can_transition_unknown_target_false():
    assert can_transition(IN_PROGRESS, "bogus") is False
    assert can_transition(None, "bogus") is False
    assert can_transition(COMMITTED, "bogus") is False


# ── transition() — illegal edges raise ────────────────────────────────────


def _patch_state(monkeypatch, status):
    """Force get_state to report a fixed current status."""
    monkeypatch.setattr(
        lifecycle,
        "get_state",
        lambda session_id, *, db_path=None: SessionState(
            write_mode="pending", status=status
        ),
    )


def _patch_primitives(monkeypatch):
    """Record which primitive was dispatched; never touch a DB."""
    calls = []

    def make(name):
        def _fn(session_id, *, db_path=None, **kwargs):
            calls.append((name, session_id, kwargs))
            return 1

        return _fn

    monkeypatch.setattr(lifecycle, "_db_begin_finalize_session", make("finalize"))
    monkeypatch.setattr(lifecycle, "_db_finish_commit_session", make("finish_commit"))
    monkeypatch.setattr(lifecycle, "_db_mark_session_committed", make("mark_committed"))
    monkeypatch.setattr(lifecycle, "_db_mark_session_failed", make("mark_failed"))
    return calls


def test_transition_committed_to_failed_raises(monkeypatch):
    _patch_state(monkeypatch, COMMITTED)
    calls = _patch_primitives(monkeypatch)
    with pytest.raises(IllegalTransition):
        transition("S1", FAILED)
    assert calls == []  # no primitive dispatched


def test_transition_failed_to_committed_raises(monkeypatch):
    _patch_state(monkeypatch, FAILED)
    calls = _patch_primitives(monkeypatch)
    with pytest.raises(IllegalTransition):
        transition("S1", COMMITTED)
    assert calls == []


def test_transition_unknown_source_raises(monkeypatch):
    """A non-existent session (status None) cannot transition."""
    _patch_state(monkeypatch, None)
    calls = _patch_primitives(monkeypatch)
    with pytest.raises(IllegalTransition):
        transition("missing", COMMITTED)
    assert calls == []


# ── transition() — idempotent no-op ───────────────────────────────────────


def test_transition_idempotent_noop_returns_zero(monkeypatch):
    for status in _STATUSES:
        _patch_state(monkeypatch, status)
        calls = _patch_primitives(monkeypatch)
        # Transitioning a terminal state to itself must be a pure no-op.
        if status in (COMMITTED, FAILED):
            assert transition("S1", status) == 0
            assert calls == []


def test_transition_committed_to_committed_noop(monkeypatch):
    _patch_state(monkeypatch, COMMITTED)
    calls = _patch_primitives(monkeypatch)
    assert transition("S1", COMMITTED) == 0
    assert calls == []


# ── transition() — dispatch routing ───────────────────────────────────────


def test_transition_finalizing_to_committed_uses_strict_primitive(monkeypatch):
    _patch_state(monkeypatch, FINALIZING)
    calls = _patch_primitives(monkeypatch)
    transition("S1", COMMITTED)
    assert [c[0] for c in calls] == ["finish_commit"]


def test_transition_in_progress_to_committed_uses_loose_primitive(monkeypatch):
    _patch_state(monkeypatch, IN_PROGRESS)
    calls = _patch_primitives(monkeypatch)
    transition("S1", COMMITTED)
    assert [c[0] for c in calls] == ["mark_committed"]


def test_transition_in_progress_to_finalizing_dispatches(monkeypatch):
    _patch_state(monkeypatch, IN_PROGRESS)
    calls = _patch_primitives(monkeypatch)
    transition("S1", FINALIZING)
    assert [c[0] for c in calls] == ["finalize"]


def test_transition_to_failed_passes_reason(monkeypatch):
    _patch_state(monkeypatch, IN_PROGRESS)
    calls = _patch_primitives(monkeypatch)
    transition("S1", FAILED, reason="workflow_cancel")
    assert [c[0] for c in calls] == ["mark_failed"]
    assert calls[0][2] == {"reason": "workflow_cancel"}


def test_transition_passes_db_path_through(monkeypatch):
    captured = {}

    def _get_state(session_id, *, db_path=None):
        captured["get_state_db"] = db_path
        return SessionState(write_mode="pending", status=IN_PROGRESS)

    monkeypatch.setattr(lifecycle, "get_state", _get_state)

    def _begin(session_id, *, db_path=None, **kwargs):
        captured["begin_db"] = db_path
        return 1

    monkeypatch.setattr(lifecycle, "_db_begin_finalize_session", _begin)
    transition("S1", FINALIZING, db_path="/tmp/x.db")
    assert captured["get_state_db"] == "/tmp/x.db"
    assert captured["begin_db"] == "/tmp/x.db"
