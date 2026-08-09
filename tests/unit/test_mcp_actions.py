import json

import apps.mcp.tools.act as act
import javdb.storage.db as _db
from apps.mcp.tools.act import tool_rollback_session
from javdb.storage.sessions.commit import CommitResult


def _insert_session(session_id: str, status: str = "in_progress") -> None:
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO ReportSessions (Id, ReportType, ReportDate, CsvFilename, "
            "DateTimeCreated, Status) VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                "daily",
                "2026-06-13",
                "t.csv",
                "2026-06-13T00:00:00Z",
                status,
            ),
        )


def _session_status(session_id: str) -> str | None:
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT Status FROM ReportSessions WHERE Id = ?",
            (session_id,),
        ).fetchone()
    return None if row is None else row[0]


def test_rollback_dry_run_returns_preview_without_mutation_or_audit(monkeypatch):
    _insert_session("rb-dry-001")
    events = []
    monkeypatch.setattr(
        "javdb.pipeline.events.store.emit",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )

    out = tool_rollback_session("rb-dry-001")

    assert out["confirmed"] is False
    assert out["dry_run"] is True
    assert "would_affect" in out
    assert _session_status("rb-dry-001") == "in_progress"
    assert events == []


def test_rollback_confirm_executes_and_emits_one_audit_event(monkeypatch):
    _insert_session("rb-run-001")
    events: list[dict] = []

    def _capture(event_type, *, session_id, entity_type, **kwargs):
        events.append(
            {
                "type": event_type,
                "session_id": session_id,
                "entity_type": entity_type,
                **kwargs,
            }
        )
        return 1

    monkeypatch.setattr("javdb.pipeline.events.store.emit", _capture)

    out = tool_rollback_session("rb-run-001", confirm=True)

    assert out["confirmed"] is True
    assert out["dry_run"] is False
    assert "affected" in out
    assert _session_status("rb-run-001") is None
    assert len(events) == 1
    assert events[0]["type"] == "SessionRolledBack"
    assert events[0]["session_id"] == "rb-run-001"
    assert events[0]["entity_type"] == "session"
    assert json.loads(events[0]["payload"])["affected"] == out["affected"]


def test_rollback_confirmed_service_error_degrades_without_raising(monkeypatch):
    calls = []
    events: list[dict] = []

    def _boom(self, session_id, *, dry_run, scope, force):
        calls.append(
            {
                "session_id": session_id,
                "dry_run": dry_run,
                "scope": scope,
                "force": force,
            }
        )
        raise RuntimeError("kaboom")

    def _capture(event_type, *, session_id, entity_type, **kwargs):
        events.append(
            {
                "type": event_type,
                "session_id": session_id,
                "entity_type": entity_type,
                **kwargs,
            }
        )
        return 1

    monkeypatch.setattr(
        "javdb.storage.repos.session_lifecycle_repo."
        "SessionLifecycleRepo.rollback_session",
        _boom,
    )
    monkeypatch.setattr("javdb.pipeline.events.store.emit", _capture)

    out = tool_rollback_session(
        "rb-error-001",
        scope="history",
        force=True,
        confirm=True,
    )

    assert out["error"] == "rollback_session failed"
    assert "kaboom" in out["detail"]
    assert len(events) == 1
    assert events[0]["type"] == "SessionRollbackFailed"
    assert events[0]["session_id"] == "rb-error-001"
    assert events[0]["entity_type"] == "session"
    assert json.loads(events[0]["payload"]) == {
        "scope": "history",
        "force": True,
        "error": "kaboom",
    }
    assert calls == [
        {
            "session_id": "rb-error-001",
            "dry_run": False,
            "scope": "history",
            "force": True,
        }
    ]


def test_commit_dry_run_returns_preview_without_mutation_or_audit(monkeypatch):
    _insert_session("commit-dry-001")
    events = []

    def _unexpected_commit(req):
        raise AssertionError(f"dry-run called commit_session with {req!r}")

    monkeypatch.setattr(
        "javdb.storage.sessions.commit.commit_session",
        _unexpected_commit,
    )
    monkeypatch.setattr(
        "javdb.pipeline.events.store.emit",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )

    out = act.tool_commit_session("commit-dry-001")

    assert out == {
        "action": "commit_session",
        "session_id": "commit-dry-001",
        "confirmed": False,
        "dry_run": True,
        "force": False,
        "drop_pending": False,
        "session_found": True,
        "would_finalize_pending": {
            "pending_residual_count": 0,
            "pending_applied_count": 0,
            "pending_total_count": 0,
        },
        "note": "Re-call with confirm=true to execute.",
    }
    assert _session_status("commit-dry-001") == "in_progress"
    assert events == []


