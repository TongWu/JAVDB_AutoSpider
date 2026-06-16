-- 2026-06-16: Repair NewWorks primary key — single-column video_code PK to
-- composite (actor_href, video_code). Fixes issue #223 (HIGH).
-- Write-Class: authoritative
--
-- Background
-- ----------
-- The original table (2026_06_14_add_actor_subscription_new_works.sql) declared
-- `video_code TEXT PRIMARY KEY`. Combined with the repo's idempotent insert,
-- that silently dropped a release when it surfaced under a second followed
-- actor: the PK collided on video_code and the new (actor_href, video_code) row
-- was never written, so the per-actor feed (GET /api/new-works?actor_href=...)
-- under-counted forever (no self-healing on re-scrape — the PK kept colliding).
--
-- SQLite/D1 cannot ALTER a PRIMARY KEY, so this rebuilds the table with the
-- standard CREATE _new -> copy -> DROP -> RENAME pattern, preserving every
-- existing row. Rows that were dropped before this migration are restored on
-- the next subscription-monitor scrape (the insert no longer collides).
--
-- The canonical DDL (2026_06_14_*.sql) and the fresh-DB SQLite mirror
-- (javdb/storage/db/_db_migrations.py) already carry the composite PK; on a
-- fresh database this migration is an equivalent empty-table rebuild.
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_06_16_newworks_composite_pk.sql
-- Then re-align the SQLite mirror:
--   python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all

CREATE TABLE NewWorks_new (
  video_code    TEXT NOT NULL,
  href          TEXT NOT NULL,
  actor_href    TEXT NOT NULL,
  title         TEXT,
  release_date  TEXT,
  discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  dismissed     INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0,1)),
  PRIMARY KEY (actor_href, video_code)
);
INSERT INTO NewWorks_new
    (video_code, href, actor_href, title, release_date, discovered_at, dismissed)
SELECT video_code, href, actor_href, title, release_date, discovered_at, dismissed
FROM NewWorks;
DROP TABLE NewWorks;
ALTER TABLE NewWorks_new RENAME TO NewWorks;

CREATE INDEX IF NOT EXISTS idx_new_works_actor     ON NewWorks(actor_href);
CREATE INDEX IF NOT EXISTS idx_new_works_dismissed ON NewWorks(dismissed);
