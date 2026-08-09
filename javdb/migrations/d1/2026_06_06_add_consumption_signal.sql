-- 2026-06-06: Add ConsumptionSignal + UnresolvedMediaItem tables (ADR-033 Phase 3).
-- Write-Class: additive
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_06_06_add_consumption_signal.sql
--
-- ConsumptionSignal records per (video_code, instance, library) watch/rating
-- evidence pulled from media servers. It is enrichment: written off the
-- Pending->Commit path, idempotent UPSERT, never merged across sources on write
-- (merging is a derived view, ADR-033 D8). UnresolvedMediaItem holds media items
-- whose video_code could not be resolved (ADR-033 D9) so the count is never lost.

-- ConsumptionSignal columns: source_type is emby|plex; instance is the configured
-- connection id such as plex-home; resolved_confidence is high|medium|low.
CREATE TABLE IF NOT EXISTS ConsumptionSignal (
  video_code          TEXT NOT NULL,
  source_type         TEXT NOT NULL,
  instance            TEXT NOT NULL,
  library_id          TEXT NOT NULL,
  library_name        TEXT,
  watched             INTEGER,
  progress_pct        INTEGER,
  play_count          INTEGER,
  rating              REAL,
  watched_at          TEXT,
  resolved_confidence TEXT,
  observed_at         TEXT,
  PRIMARY KEY (video_code, instance, library_id)
);

CREATE INDEX IF NOT EXISTS idx_consumption_video_code ON ConsumptionSignal(video_code);
CREATE INDEX IF NOT EXISTS idx_consumption_instance_library ON ConsumptionSignal(instance, library_id);

-- PK is (instance, library_id, item_id) and deliberately EXCLUDES source_type:
-- re-observing the same server-side item UPSERTs in place (no duplicate rows),
-- while source_type is recorded for audit only and never defines uniqueness
-- (adapters are independent; one instance is always exactly one source_type).
CREATE TABLE IF NOT EXISTS UnresolvedMediaItem (
  instance     TEXT NOT NULL,
  source_type  TEXT,
  library_id   TEXT NOT NULL,
  library_name TEXT,
  item_id      TEXT NOT NULL,
  raw_title    TEXT,
  file_path    TEXT,
  observed_at  TEXT,
  PRIMARY KEY (instance, library_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_unresolved_instance ON UnresolvedMediaItem(instance);
