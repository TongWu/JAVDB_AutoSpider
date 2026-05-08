"""Unit tests for the spider self-check that prevents duplicate per-run sessions.

The full spider entrypoint (``packages.python.javdb_spider.app.run_service``)
pulls in a lot of network / config infrastructure, so this file exercises
the self-check helper directly and asserts the contract:

* When ``GITHUB_RUN_ID`` already owns an in-progress session, creating a
  fresh one is refused.
* When the helper sees no prior session for the run, the second creation
  proceeds.
"""

from __future__ import annotations

import utils.infra.db as db_mod


class TestSelfCheckHelper:
    def test_zero_for_unknown_run(self):
        assert (
            db_mod.db_count_in_progress_sessions_for_run(
                "never-seen", 1,
            )
            == 0
        )

    def test_count_one_after_creation(self):
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="ssch1.csv",
            run_id="run-X",
            run_attempt=1,
        )
        assert (
            db_mod.db_count_in_progress_sessions_for_run("run-X", 1) == 1
        )

    def test_committed_session_is_excluded(self):
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="ssch2.csv",
            run_id="run-Y",
            run_attempt=1,
        )
        db_mod.db_mark_session_committed(sid)
        # After commit, the session is no longer in_progress, so
        # the count must be zero — meaning a retry of the same run
        # could legitimately create a fresh session.
        assert (
            db_mod.db_count_in_progress_sessions_for_run("run-Y", 1) == 0
        )

    def test_attempt_isolation(self):
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="ssch3.csv",
            run_id="run-Z",
            run_attempt=1,
        )
        # Same run id but a different attempt — independent count.
        assert (
            db_mod.db_count_in_progress_sessions_for_run("run-Z", 1) == 1
        )
        assert (
            db_mod.db_count_in_progress_sessions_for_run("run-Z", 2) == 0
        )

    def test_run_id_only_aggregates_across_attempts(self):
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="ssch4a.csv",
            run_id="run-W",
            run_attempt=1,
        )
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="ssch4b.csv",
            run_id="run-W",
            run_attempt=2,
        )
        # No attempt argument → aggregates.
        assert db_mod.db_count_in_progress_sessions_for_run("run-W") == 2
