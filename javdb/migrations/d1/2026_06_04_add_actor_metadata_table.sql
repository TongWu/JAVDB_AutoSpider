-- 2026-06-04: Add ActorMetadata table (ADR-040 Phase 2 / IMP-ADR040-02).
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_06_04_add_actor_metadata_table.sql
--
-- Write-Class: additive
--
-- Best-effort actor-age enrichment cache. Birthdates are resolved from minnano-av
-- keyed by the normalized javdb actor href. A row with resolved=1 and birthdate
-- NULL is a NEGATIVE cache (looked up, not found) so the pipeline does not
-- re-query the external site every run. Additive: no rows = no behavior change.

CREATE TABLE IF NOT EXISTS ActorMetadata (
  actor_href  TEXT PRIMARY KEY,
  actor_name  TEXT,
  birthdate   TEXT,
  source      TEXT,
  source_url  TEXT,
  resolved    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
