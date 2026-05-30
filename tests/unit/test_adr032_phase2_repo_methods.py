"""Pin the thin-delegation contract of the ADR-032 Phase 2a Repo methods.

Each method added in IMP-ADR032-02 Chunk 2a.1 is a 1:1 wrapper over a
``db_*`` function: it forwards its arguments and threads the Repo's
``db_path`` (or ``reports_db_path``) without adding logic. These tests
monkeypatch the backing ``db_*`` at the import site the method uses
(``javdb.storage.db.db_X``), call the Repo method, and assert that

  (a) the method returns the stub's return value verbatim, and
  (b) the stub received the expected positional/keyword args, including
      the Repo's ``db_path`` threaded as ``db_path`` / ``reports_db_path``.

Construction uses an explicit ``db_path`` string so the threading is
observable. The session/reports delegates live on the db_path-owned
``SessionLifecycleRepo`` (write-family pattern), not on the conn-owned
``SessionsRepo``.
"""

from __future__ import annotations

import pytest

import javdb.storage.db as db_mod
from javdb.storage.repos.history_repo import HistoryRepo
from javdb.storage.repos.session_lifecycle_repo import SessionLifecycleRepo


DB_PATH = "/tmp/adr032-phase2-repo.db"


class _Recorder:
    """Callable stub that records the last call and returns a sentinel."""

    def __init__(self, return_value):
        self.return_value = return_value
        self.args = None
        self.kwargs = None
        self.called = False

    def __call__(self, *args, **kwargs):
        self.called = True
        self.args = args
        self.kwargs = kwargs
        return self.return_value


@pytest.fixture
def lifecycle_repo():
    # The thin delegates route through the db_* family, which opens its own
    # connection from db_path. SessionLifecycleRepo is db_path-owned, so pass
    # db_path explicitly to assert it is threaded through.
    return SessionLifecycleRepo(db_path=DB_PATH)


# ── SessionLifecycleRepo session/reports delegates ────────────────────


def test_find_in_progress_sessions(monkeypatch, lifecycle_repo):
    rec = _Recorder(["s1", "s2"])
    monkeypatch.setattr(db_mod, "db_find_in_progress_sessions", rec)

    result = lifecycle_repo.find_in_progress_sessions(
        since="2026-01-01", max_age_hours=None, require_run_identity=True
    )

    assert result == ["s1", "s2"]
    assert rec.kwargs == {
        "since": "2026-01-01",
        "max_age_hours": None,
        "require_run_identity": True,
        "db_path": DB_PATH,
    }


def test_find_sessions_by_run(monkeypatch, lifecycle_repo):
    rec = _Recorder(["sid-a"])
    monkeypatch.setattr(db_mod, "db_find_sessions_by_run", rec)

    result = lifecycle_repo.find_sessions_by_run("run-1", 2)

    assert result == ["sid-a"]
    assert rec.args == ("run-1", 2)
    # reports_db_path (NOT db_path) carries the Repo's path for this one.
    assert rec.kwargs == {"reports_db_path": DB_PATH, "history_db_path": None}


def test_get_session_run_identity(monkeypatch, lifecycle_repo):
    rec = _Recorder(("run-9", 3))
    monkeypatch.setattr(db_mod, "db_get_session_run_identity", rec)

    result = lifecycle_repo.get_session_run_identity("sess-x")

    assert result == ("run-9", 3)
    assert rec.args == ("sess-x",)
    assert rec.kwargs == {"db_path": DB_PATH}


def test_pending_session_stats(monkeypatch):
    # pending_session_stats queries the history DB (Pending*HistoryWrites), so
    # it lives on the history-owned HistoryRepo, not SessionLifecycleRepo.
    rec = _Recorder({"pending_total_count": 7})
    monkeypatch.setattr(db_mod, "db_pending_session_stats", rec)

    result = HistoryRepo(db_path=DB_PATH).pending_session_stats("sess-y")

    assert result == {"pending_total_count": 7}
    assert rec.args == ("sess-y",)
    assert rec.kwargs == {"db_path": DB_PATH}


