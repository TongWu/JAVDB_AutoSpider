-- 2026-06-03: Add ReportSessions.CommittedAt for ADR-047 summary parity.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_03_add_reports_committed_at.sql
--
-- The Python /summary endpoint computes avg_duration_seconds from
-- DateTimeCreated -> CommittedAt, matching the TypeScript backend contract.
-- Existing committed sessions remain NULL and are ignored by the average.

ALTER TABLE ReportSessions ADD COLUMN CommittedAt TEXT;
