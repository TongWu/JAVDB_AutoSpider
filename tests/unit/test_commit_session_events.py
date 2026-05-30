"""Pin the ADR-036 commit-class emit placement in apps.cli.db.commit_session.

The event must agree with reality about what committed (ADR-036 D4):

* ``SessionCommitted`` fires only AFTER ``transition(sid, "committed")``
  succeeds — never merely because the pending drain succeeded. A drain that
  succeeds but whose status transition then fails must NOT emit
  ``SessionCommitted``.
* ``SessionFailed`` fires on BOTH failure paths: a drain exception and a
  transition exception.

These tests mock the heavy collaborators and assert the emitted event types
per control-flow path, so a future refactor that moves the emit call back to
the drain-success log (the original IMP placement) would regress and fail.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import apps.cli.db.commit_session as cs


def _emitted_types(recorder):
    """Event types passed to the patched _emit_event recorder."""
    return [c.args[0] for c in recorder.call_args_list]


@pytest.fixture
def harness(monkeypatch):
    """Patch commit_session collaborators; return a configurable namespace.

    Caller sets ``h.transition_raises`` / ``h.drain_raises`` before invoking
    ``cs.main([...])``; ``h.emit`` records every _emit_event call.
    """
    from unittest.mock import MagicMock

    state = SimpleNamespace(transition_raises=False, drain_raises=False)

    monkeypatch.setattr(cs, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(cs, "close_db", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_emit_pending_verify", lambda *a, **k: None)
    monkeypatch.setattr(
        cs, "read_session_pre_state",
        lambda sid: SimpleNamespace(write_mode="pending", status="in_progress"),
    )

    class _Repo:
        def commit_session(self, sid):
            if state.drain_raises:
                raise RuntimeError("drain boom")
            return {"residual_cleanup": False, "pending_deleted": 0}

    monkeypatch.setattr(cs, "HistoryRepo", lambda *a, **k: _Repo())

    def _transition(sid, status):
        if state.transition_raises:
            raise RuntimeError("transition boom")
        return 1

    monkeypatch.setattr(cs, "transition", _transition)

    emit = MagicMock()
    monkeypatch.setattr(cs, "_emit_event", emit)
    state.emit = emit
    return state


def test_successful_commit_emits_session_committed_only(harness):
    rc = cs.main(["--session-id", "S1", "--no-claim-commit"])
    assert rc == 0
    assert _emitted_types(harness.emit) == ["SessionCommitted"]


def test_transition_failure_emits_session_failed_not_committed(harness):
    # Drain succeeds but the status transition fails: the log must NOT claim
    # the session committed. This is the corrected-placement guarantee.
    harness.transition_raises = True
    rc = cs.main(["--session-id", "S1", "--no-claim-commit"])
    assert rc == 1
    assert _emitted_types(harness.emit) == ["SessionFailed"]


def test_drain_failure_emits_session_failed(harness):
    harness.drain_raises = True
    rc = cs.main(["--session-id", "S1", "--no-claim-commit"])
    assert rc == 1
    assert _emitted_types(harness.emit) == ["SessionFailed"]
