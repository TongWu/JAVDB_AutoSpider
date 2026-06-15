-- 2026-05-29: Add ContentFilterRule table (ADR-040 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql
--
-- Dynamic content-filter rules applied after detail parse. Additive: no rows = no change.
-- The (dimension, mode, value) triple is generic; new dimensions/modes reuse it
-- without a schema change (age = ADR-040 Phase 2; regex/release_date = WS4a).
-- dimension: actor | tag | gender | age | release_date
-- mode: exclude | include | require_lead | exclude_all_male | min_age | max_age
--       | regex_exclude | regex_include | before | after
-- value: actor name/href | tag | gender | integer age | ISO date (YYYY-MM-DD)
--        | regex pattern

CREATE TABLE IF NOT EXISTS ContentFilterRule (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  dimension  TEXT NOT NULL,
  mode       TEXT NOT NULL,
  value      TEXT,
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_content_filter_enabled ON ContentFilterRule(enabled, dimension);
