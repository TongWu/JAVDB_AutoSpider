-- 2026-06-10: Add AcquisitionOutcomeShadow table (ADR-036 Phase 2).
-- Write-Class: diagnostic
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql
--
-- Shadow projection driven by TorrentQueued+TorrentCompleted events.
-- For cross-validation against authoritative AcquisitionOutcome only.
-- Never read by production decisions.

CREATE TABLE IF NOT EXISTS AcquisitionOutcomeShadow (
  qb_hash      TEXT PRIMARY KEY NOT NULL,
  href         TEXT NOT NULL DEFAULT '',
  video_code   TEXT,
  category     TEXT,
  state        TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','completed')),
  queued_at    TEXT,
  completed_at TEXT,
  session_id   TEXT,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acq_shadow_state ON AcquisitionOutcomeShadow(state);
CREATE INDEX IF NOT EXISTS idx_acq_shadow_session ON AcquisitionOutcomeShadow(session_id);
