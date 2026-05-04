-- 2026-05-04: Add X3 rollback columns + audit tables to history DB.
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=migrations/d1/2026_05_04_add_rollback_columns_history.sql
--
-- The columns are NULLABLE so existing rows (SessionId IS NULL) remain
-- valid; rollback CLI only targets rows whose SessionId matches the run.
-- Audit tables are independent from main tables and can be dropped to
-- revert without losing data.

ALTER TABLE MovieHistory   ADD COLUMN SessionId INTEGER;
ALTER TABLE TorrentHistory ADD COLUMN SessionId INTEGER;

CREATE INDEX IF NOT EXISTS idx_movie_history_session   ON MovieHistory(SessionId);
CREATE INDEX IF NOT EXISTS idx_torrent_history_session ON TorrentHistory(SessionId);

CREATE TABLE IF NOT EXISTS MovieHistoryAudit (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId INTEGER NOT NULL,
    DateTimeCreated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mh_audit_session ON MovieHistoryAudit(SessionId, Id);

CREATE TABLE IF NOT EXISTS TorrentHistoryAudit (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId INTEGER NOT NULL,
    DateTimeCreated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_th_audit_session ON TorrentHistoryAudit(SessionId, Id);
