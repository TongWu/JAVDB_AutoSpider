-- 2026-05-08: Add (RunId, RunAttempt) to MovieHistoryAudit / TorrentHistoryAudit
-- so audit rows can be reverse-applied by GitHub Actions workflow run id
-- (cross-check + primary key) instead of being addressed solely by SessionId.
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=migration/d1/2026_05_08_add_run_identity_columns_history.sql
--
-- See migration/d1/2026_05_08_add_run_identity_columns_reports.sql for the
-- broader rationale. Columns are NULLABLE so legacy audit rows continue
-- to work; the rollback CLI falls back to SessionId-based queries when
-- RunId is NULL.

ALTER TABLE MovieHistoryAudit   ADD COLUMN RunId      TEXT;
ALTER TABLE MovieHistoryAudit   ADD COLUMN RunAttempt INTEGER;
ALTER TABLE TorrentHistoryAudit ADD COLUMN RunId      TEXT;
ALTER TABLE TorrentHistoryAudit ADD COLUMN RunAttempt INTEGER;

CREATE INDEX IF NOT EXISTS idx_mh_audit_run ON MovieHistoryAudit(RunId, RunAttempt);
CREATE INDEX IF NOT EXISTS idx_th_audit_run ON TorrentHistoryAudit(RunId, RunAttempt);
