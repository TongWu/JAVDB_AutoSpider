-- 2026-06-06: Add OwnershipLedger table (ADR-033 Phase 2).
-- Write-Class: additive
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_06_06_add_ownership_ledger.sql
--
-- OwnershipLedger is the multi-source "what do I own" superset of
-- RcloneInventory (ADR-033 D6). It is enrichment: written off the
-- Pending->Commit path by run_ownership, idempotent UPSERT by the
-- (video_code, source, category) PK. category is source-native and NOT NULL
-- (ADR-033 D-P2-1): gdrive = '<sensor>|<subtitle>' glyph composite,
-- qb = AcquisitionOutcome English category, pikpak/nas = '' when unknown.
-- present=0 rows are swept (not deleted) when a source no longer reports them.

CREATE TABLE IF NOT EXISTS OwnershipLedger (
  video_code  TEXT NOT NULL,
  source      TEXT NOT NULL CHECK (source IN ('qb','nas','gdrive','pikpak')),
  category    TEXT NOT NULL DEFAULT '',
  path        TEXT,
  size        INTEGER,
  present     INTEGER NOT NULL DEFAULT 1,
  observed_at TEXT,
  PRIMARY KEY (video_code, source, category)
);

CREATE INDEX IF NOT EXISTS idx_ownership_ledger_video_code ON OwnershipLedger(video_code);
CREATE INDEX IF NOT EXISTS idx_ownership_ledger_source ON OwnershipLedger(source);
CREATE INDEX IF NOT EXISTS idx_ownership_ledger_source_present ON OwnershipLedger(source, present);
