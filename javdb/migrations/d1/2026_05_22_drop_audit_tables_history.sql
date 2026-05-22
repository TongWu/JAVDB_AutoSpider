-- 2026-05-22: Drop MovieHistoryAudit / TorrentHistoryAudit tables (ADR-005 PR-4).
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_05_22_drop_audit_tables_history.sql
--
-- Audit mode has been retired. All history writes now use Pending Mode
-- exclusively. The audit tables and their indexes are no longer written
-- to or read from by any production code path.

-- Drop indexes first (D1 requires explicit DROP INDEX before DROP TABLE).
DROP INDEX IF EXISTS idx_mh_audit_session;
DROP INDEX IF EXISTS idx_mh_audit_run;
DROP INDEX IF EXISTS idx_th_audit_session;
DROP INDEX IF EXISTS idx_th_audit_run;

-- Drop audit tables.
DROP TABLE IF EXISTS MovieHistoryAudit;
DROP TABLE IF EXISTS TorrentHistoryAudit;

-- Bump schema version to 14.
UPDATE SchemaVersion SET Version = 14;
