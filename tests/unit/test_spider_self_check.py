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

import sqlite3

import pytest

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


class TestCsvScopedDuplicateCheck:
    """Cover the CSV-scoped self-check the spider entrypoint relies on.

    Behaviour (relaxed 2026-05-08 evening):
      * Duplicate (RunId, RunAttempt, CSVFilename) → fatal.
      * Same (RunId, RunAttempt) but different CSV → benign sibling,
        helper returns empty list so the spider only emits a warning.
    """

    def test_returns_existing_id_when_csv_matches(self):
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="csv-dup.csv",
            run_id="run-DUP",
            run_attempt=1,
        )
        ids = db_mod.db_find_in_progress_session_ids_for_run_csv(
            "run-DUP", 1, "csv-dup.csv",
        )
        assert ids == [sid]

    def test_returns_empty_for_sibling_with_different_csv(self):
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="sibling-A.csv",
            run_id="run-SIB",
            run_attempt=1,
        )
        ids = db_mod.db_find_in_progress_session_ids_for_run_csv(
            "run-SIB", 1, "sibling-B.csv",
        )
        assert ids == []
        # ``count_in_progress_sessions_for_run`` still sees the sibling,
        # which is what powers the warning log line.
        assert (
            db_mod.db_count_in_progress_sessions_for_run("run-SIB", 1)
            == 1
        )

    def test_committed_session_does_not_block_csv_reuse(self):
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="csv-reuse.csv",
            run_id="run-REUSE",
            run_attempt=1,
        )
        db_mod.db_mark_session_committed(sid)
        ids = db_mod.db_find_in_progress_session_ids_for_run_csv(
            "run-REUSE", 1, "csv-reuse.csv",
        )
        assert ids == []

    def test_partial_unique_index_blocks_same_csv_double_insert(self):
        """Schema-level invariant: same (RunId, RunAttempt, CsvFilename) +
        Status='in_progress' → IntegrityError on the second INSERT.
        This is the root-cause fix that makes the application-layer
        self-check defence-in-depth rather than the only line of defence.
        """
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="uq-block.csv",
            run_id="run-UQ",
            run_attempt=1,
        )
        assert sid > 0

        # A bypass-the-Python-helper raw INSERT that would have created
        # a second in-progress row for the same CSV must now be refused
        # by the partial UNIQUE index.
        with db_mod.get_db() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO ReportSessions ("
                    "Id, ReportType, ReportDate, CsvFilename, "
                    "DateTimeCreated, Status, RunId, RunAttempt) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sid + 1,
                        "DailyReport",
                        "2026-05-08",
                        "uq-block.csv",
                        "2026-05-08 22:00:00",
                        "in_progress",
                        "run-UQ",
                        1,
                    ),
                )

    def test_partial_unique_index_allows_sibling_csv(self):
        """Different CSV in the same (RunId, RunAttempt) is legitimate
        (DailyIngestion runs multiple spiders) — the partial index must
        not block it.
        """
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="uq-sibling-A.csv",
            run_id="run-UQ-SIB",
            run_attempt=1,
        )
        # Should succeed — different CSV.
        sid_b = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="uq-sibling-B.csv",
            run_id="run-UQ-SIB",
            run_attempt=1,
        )
        assert sid_b > 0

    def test_partial_unique_index_allows_csv_reuse_after_resolution(self):
        """The partial WHERE clause excludes resolved sessions, so the
        same CSV can be re-ingested after the previous attempt was
        committed (or failed and rolled back).
        """
        sid = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="uq-after-commit.csv",
            run_id="run-UQ-RC",
            run_attempt=1,
        )
        db_mod.db_mark_session_committed(sid)
        # Same CSV, *next* attempt — must succeed.
        sid2 = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="uq-after-commit.csv",
            run_id="run-UQ-RC",
            run_attempt=2,
        )
        assert sid2 > 0
        # And the same CSV in the *same* attempt also works once the
        # previous one is resolved.
        db_mod.db_mark_session_committed(sid2)
        sid3 = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="uq-after-commit.csv",
            run_id="run-UQ-RC",
            run_attempt=2,
        )
        assert sid3 > 0

    def test_partial_unique_index_ignores_legacy_null_runid(self):
        """Legacy rows with RunId IS NULL are intentionally excluded —
        the migration must remain backwards-compatible.
        """
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="uq-legacy.csv",
            run_id=None,
            run_attempt=None,
        )
        # A second null-RunId row with the same CSV is allowed (legacy
        # contract) — the index doesn't apply.
        sid2 = db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="uq-legacy.csv",
            run_id=None,
            run_attempt=None,
        )
        assert sid2 > 0

    def test_attempt_isolation_for_csv_check(self):
        db_mod.db_create_report_session(
            report_type="DailyReport",
            report_date="2026-05-08",
            csv_filename="csv-att.csv",
            run_id="run-ATT",
            run_attempt=1,
        )
        # Same csv on a different attempt is treated as fresh.
        assert (
            db_mod.db_find_in_progress_session_ids_for_run_csv(
                "run-ATT", 2, "csv-att.csv",
            )
            == []
        )