def test_find_stale_pending_sessions(monkeypatch, lifecycle_repo):
    rec = _Recorder([("sid", "in_progress", "pending")])
    monkeypatch.setattr(db_mod, "db_find_stale_pending_sessions", rec)

    result = lifecycle_repo.find_stale_pending_sessions(
        max_age_hours=12.0, require_run_identity=False
    )

    assert result == [("sid", "in_progress", "pending")]
    assert rec.kwargs == {
        "db_path": DB_PATH,
        "max_age_hours": 12.0,
        "require_run_identity": False,
    }


def test_find_in_progress_session_ids_for_run_csv(monkeypatch, lifecycle_repo):
    rec = _Recorder(["sid-1"])
    monkeypatch.setattr(
        db_mod, "db_find_in_progress_session_ids_for_run_csv", rec
    )

    result = lifecycle_repo.find_in_progress_session_ids_for_run_csv(
        "run-2", 1, "daily.csv"
    )

    assert result == ["sid-1"]
    assert rec.args == ("run-2", 1, "daily.csv")
    assert rec.kwargs == {"db_path": DB_PATH}


def test_get_latest_session_local(monkeypatch, lifecycle_repo):
    rec = _Recorder({"Id": "sess-latest"})
    monkeypatch.setattr(db_mod, "db_get_latest_session_local", rec)

    result = lifecycle_repo.get_latest_session_local("daily")

    assert result == {"Id": "sess-latest"}
    # Backing fn takes (report_type, db_path) positionally.
    assert rec.args == ("daily", DB_PATH)
    assert rec.kwargs == {}


def test_insert_report_rows(monkeypatch, lifecycle_repo):
    rec = _Recorder(5)
    monkeypatch.setattr(db_mod, "db_insert_report_rows", rec)

    rows = [{"href": "/v/abc"}]
    result = lifecycle_repo.insert_report_rows("sess-z", rows)

    assert result == 5
    # Backing fn takes (session_id, rows, db_path) positionally.
    assert rec.args == ("sess-z", rows, DB_PATH)
    assert rec.kwargs == {}


# ── HistoryRepo delegate ──────────────────────────────────────────────


def test_resume_finalizing_session(monkeypatch):
    rec = _Recorder({"movies_upserted": 2})
    monkeypatch.setattr(db_mod, "db_resume_finalizing_session", rec)

    repo = HistoryRepo(db_path=DB_PATH)
    result = repo.resume_finalizing_session(
        "sess-fin", reports_db_path=DB_PATH
    )

    assert result == {"movies_upserted": 2}
    assert rec.args == ("sess-fin",)
    # **kwargs pass-through: only what the caller forwarded.
    assert rec.kwargs == {"reports_db_path": DB_PATH}


# ── SessionLifecycleRepo delegate ─────────────────────────────────────


def test_rollback_session(monkeypatch):
    rec = _Recorder({"history": {"MovieHistory": 1}})
    monkeypatch.setattr(db_mod, "db_rollback_session", rec)

    repo = SessionLifecycleRepo(db_path=DB_PATH)
    result = repo.rollback_session("sess-r", dry_run=True, scope="history")

    assert result == {"history": {"MovieHistory": 1}}
    assert rec.args == ("sess-r",)
    # Full signature forwarded; reports_db_path defaults to the Repo's db_path.
    assert rec.kwargs == {
        "dry_run": True,
        "scope": "history",
        "force": False,
        "history_db_path": None,
        "reports_db_path": DB_PATH,
        "operations_db_path": None,
        "run_started_at": None,
        "failure_reason": None,
        "auto_resume_finalizing": True,
    }


def test_rollback_session_explicit_reports_db_path_overrides(monkeypatch):
    """An explicit reports_db_path wins over the Repo's db_path default."""
    rec = _Recorder({})
    monkeypatch.setattr(db_mod, "db_rollback_session", rec)

    repo = SessionLifecycleRepo(db_path=DB_PATH)
    repo.rollback_session("sess-r", reports_db_path="/tmp/other.db")

    assert rec.kwargs["reports_db_path"] == "/tmp/other.db"
