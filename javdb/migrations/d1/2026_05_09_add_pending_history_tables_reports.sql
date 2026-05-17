-- 2026-05-09: Add ReportSessions.WriteMode column for Phase 0 of the
--   Ingestion Perfect Rollback project (see
--   /Users/tedwu/.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=migration/d1/2026_05_09_add_pending_history_tables_reports.sql
--
-- Values:
--   'audit'   — legacy X3 audit replay path (default; current behaviour
--               for every existing session and every new session unless
--               the env var ``JAVDB_HISTORY_WRITE_MODE=pending`` is set).
--   'pending' — Phase 2 path: writes go into PendingMovie/TorrentHistory
--               Writes; ``apps.cli.commit_session`` promotes them into
--               the live tables; ``apps.cli.rollback`` deletes pending
--               rows on ``in_progress`` failure or resumes the commit
--               on ``finalizing`` failure.
--
-- Status state machine is also extended (no DDL change needed because
-- the column has no CHECK constraint):
--   in_progress → finalizing → committed
--   in_progress → failed
--   finalizing → finalizing → committed (idempotent resume)
--
-- WriteMode is per-session so DailyIngestion (audit) can run alongside
-- TestIngestion (pending) without either workflow disturbing the other.

ALTER TABLE ReportSessions
    ADD COLUMN WriteMode TEXT NOT NULL DEFAULT 'audit';

CREATE INDEX IF NOT EXISTS idx_report_sessions_write_mode
    ON ReportSessions(WriteMode, Status);
