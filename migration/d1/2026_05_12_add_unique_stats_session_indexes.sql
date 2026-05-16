-- 2026-05-12 — backfill the missing UNIQUE(SessionId) indexes on the
-- *Stats tables in D1.
--
-- Background: the local SQLite DDL in
-- ``packages/python/javdb_platform/db.py`` creates three UNIQUE
-- indexes that the application's idempotent stats writers
-- (``db_save_spider_stats`` / ``db_save_uploader_stats`` /
-- ``db_save_pikpak_stats``) rely on for their
-- ``INSERT ... ON CONFLICT(SessionId) DO UPDATE`` upsert:
--
--   CREATE UNIQUE INDEX uq_spiderstats_session   ON SpiderStats(SessionId);
--   CREATE UNIQUE INDEX uq_uploaderstats_session ON UploaderStats(SessionId);
--   CREATE UNIQUE INDEX uq_pikpakstats_session   ON PikpakStats(SessionId);
--
-- These indexes were never mirrored to D1, so the same upsert against
-- D1 fails with:
--
--   code 7500: ON CONFLICT clause does not match any PRIMARY KEY or
--   UNIQUE constraint: SQLITE_ERROR
--
-- which the d1_client retry classifier currently treats as transient
-- (HTTP 400 with SQLITE_ERROR), wasting five retries × ~15s back-off
-- before bubbling up as a hard D1 write failure. The d1_client
-- classifier should ideally treat ``code 7500`` as permanent, but the
-- real fix is to give D1 the indexes the SQL relies on.
--
-- All three statements are idempotent (``IF NOT EXISTS``); fresh D1
-- databases get them on first apply and existing ones are a no-op.
--
-- Apply:
--   wrangler d1 execute javdb-reports --remote \
--     --file=migration/d1/2026_05_12_add_unique_stats_session_indexes.sql
--
-- Roll back (rarely needed):
--   DROP INDEX IF EXISTS uq_spiderstats_session;
--   DROP INDEX IF EXISTS uq_uploaderstats_session;
--   DROP INDEX IF EXISTS uq_pikpakstats_session;
--
-- If duplicate (SessionId) rows already exist in D1 the CREATE UNIQUE
-- INDEX will fail loudly; run the per-table dedupe pass below first
-- (kept commented because it is destructive — operator must confirm
-- the dedupe target before un-commenting).
--
-- DELETE FROM SpiderStats   WHERE Id NOT IN (SELECT MAX(Id) FROM SpiderStats   GROUP BY SessionId);
-- DELETE FROM UploaderStats WHERE Id NOT IN (SELECT MAX(Id) FROM UploaderStats GROUP BY SessionId);
-- DELETE FROM PikpakStats   WHERE Id NOT IN (SELECT MAX(Id) FROM PikpakStats   GROUP BY SessionId);

CREATE UNIQUE INDEX IF NOT EXISTS uq_spiderstats_session
    ON SpiderStats(SessionId);

CREATE UNIQUE INDEX IF NOT EXISTS uq_uploaderstats_session
    ON UploaderStats(SessionId);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pikpakstats_session
    ON PikpakStats(SessionId);
