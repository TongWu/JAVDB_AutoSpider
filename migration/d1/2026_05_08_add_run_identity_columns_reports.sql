-- 2026-05-08: Add (RunId, RunAttempt, FailureReason) to ReportSessions so a
-- rollback can be scoped by GitHub Actions workflow run rather than by the
-- application-generated SessionId alone.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=migration/d1/2026_05_08_add_run_identity_columns_reports.sql
--
-- Background:
--   Before this migration, the only way to find an in-progress session
--   for cleanup was a date-window match on DateTimeCreated. That misfired
--   on 2026-05-08 (see logs/rollback.log analysis) when two concurrent
--   sessions (332 and 346) both fell into the same window and the
--   sqlite-vs-D1 lastrowid drift made one row look like the other.
--
--   With (RunId, RunAttempt) attached, rollback CLI can address the
--   exact session(s) created by a particular workflow run, regardless
--   of any id drift between the two backends.
--
-- Columns are NULLABLE so legacy rows remain valid.

ALTER TABLE ReportSessions ADD COLUMN RunId         TEXT;
ALTER TABLE ReportSessions ADD COLUMN RunAttempt    INTEGER;
ALTER TABLE ReportSessions ADD COLUMN FailureReason TEXT;

CREATE INDEX IF NOT EXISTS idx_report_sessions_run
    ON ReportSessions(RunId, RunAttempt);
