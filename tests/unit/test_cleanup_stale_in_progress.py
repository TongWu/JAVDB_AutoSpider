"""Unit tests for ``apps.cli.cleanup_stale_in_progress``."""

from __future__ import annotations

from datetime import datetime, timedelta

import apps.cli.db.cleanup_stale_in_progress as cleanup_cli
import utils.infra.db as db_mod


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
    sid = db_mod.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-08",
        csv_filename=csv,
        created_at=when,
        run_id=run_id,
    )
    if status != "in_progress":
        with db_mod.get_db() as conn:
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

        with db_mod.get_db() as conn:
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
        with db_mod.get_db() as conn:
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
        with db_mod.get_db() as conn:
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
        with db_mod.get_db() as conn:
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
        with db_mod.get_db() as conn:
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
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?",
                (legacy,),
            ).fetchone()
        assert row["Status"] == "failed"
