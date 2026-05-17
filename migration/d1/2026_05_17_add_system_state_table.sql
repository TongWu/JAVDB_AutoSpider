-- 2026-05-17: Add system_state KV table (mirrors SQLite _OPERATIONS_DDL change).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=migration/d1/2026_05_17_add_system_state_table.sql
--
-- Mirror table used by:
--   - apps/api/routers/onboarding.py (onboarded flag, dismissed_hints)
--   - apps/api/routers/system_state.py (generic KV)

CREATE TABLE IF NOT EXISTS system_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_system_state_updated_at ON system_state(updated_at);
