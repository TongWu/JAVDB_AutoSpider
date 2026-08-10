-- 0042: system_state KV table (the migration ledger's own home).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/0042_system_state_table.sql
--
-- Header added so the admin migration runner can resolve a target database.
-- This is the bootstrap migration: it CREATEs the very table the runner reads
-- its applied-migration ledger from, so the runner treats an absent
-- system_state as "nothing applied" rather than refusing.

CREATE TABLE IF NOT EXISTS system_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_system_state_updated_at ON system_state(updated_at);
