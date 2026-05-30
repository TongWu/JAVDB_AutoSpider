-- 2026-05-29: Add AcquisitionOutcome table (ADR-033 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_acquisition_outcome.sql
--
-- AcquisitionOutcome records the real fate of each selected torrent after qB.
-- It is enrichment: written off the Pending->Commit path, idempotent UPSERT
-- by qb_hash. session_id is provenance only (the run that queued the torrent).

CREATE TABLE IF NOT EXISTS AcquisitionOutcome (
  qb_hash       TEXT PRIMARY KEY,
  href          TEXT NOT NULL DEFAULT '',
  video_code    TEXT,
  category      TEXT,
  state         TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','downloading','completed','in_library','stalled','failed')),
  queued_at     TEXT,
  completed_at  TEXT,
  landed_at     TEXT,
  last_seen_at  TEXT,
  session_id    TEXT
);

CREATE INDEX IF NOT EXISTS idx_acq_outcome_state ON AcquisitionOutcome(state);
CREATE INDEX IF NOT EXISTS idx_acq_outcome_video_code ON AcquisitionOutcome(video_code);
CREATE INDEX IF NOT EXISTS idx_acq_outcome_session ON AcquisitionOutcome(session_id);
CREATE INDEX IF NOT EXISTS idx_acq_outcome_last_seen ON AcquisitionOutcome(last_seen_at);
