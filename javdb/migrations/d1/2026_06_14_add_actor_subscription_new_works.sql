-- 2026-06-14: Add ActorSubscription + NewWorks tables (ADR-054 WS2 Phase A).
-- Write-Class: authoritative
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql
-- Then re-align the SQLite mirror:
--   python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
--
-- ActorSubscription: a followed actor + last-seen cursor (user-intent, ADR-054
-- WS2). Identity = the normalized /actors/<id> href (matches
-- ActorMetadata.actor_href and MovieHistory.ActorLink). actor_name is
-- display-only and best-effort (Japanese-name matching is unreliable) - never a
-- join key. Single-operator (no user_id). Co-located with WatchIntent /
-- MovieRatings in javdb-history. Outside the Pending->Commit flow.
--
-- NewWorks: a discovered, not-yet-acted-on release for a subscribed actor. A
-- feed-state table (discovered_at + dismissed), NOT an ingestion record -
-- MovieHistory remains the authoritative ingestion log. The SubscriptionMonitor
-- cron writes NewWorks from the scrape diff and never reads it back as truth.

CREATE TABLE IF NOT EXISTS ActorSubscription (
  actor_href      TEXT PRIMARY KEY,
  actor_name      TEXT,
  active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  last_seen_href  TEXT,
  last_checked_at TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_actor_subscription_active ON ActorSubscription(active);

CREATE TABLE IF NOT EXISTS NewWorks (
  video_code    TEXT PRIMARY KEY,
  href          TEXT NOT NULL,
  actor_href    TEXT NOT NULL,
  title         TEXT,
  release_date  TEXT,
  discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  dismissed     INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0,1))
);

CREATE INDEX IF NOT EXISTS idx_new_works_actor     ON NewWorks(actor_href);
CREATE INDEX IF NOT EXISTS idx_new_works_dismissed ON NewWorks(dismissed);
