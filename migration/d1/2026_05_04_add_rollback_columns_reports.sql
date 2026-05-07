-- 2026-05-04: Add Status column to ReportSessions for X3 rollback tracking.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=migration/d1/2026_05_04_add_rollback_columns_reports.sql
--
-- Values:
--   'in_progress' (default for new rows; default for legacy rows is NULL,
--                  which the rollback CLI treats as "not eligible" so old
--                  data is never disturbed)
--   'committed'   (set by apps.cli.commit_session at end of run-pipeline)
--   'failed'      (set by apps.cli.rollback before deletion, for debug)

ALTER TABLE ReportSessions ADD COLUMN Status TEXT DEFAULT 'in_progress';

CREATE INDEX IF NOT EXISTS idx_report_sessions_status
    ON ReportSessions(Status, DateTimeCreated);
