from __future__ import annotations

from contextlib import contextmanager

import pytest

import javdb.storage.db._db_history_write as history_write
import javdb.storage.db._db_connection as db_connection
import javdb.storage.db._db_reports as reports
from javdb.storage.db import (
    db_commit_session_history,
    db_create_report_session,
    db_get_session_status,
    db_stage_history_write,
)
from javdb.storage.d1_recovery import RecoveryEvent, RecoveryPolicy, append_event


def test_pending_commit_refuses_unresolved_recovery_key(monkeypatch, tmp_path):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        db_connection,
        "current_backend",
        lambda: "d1",
    )
    sid = db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-19",
        csv_filename="recovery-block.csv",
        write_mode="pending",
    )
    db_stage_history_write(
        sid,
        "movie",
        {
            "Href": "https://javdb.com/v/abc",
            "VideoCode": "ABC-001",
            "DateTimeVisited": "2026-05-19 12:00:00",
        },
    )
    policy = RecoveryPolicy(
        logical_db="history",
        operation_type="pending_stage",
        idempotency_key=f"history:{sid}:seq1",
        ordering_key=f"history:{sid}",
        recovery_allowed=True,
        max_attempts=3,
    )
    append_event(
        tmp_path / "D1" / "d1_recovery_outbox.jsonl",
        RecoveryEvent.queued(
            policy,
            "INSERT INTO x VALUES (?)",
            ["a"],
            "timeout",
        ),
    )

    with pytest.raises(RuntimeError, match="unresolved D1 recovery"):
        db_commit_session_history(sid)

    assert db_get_session_status(sid) == ("pending", "in_progress")


def test_pending_commit_flushes_safe_batch_before_finalizing(monkeypatch):
    calls = []

    monkeypatch.setattr(
        reports,
        "db_get_session_status",
        lambda _sid, db_path=None: ("pending", "in_progress"),
    )
    monkeypatch.setattr(
        reports,
        "db_begin_finalize_session",
        lambda _sid, db_path=None: calls.append("finalizing"),
    )
    monkeypatch.setattr(
        history_write,
        "_assert_no_blocking_d1_recovery",
        lambda _sid: calls.append("gate"),
    )
    monkeypatch.setattr(
        history_write,
        "_commit_session_bulk",
        lambda *args, **kwargs: ({}, set(), set()),
    )
    monkeypatch.setattr(history_write, "_pending_distinct_hrefs", lambda *_args: [])

    class Conn:
        def flush(self, ordering_key=None):
            calls.append(f"flush:{ordering_key}")
            raise RuntimeError("safe batch failed before finalizing")

    @contextmanager
    def fake_get_db(_path=None):
        yield Conn()

    monkeypatch.setattr(history_write, "_get_db", fake_get_db)

    with pytest.raises(RuntimeError, match="safe batch failed before finalizing"):
        history_write.db_commit_session_history("s1")

    assert calls == ["flush:history:s1"]
    assert "finalizing" not in calls


def test_pending_commit_runs_gate_before_finalizing(monkeypatch):
    import javdb.storage.sessions.lifecycle as lifecycle

    calls = []

    monkeypatch.setattr(
        reports,
        "db_get_session_status",
        lambda _sid, db_path=None: ("pending", "in_progress"),
    )
    # Status flips now route through SessionLifecycle.transition (ADR-019),
    # which reads status and dispatches via its own import-time aliases.
    # Stub those so the call ordering is observable without a real DB. The
    # status stays in_progress, so the committed flip takes the loose
    # in_progress->committed primitive; record "committed" from either path.
    monkeypatch.setattr(
        lifecycle,
        "_db_get_session_status",
        lambda _sid, db_path=None: ("pending", "in_progress"),
    )
    monkeypatch.setattr(
        lifecycle,
        "_db_begin_finalize_session",
        lambda _sid, db_path=None: calls.append("finalizing"),
    )
    monkeypatch.setattr(
        lifecycle,
        "_db_finish_commit_session",
        lambda _sid, db_path=None: calls.append("committed"),
    )
    monkeypatch.setattr(
        lifecycle,
        "_db_mark_session_committed",
        lambda _sid, db_path=None: calls.append("committed"),
    )
    monkeypatch.setattr(
        history_write,
        "_assert_no_blocking_d1_recovery",
        lambda _sid: calls.append("gate"),
    )
    monkeypatch.setattr(
        history_write,
        "_commit_session_bulk",
        lambda *args, **kwargs: ({}, set(), set()),
    )
    monkeypatch.setattr(history_write, "_pending_distinct_hrefs", lambda *_args: [])
    monkeypatch.setattr(history_write, "_d1_retry_pending_cleanup", lambda *_args: None)

    class Cursor:
        rowcount = 0

    class Conn:
        def flush(self, ordering_key=None):
            calls.append(f"flush:{ordering_key}")

        def execute(self, *args, **kwargs):
            return Cursor()

    @contextmanager
    def fake_get_db(_path=None):
        yield Conn()

    monkeypatch.setattr(history_write, "_get_db", fake_get_db)

    counts = history_write.db_commit_session_history("s1")

    assert calls[:3] == ["flush:history:s1", "gate", "finalizing"]
    assert "committed" in calls
    assert counts["hrefs_processed"] == 0
