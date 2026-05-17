"""Unit tests for ``scripts.cleanup_stale_session_audits``.

Targets the sqlite side only — D1 needs live credentials and is exercised
through the same code path via ``--target d1`` in production.

Phase 4 contract
----------------
The script is **read-only** since Phase 4 (2026-05).  Detection still
flags ``orphan_session``, ``cross_day``, and ``committed_with_audit``
phantoms, but ``--apply`` is a deprecated alias that logs a warning and
behaves identically to ``--dry-run``.  The destructive archival path
moved to :mod:`scripts.audit_archive`.
"""

from __future__ import annotations

import json
import logging
import os
import sys

import pytest

import apps.cli.db.cleanup_stale_session_audits as cleanup
import utils.infra.db as db_mod


def _stamp_audit(session_id: int, target_id: int, *, when: str) -> None:
    with db_mod.get_db() as conn:
        conn.execute(
            "INSERT INTO MovieHistoryAudit "
            "(TargetId, Action, OldRowJson, SessionId, DateTimeCreated) "
            "VALUES (?, 'INSERT', NULL, ?, ?)",
            (target_id, session_id, when),
        )


def _create_session(csv: str, *, when: str = "2026-05-08 09:00:00") -> int:
    return db_mod.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-08",
        csv_filename=csv,
        created_at=when,
    )


@pytest.fixture
def reports_dir(tmp_path, monkeypatch) -> str:
    rd = tmp_path / "reports"
    rd.mkdir()
    monkeypatch.setenv("REPORTS_DIR", str(rd))
    return str(rd)


def _latest_report(reports_dir: str) -> dict:
    report_dir = os.path.join(
        reports_dir, "D1", "cleanup_stale_session_audits"
    )
    files = sorted(
        f for f in os.listdir(report_dir)
        if f.startswith("cleanup_stale_session_audits_")
    )
    assert files, "expected at least one report file"
    with open(os.path.join(report_dir, files[-1])) as f:
        return json.load(f)


# ── Detection ───────────────────────────────────────────────────────────


class TestDetection:
    def test_orphan_session_audit_is_flagged(self, reports_dir, monkeypatch):
        # SessionId 999999 doesn't exist in ReportSessions.
        _stamp_audit("999999", 1, when="2026-05-08 09:00:00")

        rc = cleanup.main([
            "--target", "sqlite",
            "--dry-run",
        ])
        assert rc == 0

        report = _latest_report(reports_dir)
        sqlite = next(
            r for r in report["results"] if r.get("side") == "sqlite"
        )
        flagged = sqlite["findings"]["audit"]["MovieHistoryAudit"]
        assert any(
            item["session_id"] == "999999" and item["reason"] == "orphan_session"
            for item in flagged
        )

    def test_cross_day_audit_is_flagged(self, reports_dir):
        sid = _create_session("xd.csv")
        # Two audits on the same session 30h apart → cross_day.
        _stamp_audit(sid, 1, when="2026-05-07 02:00:00")
        _stamp_audit(sid, 2, when="2026-05-08 09:00:00")

        rc = cleanup.main([
            "--target", "sqlite",
            "--cross-day-hours", "12",
            "--dry-run",
        ])
        assert rc == 0

        report = _latest_report(reports_dir)
        sqlite = next(
            r for r in report["results"] if r.get("side") == "sqlite"
        )
        flagged = sqlite["findings"]["audit"]["MovieHistoryAudit"]
        assert any(
            item["session_id"] == sid and item["reason"] == "cross_day"
            for item in flagged
        ), f"Expected cross_day flag for session {sid}; got {flagged!r}"

    def test_committed_with_audit_is_flagged(self, reports_dir):
        sid = _create_session("cw.csv")
        _stamp_audit(sid, 5, when="2026-05-08 09:00:00")
        # Mark committed but bypass the prune so the audit row stays.
        with db_mod.get_db() as conn:
            conn.execute(
                "UPDATE ReportSessions SET Status='committed' WHERE Id=?",
                (sid,),
            )

        rc = cleanup.main([
            "--target", "sqlite",
            "--dry-run",
        ])
        assert rc == 0

        report = _latest_report(reports_dir)
        sqlite = next(
            r for r in report["results"] if r.get("side") == "sqlite"
        )
        flagged = sqlite["findings"]["audit"]["MovieHistoryAudit"]
        assert any(
            item["session_id"] == sid
            and item["reason"] == "committed_with_audit"
            for item in flagged
        )

    def test_dry_run_does_not_delete(self, reports_dir):
        _stamp_audit(999998, 1, when="2026-05-08 09:00:00")

        rc = cleanup.main([
            "--target", "sqlite",
            "--dry-run",
        ])
        assert rc == 0

        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=999998"
            ).fetchone()["n"]
        assert n == 1


# ── Read-only contract (Phase 4) ───────────────────────────────────────


class TestReadOnlyContract:
    """``--apply`` is now a deprecated alias for ``--dry-run``."""

    def test_apply_does_not_delete_audit_rows(self, reports_dir):
        _stamp_audit(999997, 1, when="2026-05-08 09:00:00")

        rc = cleanup.main([
            "--target", "sqlite",
            "--apply",
        ])
        assert rc == 0

        # Phase 4 contract: audit row survives ``--apply``.  The
        # archival cron (scripts.audit_archive) is the only writer.
        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=999997"
            ).fetchone()["n"]
        assert n == 1

    def test_apply_emits_deprecation_warning(self, reports_dir, caplog):
        _stamp_audit(999996, 1, when="2026-05-08 09:00:00")

        with caplog.at_level(logging.WARNING):
            rc = cleanup.main([
                "--target", "sqlite",
                "--apply",
            ])
        assert rc == 0
        assert any(
            "deprecated" in record.getMessage().lower()
            and "audit_archive" in record.getMessage()
            for record in caplog.records
        ), f"expected deprecation warning, got {[r.getMessage() for r in caplog.records]!r}"

    def test_apply_writes_dryrun_named_report(self, reports_dir):
        # Even when ``--apply`` is the CLI arg, the readonly fallback
        # surfaces in the report filename so artifacts are easy to tag.
        _stamp_audit(999995, 1, when="2026-05-08 09:00:00")

        rc = cleanup.main([
            "--target", "sqlite",
            "--apply",
        ])
        assert rc == 0

        report_dir = os.path.join(
            reports_dir, "D1", "cleanup_stale_session_audits"
        )
        files = sorted(os.listdir(report_dir))
        assert files, "expected at least one report file"
        # ``dryrun`` token is the new default because args.dry_run is
        # always coerced to True by ``_enforce_readonly``.
        assert all("dryrun" in f for f in files), files
