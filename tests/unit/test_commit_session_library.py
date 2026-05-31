"""Library-shape tests for ``javdb.storage.sessions.commit``.

Verifies the public contract: dataclass fields, defaults, and error
handling.  Behavioural tests for the underlying DB mutations are in
``test_commit_session_bulk.py`` and ``test_rollback_commit_cli.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import javdb.storage.sessions.commit as commit_lib
from javdb.ops.sentinel.models import DriftFinding, SentinelVerdict
from javdb.storage.sessions import (
    CommitRequest,
    CommitResult,
    SiteContractDriftError,
    commit_session,
)


def test_request_dataclass_has_expected_fields():
    req = CommitRequest(
        session_id="20260518T000000.000000Z-0001-0001",
        force=True,
        drop_pending=False,
        emit_metrics=True,
        fanout_claims=True,
        shard_date="2026-05-18",
    )
    assert req.session_id == "20260518T000000.000000Z-0001-0001"
    assert req.force is True
    assert req.emit_metrics is True
    assert req.fanout_claims is True
    assert req.shard_date == "2026-05-18"


def test_request_defaults_match_safe_posture():
    req = CommitRequest(session_id="some-id")
    assert req.force is False
    assert req.drop_pending is False
    assert req.emit_metrics is False
    assert req.fanout_claims is False
    assert req.shard_date is None


def test_result_dataclass_has_claim_results():
    result = CommitResult(
        session_id="x",
        new_state="committed",
        claim_results=[{"ok": True}],
    )
    assert result.claim_results == [{"ok": True}]


def test_result_defaults_to_empty_claim_results():
    result = CommitResult(session_id="x", new_state="committed")
    assert result.claim_results == []


def test_commit_raises_lookup_error_for_unknown_session():
    req = CommitRequest(session_id="missing-session-does-not-exist")
    with pytest.raises(LookupError):
        commit_session(req)


@pytest.fixture
def commit_harness(monkeypatch):
    """Patch library commit collaborators around a pending in-progress row."""
    import javdb.storage.db as storage_db
    import javdb.storage.db._db_history_write as history_write
    import javdb.storage.sessions.lifecycle as lifecycle

    state = SimpleNamespace(
        drained=[],
        transitions=[],
        marked=[],
        status="in_progress",
    )

    class _Conn:
        def execute(self, sql, params=()):
            assert "FROM ReportSessions" in sql
            assert params == ("S1",)
            return self

        def fetchone(self):
            return ("S1", "pending", state.status)

    class _DB:
        def __enter__(self):
            return _Conn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(storage_db, "get_db", lambda path: _DB())
    monkeypatch.setattr(storage_db, "REPORTS_DB_PATH", "reports.db", raising=False)

    def _drain(sid):
        state.drained.append(sid)
        return {"residual_cleanup": False, "pending_deleted": 0}

    def _transition(sid, status):
        state.transitions.append((sid, status))
        return 1

    monkeypatch.setattr(history_write, "db_commit_session_history", _drain)
    monkeypatch.setattr(lifecycle, "transition", _transition)
    monkeypatch.setattr(
        commit_lib,
        "_sentinel_mark_committed",
        lambda sid: state.marked.append(sid),
        raising=False,
    )
    return state


def test_site_drift_gate_blocks_library_commit_before_drain(
    commit_harness,
    monkeypatch,
):
    monkeypatch.setattr(
        commit_lib,
        "_sentinel_evaluate",
        lambda sid: SentinelVerdict(
            critical=True,
            findings=[DriftFinding("index", "href", "critical", 0.05, 0.99)],
            evaluated=1,
        ),
        raising=False,
    )

    with pytest.raises(SiteContractDriftError, match="site-contract.*critical.*refusing commit"):
        commit_session(CommitRequest(session_id="S1"))

    assert commit_harness.drained == []
    assert commit_harness.transitions == []
    assert commit_harness.marked == []


def test_site_drift_gate_allows_clean_library_commit_and_marks_committed(
    commit_harness,
    monkeypatch,
):
    monkeypatch.setattr(
        commit_lib,
        "_sentinel_evaluate",
        lambda sid: SentinelVerdict(critical=False),
        raising=False,
    )

    result = commit_session(CommitRequest(session_id="S1"))

    assert result.new_state == "committed"
    assert commit_harness.drained == ["S1"]
    assert commit_harness.transitions == [("S1", "committed")]
    assert commit_harness.marked == ["S1"]


def test_site_drift_gate_fails_open_on_library_sentinel_error(
    commit_harness,
    monkeypatch,
):
    def _boom(sid):
        raise RuntimeError("sentinel boom")

    monkeypatch.setattr(commit_lib, "_sentinel_evaluate", _boom, raising=False)

    result = commit_session(CommitRequest(session_id="S1"))

    assert result.new_state == "committed"
    assert commit_harness.drained == ["S1"]
    assert commit_harness.transitions == [("S1", "committed")]
    assert commit_harness.marked == ["S1"]


def test_already_committed_library_commit_marks_sentinel_idempotently(
    commit_harness,
    monkeypatch,
):
    commit_harness.status = "committed"
    evaluated: list[str] = []
    monkeypatch.setattr(
        commit_lib,
        "_sentinel_evaluate",
        lambda sid: evaluated.append(sid) or SentinelVerdict(critical=False),
        raising=False,
    )

    result = commit_session(CommitRequest(session_id="S1"))

    assert result.new_state == "committed"
    assert evaluated == ["S1"]
    assert commit_harness.drained == []
    assert commit_harness.transitions == []
    assert commit_harness.marked == ["S1"]


def test_already_committed_library_commit_blocks_critical_drift_before_mark(
    commit_harness,
    monkeypatch,
):
    commit_harness.status = "committed"
    monkeypatch.setattr(
        commit_lib,
        "_sentinel_evaluate",
        lambda sid: SentinelVerdict(
            critical=True,
            findings=[DriftFinding("index", "href", "critical", 0.05, 0.99)],
            evaluated=1,
        ),
        raising=False,
    )

    with pytest.raises(SiteContractDriftError, match="site-contract.*critical.*refusing commit"):
        commit_session(CommitRequest(session_id="S1"))

    assert commit_harness.drained == []
    assert commit_harness.transitions == []
    assert commit_harness.marked == []


def test_sentinel_mark_committed_failure_does_not_fail_library_commit(
    commit_harness,
    monkeypatch,
):
    monkeypatch.setattr(
        commit_lib,
        "_sentinel_evaluate",
        lambda sid: SentinelVerdict(critical=False),
        raising=False,
    )

    def _mark_boom(sid):
        raise RuntimeError("mark boom")

    monkeypatch.setattr(commit_lib, "_sentinel_mark_committed", _mark_boom)

    result = commit_session(CommitRequest(session_id="S1"))

    assert result.new_state == "committed"
    assert commit_harness.drained == ["S1"]
    assert commit_harness.transitions == [("S1", "committed")]
