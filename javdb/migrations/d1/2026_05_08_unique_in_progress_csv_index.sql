-- 2026-05-08 (evening) — schema-level invariant against same-CSV double-INSERT.
--
-- Background: the spider self-check used to refuse any second in-progress
-- session that shared (RunId, RunAttempt) with an existing one.  That
-- assumption is wrong: DailyIngestion / AdHocIngestion legitimately run
-- multiple spider invocations in the same GitHub run (different CSVs), so
-- the application-layer guard had to be relaxed.  The *real* invariant we
-- want is: "no two in-progress sessions ever share the same
-- (RunId, RunAttempt, CsvFilename)" — a true duplicate caused by re-entry
-- or dual-write lastrowid drift.
--
-- This migration enforces that invariant at the DB layer via a partial
-- UNIQUE index, so violations fail with IntegrityError regardless of
-- application path.  The existing `idx_report_sessions_run` plain index
-- stays for query lookups.
--
-- Scope of the partial predicate:
--   * Status = 'in_progress'  → resolved (committed/failed) sessions can
--     legitimately reuse the same CSV in a future attempt.
--   * RunId IS NOT NULL       → keeps legacy / local-dev rows out so the
--     migration is fully backwards compatible.
--
-- Idempotent:
CREATE UNIQUE INDEX IF NOT EXISTS uq_reportsessions_runidentity_csv
    ON ReportSessions(RunId, RunAttempt, CsvFilename)
    WHERE Status = 'in_progress' AND RunId IS NOT NULL;
