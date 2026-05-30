-- 2026-05-29: Add event-spine tables (ADR-036 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_pipeline_event.sql
--
-- Additive, append-only. Does NOT change the authoritative pending->commit path.

-- PipelineEvent columns: seq = global monotonic order; entity_type is one of
-- session | movie | torrent; payload is JSON. (Inline comments are kept OUT of
-- the CREATE TABLE body so the migration-coverage test's column parser, which
-- splits on commas, does not read a comment as a column name.)
CREATE TABLE IF NOT EXISTS PipelineEvent (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT NOT NULL,
  run_id       TEXT,
  run_attempt  INTEGER,
  event_type   TEXT NOT NULL,
  entity_type  TEXT NOT NULL,
  entity_id    TEXT,
  payload      TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipeline_event_session ON PipelineEvent(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_pipeline_event_type ON PipelineEvent(event_type, seq);

CREATE TABLE IF NOT EXISTS EventConsumerCursor (
  consumer   TEXT PRIMARY KEY,
  last_seq   INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

-- Demonstrator projection: per (session_id, event_type) counts.
CREATE TABLE IF NOT EXISTS RunEventSummary (
  session_id  TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  count       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (session_id, event_type)
);
