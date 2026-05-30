-- 2026-05-29: Add event-spine tables (ADR-036 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_pipeline_event.sql
--
-- Additive, append-only. Does NOT change the authoritative pending->commit path.

CREATE TABLE IF NOT EXISTS PipelineEvent (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,  -- global monotonic order
  session_id   TEXT NOT NULL,
  run_id       TEXT,
  run_attempt  INTEGER,
  event_type   TEXT NOT NULL,
  entity_type  TEXT NOT NULL,   -- session | movie | torrent
  entity_id    TEXT,
  payload      TEXT,            -- JSON
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
