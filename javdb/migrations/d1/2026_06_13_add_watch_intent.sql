-- 2026-06-13: Add WatchIntent table (ADR-054 WS1 Phase 1).
-- Write-Class: authoritative
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_06_13_add_watch_intent.sql
-- Then re-align the SQLite mirror:
--   python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
--
-- WatchIntent: the operator's manual want/viewed status per movie (user-intent,
-- ADR-054 WS1). Single-operator (no user_id). Distinct from ConsumptionSignal
-- (machine-observed media-server evidence, in javdb-operations) and from
-- MovieMetadata.want_count/watched_count (scraped site-wide crowd counts).
-- Co-located with MovieRatings in javdb-history. Outside the Pending->Commit flow.

CREATE TABLE IF NOT EXISTS WatchIntent (
  video_code  TEXT PRIMARY KEY,
  href        TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('want','viewed')),
  notes       TEXT,
  status_at   TEXT,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_watch_intent_status ON WatchIntent(status);
