"""Unit tests for ``apps.cli.db.cleanup_stale_in_progress``."""

from __future__ import annotations

from datetime import datetime, timedelta

import apps.cli.db.cleanup_stale_in_progress as cleanup_cli
from javdb.storage.db import get_db, db_create_report_session


def _make_session(
    *,
    status: str,
    age_hours: float,
    csv: str,
    run_id: str | None = "legacy-guard-test",
) -> int:
    """Insert a session with a backdated DateTimeCreated."""
    when = (datetime.utcnow() - timedelta(hours=age_hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    sid = db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-08",
        csv_filename=csv,
        created_at=when,
        run_id=run_id,
    )
    if status != "in_progress":
        with get_db() as conn:
            conn.execute(
                "UPDATE ReportSessions SET Status=? WHERE Id=?",
                (status, sid),
            )
    return sid


class TestStaleSessionCleanup:
    def test_dry_run_does_not_modify_db(self, tmp_path, monkeypatch):
        sid = _make_session(status="in_progress", age_hours=72, csv="dr.csv")
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        rc = cleanup_cli.main([
            "--max-age-hours", "48",
            "--dry-run",
        ])
        assert rc == 0

        with get_db() as conn:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?", (sid,),
            ).fetchone()
        assert row["Status"] == "in_progress"

    def test_apply_marks_stale_session_failed_with_reason(
        self, tmp_path, monkeypatch,
    ):
        sid = _make_session(status="in_progress", age_hours=72, csv="ap.csv")
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        rc = cleanup_cli.main([
            "--max-age-hours", "48",
            "--apply",
            "--scope", "reports",
        ])
        # reports scope DELETEs the ReportSessions row, so it's gone now.
        assert rc in (0, 4)
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions WHERE Id=?", (sid,),
            ).fetchone()
        assert row["n"] == 0

    def test_apply_failure_reason_set_on_session(
        self, tmp_path, monkeypatch,
    ):
        sid = _make_session(status="in_progress", age_hours=72, csv="fr.csv")
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        rc = cleanup_cli.main([
            "--max-age-hours", "48",
            "--apply",
            "--scope", "operations",  # reports scope would DELETE the row
        ])
        assert rc in (0, 4)
        with get_db() as conn:
            row = conn.execute(
                "SELECT FailureReason, Status FROM ReportSessions WHERE Id=?",
                (sid,),
            ).fetchone()
        assert row["FailureReason"] == "stale_timeout"
        assert row["Status"] == "failed"

    def test_threshold_excludes_recent_sessions(self, tmp_path, monkeypatch):
        recent = _make_session(
            status="in_progress", age_hours=2, csv="recent.csv",
        )
        old = _make_session(
            status="in_progress", age_hours=72, csv="old.csv",
        )
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        rc = cleanup_cli.main([
            "--max-age-hours", "48",
            "--apply",
            "--scope", "operations",
        ])
        assert rc in (0, 4)
        with get_db() as conn:
            r_status = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?", (recent,),
            ).fetchone()["Status"]
            o_status = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?", (old,),
            ).fetchone()["Status"]
        assert r_status == "in_progress"  # recent session untouched
        assert o_status == "failed"  # old session marked failed

    def test_legacy_in_progress_is_skipped_by_default(
        self, tmp_path, monkeypatch,
    ):
        legacy = _make_session(
            status="in_progress",
            age_hours=72,
            csv="legacy.csv",
            run_id=None,
        )
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        rc = cleanup_cli.main([
            "--max-age-hours", "48",
            "--apply",
            "--scope", "operations",
        ])
        assert rc == 0
        with get_db() as conn:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?",
                (legacy,),
            ).fetchone()
        assert row["Status"] == "in_progress"

    def test_include_legacy_allows_cleanup(
        self, tmp_path, monkeypatch,
    ):
        legacy = _make_session(
            status="in_progress",
            age_hours=72,
            csv="legacy-include.csv",
            run_id=None,
        )
        monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

        rc = cleanup_cli.main([
            "--max-age-hours", "48",
            "--apply",
            "--scope", "operations",
            "--include-legacy",
        ])
        assert rc in (0, 4)
        with get_db() as conn:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?",
                (legacy,),
            ).fetchone()
        assert row["Status"] == "failed"


class TestStaleCleanupRoutesThroughRepos:
    """ADR-032 Phase 2a boundary test: run_stale_cleanup must route every
    lifecycle mutation through the public Repo methods (SessionLifecycleRepo /
    HistoryRepo) — never the raw db_* primitives. Follows the seam-pinning
    style of tests/unit/test_adr005_pr3a_repo_callers.py."""

    def test_rollback_and_resume_route_through_repos(self, monkeypatch):
        from unittest.mock import MagicMock

        # Stub out DB init / connection bookkeeping so no real DB is touched.
        monkeypatch.setattr(cleanup_cli, "_sync_db_migration_paths", lambda: None)
        monkeypatch.setattr(cleanup_cli._db_mig, "init_db", lambda: None)
        monkeypatch.setattr(cleanup_cli, "close_db", lambda: None)
        monkeypatch.setattr(cleanup_cli, "_read_session_meta", lambda sid: {"Id": sid})

        # One in_progress (→ rollback) and one finalizing/pending (→ resume).
        session_repo = MagicMock()
        session_repo.find_stale_pending_sessions.return_value = [
            ("S-INPROG", "in_progress", "pending"),
            ("S-FINAL", "finalizing", "pending"),
        ]
        session_repo.rollback_session.return_value = {"history": {}}
        history_repo = MagicMock()
        history_repo.resume_finalizing_session.return_value = {"resumed": True}

        monkeypatch.setattr(
            cleanup_cli, "SessionLifecycleRepo", lambda *a, **k: session_repo,
        )
        monkeypatch.setattr(
            cleanup_cli, "HistoryRepo", lambda *a, **k: history_repo,
        )

        # The raw db_* primitives must NOT be called directly.
        import javdb.storage.db._db_reports as reports_db
        import javdb.storage.db._db_rollback as rollback_db
        import javdb.storage.db._db_history_write as history_write_db

        def _forbidden(name):
            def _raise(*a, **k):
                raise AssertionError(f"raw db function called: {name}")
            return _raise

        monkeypatch.setattr(
            reports_db,
            "db_find_stale_pending_sessions",
            _forbidden("db_find_stale_pending_sessions"),
        )
        monkeypatch.setattr(
            rollback_db, "db_rollback_session",
            _forbidden("db_rollback_session"),
        )
        monkeypatch.setattr(
            history_write_db,
            "db_resume_finalizing_session",
            _forbidden("db_resume_finalizing_session"),
        )

        result = cleanup_cli.run_stale_cleanup(
            max_age_hours=12.0, scope="reports", dry_run=False,
        )

        # Repo seams were exercised with the exact argument mapping.
        session_repo.find_stale_pending_sessions.assert_called_once_with(
            max_age_hours=12.0, require_run_identity=True,
        )
        session_repo.rollback_session.assert_called_once_with(
            "S-INPROG",
            dry_run=False,
            scope="reports",
            force=False,
            run_started_at=None,
            failure_reason="stale_timeout",
            auto_resume_finalizing=True,
        )
        history_repo.resume_finalizing_session.assert_called_once_with("S-FINAL")

        assert result["sessions_found"] == 2
        assert result["sessions_failed"] == 0
