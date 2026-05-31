"""ADR-035 site-contract gate on the API commit path.

These drive the real ``javdb.storage.sessions.commit.commit_session`` library
(the one behind ``POST /api/sessions/{id}/commit``) against a real isolated
SQLite DB, and assert the gating control flow:

* critical drift → ``RuntimeError`` raised, the pending-rows drain is skipped,
  and the session is NOT flipped to ``committed``;
* a clean verdict → commit proceeds and ``mark_committed`` is called;
* a sentinel evaluation error → fail-open (commit still proceeds);
* ``drop_pending`` is exempt from the gate (safe rollback direction).

The CLI-path equivalents live in ``tests/unit/test_commit_session_events.py``;
the sentinel *verdict* itself is covered by
``tests/unit/test_commit_gate_site_drift.py``.
"""

from __future__ import annotations

import pytest

import javdb.storage.db as _db
from javdb.ops.sentinel import service as sentinel_service
from javdb.ops.sentinel.models import DriftFinding, SentinelVerdict
from javdb.storage.db import db_create_report_session
from javdb.storage.sessions import CommitRequest, commit_session


def _new_pending_session() -> str:
    return db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-31",
        csv_filename="gate-api.csv",
        write_mode="pending",
    )


def _status(session_id: str):
    # Resolve REPORTS_DB_PATH at call time: the autouse ``_isolate_sqlite``
    # fixture patches the module attribute, so a value captured at import
    # time would point at the un-isolated (production) DB.
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT Status FROM ReportSessions WHERE Id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else None


def _critical_verdict() -> SentinelVerdict:
    return SentinelVerdict(
        critical=True,
        findings=[DriftFinding("index", "href", "critical", 0.05, 0.99)],
        evaluated=1,
    )


def test_api_commit_blocked_on_critical_drift(monkeypatch):
    """Critical drift must raise and never reach the drain / status flip."""
    sid = _new_pending_session()

    monkeypatch.setattr(
        sentinel_service, "evaluate_session",
        lambda *a, **k: _critical_verdict(),
    )
    marks: list = []
    monkeypatch.setattr(
        sentinel_service, "mark_committed",
        lambda s, **k: marks.append(s),
    )

    with pytest.raises(RuntimeError, match="site-contract drift gate"):
        commit_session(CommitRequest(session_id=str(sid)))

    # Gate blocked before the drain AND the status flip: the session is left
    # untouched at in_progress (not committed), and the fills are not marked.
    assert _status(sid) == "in_progress"
    assert marks == []                   # mark_committed never reached


def test_api_commit_proceeds_and_marks_on_clean(monkeypatch):
    """A clean verdict commits normally and marks the run baseline-eligible."""
    sid = _new_pending_session()

    monkeypatch.setattr(
        sentinel_service, "evaluate_session",
        lambda *a, **k: SentinelVerdict(critical=False),
    )
    marks: list = []
    monkeypatch.setattr(
        sentinel_service, "mark_committed",
        lambda s, **k: marks.append(s),
    )

    result = commit_session(CommitRequest(session_id=str(sid)))

    assert result.new_state == "committed"
    assert _status(sid) == "committed"
    assert marks == [str(sid)]


def test_api_commit_fails_open_on_sentinel_error(monkeypatch):
    """A sentinel evaluation error must NOT block the commit (fail-open)."""
    sid = _new_pending_session()

    def _boom(*a, **k):
        raise RuntimeError("sentinel boom")

    monkeypatch.setattr(sentinel_service, "evaluate_session", _boom)
    marks: list = []
    monkeypatch.setattr(
        sentinel_service, "mark_committed",
        lambda s, **k: marks.append(s),
    )

    result = commit_session(CommitRequest(session_id=str(sid)))

    assert result.new_state == "committed"
    assert _status(sid) == "committed"
    assert marks == [str(sid)]           # commit proceeded despite the error


def test_api_drop_pending_skips_gate_and_baseline_marking(monkeypatch):
    """``drop_pending`` discards staged rows rather than promoting them, so the
    gate must not evaluate drift AND the run's fills must NOT be marked
    baseline-eligible (a dropped bad/failed parse must not pollute the
    soft-field baseline)."""
    sid = _new_pending_session()

    def _should_not_run(*a, **k):
        raise AssertionError("gate evaluated drift on the drop_pending path")

    monkeypatch.setattr(sentinel_service, "evaluate_session", _should_not_run)
    marks: list = []
    monkeypatch.setattr(
        sentinel_service, "mark_committed",
        lambda s, **k: marks.append(s),
    )

    result = commit_session(
        CommitRequest(session_id=str(sid), drop_pending=True),
    )

    assert result.new_state == "committed"
    assert _status(sid) == "committed"
    assert marks == []   # discarded run must not enter the soft-field baseline
