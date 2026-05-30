-- 2026-05-29: Add ParseRunFieldFill table (ADR-035 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_parse_run_field_fill.sql
--
-- Per (session_id, page_type, field): the fill-rate observed during that run's
-- live parse. The commit gate reads these; the soft-field baseline is the median
-- over recent rows where committed=1. Enrichment, off the Pending->Commit path.

CREATE TABLE IF NOT EXISTS ParseRunFieldFill (
  session_id    TEXT NOT NULL,
  page_type     TEXT NOT NULL,
  field         TEXT NOT NULL,
  fill_rate     REAL NOT NULL,
  sample_count  INTEGER NOT NULL,
  committed     INTEGER NOT NULL DEFAULT 0,
  observed_at   TEXT,
  PRIMARY KEY (session_id, page_type, field)
);

CREATE INDEX IF NOT EXISTS idx_prff_field_committed
  ON ParseRunFieldFill(page_type, field, committed, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_prff_session ON ParseRunFieldFill(session_id);