def test_commit_drop_pending_dry_run_describes_action_without_side_effects(
    monkeypatch,
):
    _insert_session("commit-drop-dry-001")
    events = []

    def _unexpected_commit(req):
        raise AssertionError(f"dry-run called commit_session with {req!r}")

    monkeypatch.setattr(
        "javdb.storage.sessions.commit.commit_session",
        _unexpected_commit,
    )
    monkeypatch.setattr(
        "javdb.pipeline.events.store.emit",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )

    out = act.tool_commit_session(
        "commit-drop-dry-001",
        force=True,
        drop_pending=True,
    )

    assert out["confirmed"] is False
    assert out["dry_run"] is True
    assert out["force"] is True
    assert out["drop_pending"] is True
    assert out["session_found"] is True
    assert out["would_drop_pending"] == {
        "pending_residual_count": 0,
        "pending_applied_count": 0,
        "pending_total_count": 0,
    }
    assert "would_finalize_pending" not in out
    assert _session_status("commit-drop-dry-001") == "in_progress"
    assert events == []


def test_commit_confirm_executes_and_emits_one_audit_event(monkeypatch):
    calls = []
    events: list[dict] = []

    def _commit(req):
        calls.append(req)
        return CommitResult(
            session_id=req.session_id,
            new_state="committed",
            pending_dropped=3,
        )

    def _capture(event_type, *, session_id, entity_type, **kwargs):
        events.append(
            {
                "type": event_type,
                "session_id": session_id,
                "entity_type": entity_type,
                **kwargs,
            }
        )
        return 1

    monkeypatch.setattr("javdb.storage.sessions.commit.commit_session", _commit)
    monkeypatch.setattr("javdb.pipeline.events.store.emit", _capture)

    out = act.tool_commit_session(
        "commit-run-001",
        force=True,
        drop_pending=True,
        confirm=True,
    )

    assert len(calls) == 1
    assert calls[0].session_id == "commit-run-001"
    assert calls[0].force is True
    assert calls[0].drop_pending is True
    assert calls[0].emit_metrics is False
    assert calls[0].fanout_claims is False
    assert out == {
        "action": "commit_session",
        "session_id": "commit-run-001",
        "confirmed": True,
        "dry_run": False,
        "result": {
            "session_id": "commit-run-001",
            "new_state": "committed",
            "pending_dropped": 3,
        },
    }
    assert len(events) == 1
    assert events[0]["type"] == "SessionCommitted"
    assert events[0]["session_id"] == "commit-run-001"
    assert events[0]["entity_type"] == "session"
    assert json.loads(events[0]["payload"]) == {
        "new_state": "committed",
        "pending_dropped": 3,
        "force": True,
        "drop_pending": True,
    }


def test_commit_confirmed_service_error_degrades_and_emits_one_failure_event(
    monkeypatch,
):
    calls = []
    events: list[dict] = []

    def _boom(req):
        calls.append(req)
        raise RuntimeError("commit-boom")

    def _capture(event_type, *, session_id, entity_type, **kwargs):
        events.append(
            {
                "type": event_type,
                "session_id": session_id,
                "entity_type": entity_type,
                **kwargs,
            }
        )
        return 1

    monkeypatch.setattr("javdb.storage.sessions.commit.commit_session", _boom)
    monkeypatch.setattr("javdb.pipeline.events.store.emit", _capture)

    out = act.tool_commit_session(
        "commit-error-001",
        force=True,
        drop_pending=True,
        confirm=True,
    )

    assert out["error"] == "commit_session failed"
    assert out["detail"] == "commit-boom"
    assert len(calls) == 1
    assert calls[0].session_id == "commit-error-001"
    assert calls[0].force is True
    assert calls[0].drop_pending is True
    assert len(events) == 1
    assert events[0]["type"] == "SessionCommitFailed"
    assert events[0]["session_id"] == "commit-error-001"
    assert events[0]["entity_type"] == "session"
    assert json.loads(events[0]["payload"]) == {
        "force": True,
        "drop_pending": True,
        "error": "commit-boom",
    }
