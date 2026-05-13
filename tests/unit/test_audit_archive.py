"""Unit tests for ``scripts.audit_archive``.

Phase 4 (Ingestion Perfect Rollback): the audit-archive cron prunes
``MovieHistoryAudit`` / ``TorrentHistoryAudit`` rows whose owning
``ReportSessions`` row is older than ``--older-than-days`` (default 30
days) and either committed, failed, in_progress/finalizing past the
threshold, or orphaned.  These tests cover the SQLite side only — D1 is
exercised through the same code path in production via ``--target d1``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import scripts.audit_archive as audit_archive
import utils.infra.db as db_mod


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def reports_dir(tmp_path, monkeypatch) -> str:
    rd = tmp_path / "reports"
    rd.mkdir()
    monkeypatch.setenv("REPORTS_DIR", str(rd))
    return str(rd)


def _stamp_audit(
    session_id: str,
    target_id: int,
    *,
    when: str,
    table: str = "MovieHistoryAudit",
) -> None:
    with db_mod.get_db() as conn:
        conn.execute(
            f"INSERT INTO {table} "
            "(TargetId, Action, OldRowJson, SessionId, DateTimeCreated) "
            "VALUES (?, 'INSERT', NULL, ?, ?)",
            (target_id, session_id, when),
        )


def _create_session(
    csv: str,
    *,
    when: str = "2026-04-01 09:00:00",
    status: str = "committed",
) -> str:
    sid = db_mod.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-04-01",
        csv_filename=csv,
        created_at=when,
    )
    if status != "in_progress":
        with db_mod.get_db() as conn:
            conn.execute(
                "UPDATE ReportSessions SET Status=? WHERE Id=?",
                (status, sid),
            )
    return sid


def _latest_report(reports_dir: str) -> dict:
    report_dir = os.path.join(reports_dir, "D1", "audit_archive")
    files = sorted(
        f for f in os.listdir(report_dir)
        if f.startswith("audit_archive_")
    )
    assert files, "expected at least one audit-archive report"
    with open(os.path.join(report_dir, files[-1])) as f:
        return json.load(f)


def _sqlite_result(report: dict) -> dict:
    return next(
        r for r in report["results"] if r.get("side") == "sqlite"
    )


# ── Decision logic ─────────────────────────────────────────────────────


class TestDecision:
    """Direct unit tests on :func:`audit_archive._classify`."""

    def _now(self) -> datetime:
        return datetime(2026, 5, 13, tzinfo=timezone.utc)

    def test_committed_older_than_window_is_archived(self):
        cutoff = self._now() - timedelta(days=30)
        decision = audit_archive._classify(
            "sid-1",
            {
                "count": 3,
                "first_at": "2026-04-01 09:00:00",
                "last_at": "2026-04-05 09:00:00",
            },
            {"sid-1": "committed"},
            cutoff,
        )
        assert decision is not None
        assert decision["reason"] == "committed_expired"
        assert decision["row_count"] == 3

    def test_orphan_session_older_than_window_is_archived(self):
        cutoff = self._now() - timedelta(days=30)
        decision = audit_archive._classify(
            "sid-orphan",
            {
                "count": 5,
                "first_at": "2026-03-01 09:00:00",
                "last_at": "2026-03-05 09:00:00",
            },
            {},  # owning session not present
            cutoff,
        )
        assert decision is not None
        assert decision["reason"] == "orphan_session"

    def test_recent_session_is_skipped(self):
        cutoff = self._now() - timedelta(days=30)
        decision = audit_archive._classify(
            "sid-recent",
            {
                "count": 2,
                "first_at": "2026-05-10 09:00:00",
                "last_at": "2026-05-12 09:00:00",
            },
            {"sid-recent": "committed"},
            cutoff,
        )
        assert decision is None

    def test_in_progress_past_cutoff_is_archived_as_expired(self):
        cutoff = self._now() - timedelta(days=30)
        decision = audit_archive._classify(
            "sid-stuck",
            {
                "count": 1,
                "first_at": "2026-04-01 09:00:00",
                "last_at": "2026-04-05 09:00:00",
            },
            {"sid-stuck": "in_progress"},
            cutoff,
        )
        assert decision is not None
        assert decision["reason"] == "in_progress_expired"

    def test_unparseable_timestamp_is_skipped(self):
        cutoff = self._now() - timedelta(days=30)
        decision = audit_archive._classify(
            "sid-bad",
            {
                "count": 1,
                "first_at": None,
                "last_at": "not-a-timestamp",
            },
            {"sid-bad": "committed"},
            cutoff,
        )
        assert decision is None


# ── Dry-run ────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_reports_pending_sessions(self, reports_dir):
        sid = _create_session("dry.csv", when="2026-03-01 09:00:00")
        _stamp_audit(sid, 1, when="2026-03-01 09:00:00")
        _stamp_audit(sid, 2, when="2026-03-01 09:00:00",
                     table="TorrentHistoryAudit")

        rc = audit_archive.main([
            "--target", "sqlite",
            "--older-than-days", "30",
            "--dry-run",
        ])
        assert rc == 0

        # Audit rows must still be there after a dry-run.
        with db_mod.get_db() as conn:
            n_m = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
            n_t = conn.execute(
                "SELECT COUNT(*) AS n FROM TorrentHistoryAudit "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert n_m == 1 and n_t == 1

        report = _latest_report(reports_dir)
        sqlite = _sqlite_result(report)
        plan = sqlite["plan"]
        assert plan["summary"]["rows_total"] == 2
        movie_items = plan["tables"]["MovieHistoryAudit"]
        torrent_items = plan["tables"]["TorrentHistoryAudit"]
        assert any(item["session_id"] == sid for item in movie_items)
        assert any(item["session_id"] == sid for item in torrent_items)


# ── Apply ──────────────────────────────────────────────────────────────


class TestApply:
    def test_apply_deletes_committed_audit_rows_past_cutoff(
        self, reports_dir,
    ):
        sid = _create_session("apply.csv", when="2026-03-01 09:00:00")
        _stamp_audit(sid, 11, when="2026-03-01 09:00:00")
        _stamp_audit(sid, 12, when="2026-03-01 09:00:00",
                     table="TorrentHistoryAudit")

        rc = audit_archive.main([
            "--target", "sqlite",
            "--older-than-days", "30",
            "--apply",
        ])
        assert rc == 0

        with db_mod.get_db() as conn:
            n_m = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
            n_t = conn.execute(
                "SELECT COUNT(*) AS n FROM TorrentHistoryAudit "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert (n_m, n_t) == (0, 0)

    def test_apply_skips_recent_committed_sessions(self, reports_dir):
        # ``last_at`` is younger than the cutoff so this session must
        # survive even after ``--apply``.  A committed session that just
        # finished still has ``DateTimeCreated`` near now() — its audit
        # rows may legitimately be re-read by a manual --force rollback.
        sid = _create_session("recent.csv", when="2026-05-10 09:00:00")
        _stamp_audit(sid, 21, when="2026-05-10 09:00:00")

        rc = audit_archive.main([
            "--target", "sqlite",
            "--older-than-days", "30",
            "--apply",
        ])
        assert rc == 0

        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId=?",
                (sid,),
            ).fetchone()["n"]
        assert n == 1

    def test_apply_deletes_orphan_sessions(self, reports_dir):
        # No matching ReportSessions row — must be treated as orphaned.
        _stamp_audit("9999999", 31, when="2026-02-01 09:00:00")

        rc = audit_archive.main([
            "--target", "sqlite",
            "--older-than-days", "30",
            "--apply",
        ])
        assert rc == 0

        with db_mod.get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM MovieHistoryAudit "
                "WHERE SessionId='9999999'"
            ).fetchone()["n"]
        assert n == 0


# ── Safety ─────────────────────────────────────────────────────────────


class TestSafety:
    def test_apply_refuses_when_storage_backend_is_dual(
        self, reports_dir, monkeypatch,
    ):
        monkeypatch.setenv("STORAGE_BACKEND", "dual")
        with pytest.raises(SystemExit) as exc:
            audit_archive.main([
                "--target", "sqlite",
                "--apply",
            ])
        assert exc.value.code == 2

    def test_dry_run_allowed_with_dual_backend(
        self, reports_dir, monkeypatch,
    ):
        monkeypatch.setenv("STORAGE_BACKEND", "dual")
        # Dry-run is read-only, so the guard does not apply.
        rc = audit_archive.main([
            "--target", "sqlite",
            "--dry-run",
        ])
        assert rc == 0
