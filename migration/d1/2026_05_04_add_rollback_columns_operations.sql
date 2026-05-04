-- 2026-05-04: Add SessionId column to operations tables for X3 rollback.
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=migration/d1/2026_05_04_add_rollback_columns_operations.sql
--
-- The columns are NULLABLE so existing rows (SessionId IS NULL) remain
-- valid; rollback CLI only targets rows whose SessionId matches the run.

ALTER TABLE PikpakHistory              ADD COLUMN SessionId INTEGER;
ALTER TABLE DedupRecords               ADD COLUMN SessionId INTEGER;
ALTER TABLE InventoryAlignNoExactMatch ADD COLUMN SessionId INTEGER;

CREATE INDEX IF NOT EXISTS idx_pikpak_history_session  ON PikpakHistory(SessionId);
CREATE INDEX IF NOT EXISTS idx_dedup_records_session   ON DedupRecords(SessionId);
CREATE INDEX IF NOT EXISTS idx_align_no_match_session  ON InventoryAlignNoExactMatch(SessionId);
