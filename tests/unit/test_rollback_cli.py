"""Unit tests for ``apps.cli.rollback`` command-line behaviour.

Focused on the changes introduced in 2026-05-08:

* ``--include-orphaned`` gating of the legacy window scan
* ``--run-id`` + ``--attempt`` primary lookup path
* Cross-day reject (returns exit 2)
* Drift / orphan_pruned metric emission to ``reports/d1_drift.jsonl`` and
  ``GITHUB_OUTPUT``
* ``FailureReason`` is propagated through to ``db_rollback_session``
"""

from __future__ import annotations

import json
import os
from typing import List

import pytest

import apps.cli.rollback as rollback_cli
import utils.infra.db as db_mod


# ── helpers ──────────────────────────────────────────────────────────────


def _create_session(
    *,
    when: str = "2026-05-08 09:00:00",
    run_id: str = "r-test",
    run_attempt: int = 1,
    csv: str = "cli.csv",
    status: str = "in_progress",
) -> int:
    sid = db_mod.db_create_report_session(
        report_type="DailyReport",
        report_date="2026-05-08",
        csv_filename=csv,
        created_at=when,
        run_id=run_id,
        run_attempt=run_attempt,
    )
    if status != "in_progress":
        with db_mod.get_db() as conn:
            conn.execute(
                "UPDATE ReportSessions SET Status=? WHERE Id=?",
                (status, sid),
            )
    return sid


@pytest.fixture
def reports_dir(monkeypatch, tmp_path) -> str:
    rd = tmp_path / "reports"
    rd.mkdir()
    monkeypatch.setenv("REPORTS_DIR", str(rd))
    return str(rd)


# ── --include-orphaned gating ────────────────────────────────────────────


class TestIncludeOrphanedGating:
    def test_default_does_not_expand_window(self, reports_dir):
        # Two sessions in the same window: one explicitly targeted, one
        # that legacy --run-started-at would have swept up.
        target = _create_session(csv="t.csv")
        sweepable = _create_session(csv="s.csv")  # noqa: F841

        rc = rollback_cli.main([
            "--session-id", str(target),
            "--run-started-at", "2026-05-08T08:00:00Z",
            "--apply",
            "--scope", "reports",
        ])
        assert rc in (0, 4)

        # ReportSessions row for sweepable must still exist.
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions WHERE CsvFilename='s.csv'"
            ).fetchone()
        assert row["n"] == 1

    def test_include_orphaned_brings_window_scan_back(self, reports_dir):
        target = _create_session(csv="ti.csv")
        sweepable = _create_session(csv="si.csv")

        rc = rollback_cli.main([
            "--session-id", str(target),
            "--run-started-at", "2026-05-08T08:00:00Z",
            "--include-orphaned",
            "--apply",
            "--scope", "reports",
        ])
        assert rc in (0, 4)

        with db_mod.get_db() as conn:
            t_left = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions WHERE CsvFilename='ti.csv'"
            ).fetchone()["n"]
            s_left = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions WHERE CsvFilename='si.csv'"
            ).fetchone()["n"]
        assert t_left == 0
        assert s_left == 0


# ── Cross-day reject ─────────────────────────────────────────────────────


class TestCrossDayReject:
    def test_session_predating_window_returns_exit_2(self, reports_dir):
        old = _create_session(when="2026-05-06 09:00:00", csv="old.csv")

        rc = rollback_cli.main([
            "--session-id", str(old),
            "--run-started-at", "2026-05-08T09:00:00Z",
            "--apply",
        ])
        assert rc == 2

        # Old session must still exist.
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions WHERE Id=?",
                (old,),
            ).fetchone()
        assert row["n"] == 1

    def test_force_overrides_cross_day_reject(self, reports_dir):
        old = _create_session(when="2026-05-06 09:00:00", csv="old2.csv")

        rc = rollback_cli.main([
            "--session-id", str(old),
            "--run-started-at", "2026-05-08T09:00:00Z",
            "--apply",
            "--force",
        ])
        assert rc in (0, 4)


# ── --run-id primary path ────────────────────────────────────────────────


class TestRunIdPrimaryPath:
    def test_run_id_attempt_finds_owned_sessions(self, reports_dir):
        a = _create_session(run_id="r-X", run_attempt=1, csv="rx1.csv")
        b = _create_session(run_id="r-X", run_attempt=1, csv="rx2.csv")
        c = _create_session(run_id="r-Y", run_attempt=1, csv="ry1.csv")  # noqa: F841

        rc = rollback_cli.main([
            "--run-id", "r-X",
            "--attempt", "1",
            "--apply",
            "--scope", "reports",
        ])
        assert rc in (0, 4)

        with db_mod.get_db() as conn:
            x_left = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions WHERE RunId='r-X'"
            ).fetchone()["n"]
            y_left = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions WHERE RunId='r-Y'"
            ).fetchone()["n"]
        assert x_left == 0
        assert y_left == 1


# ── Metrics emission ─────────────────────────────────────────────────────


class TestMetricsEmission:
    def test_metric_appended_to_d1_drift_jsonl(
        self, reports_dir, monkeypatch,
    ):
        sid = _create_session(csv="metric.csv")
        rollback_cli.main([
            "--session-id", str(sid),
            "--apply",
            "--scope", "reports",
        ])

        path = os.path.join(reports_dir, "D1", "d1_drift.jsonl")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            lines = [
                json.loads(line) for line in f if line.strip()
            ]
        assert any(
            r.get("kind") == "rollback_summary" for r in lines
        ), "Expected rollback_summary metric in d1_drift.jsonl"

    def test_github_output_contains_drift_total(
        self, reports_dir, tmp_path, monkeypatch,
    ):
        gh = tmp_path / "github_output.txt"
        gh.write_text("")
        monkeypatch.setenv("GITHUB_OUTPUT", str(gh))

        sid = _create_session(csv="metric2.csv")
        rollback_cli.main([
            "--session-id", str(sid),
            "--apply",
            "--scope", "reports",
        ])

        contents = gh.read_text()
        assert "drift_total=" in contents
        assert "orphan_pruned_total=" in contents


# ── FailureReason propagation ────────────────────────────────────────────


class TestFailureReasonFromCli:
    def test_failure_reason_arg_passed_through(self, reports_dir):
        sid = _create_session(csv="fr.csv")
        rc = rollback_cli.main([
            "--session-id", str(sid),
            "--apply",
            "--scope", "operations",
            "--failure-reason", "manual_test",
        ])
        # operations scope only, so the ReportSessions row remains.
        assert rc in (0, 4)
        with db_mod.get_db() as conn:
            row = conn.execute(
                "SELECT FailureReason FROM ReportSessions WHERE Id=?",
                (sid,),
            ).fetchone()
        assert row["FailureReason"] == "manual_test"
