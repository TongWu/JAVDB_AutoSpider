-- 2026-05-27: Add MovieRatings and ContentPreferences tables (ADR-022 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_05_27_add_ratings_preferences_tables.sql
--
-- MovieRatings: per-movie explicit ratings (1–5) + predefined tag slugs + notes.
-- ContentPreferences: per-dimension (actor/category/maker/director) heart + weight.
-- Both sit outside the Pending→Commit session flow.

CREATE TABLE IF NOT EXISTS MovieRatings (
  href        TEXT PRIMARY KEY,
  video_code  TEXT NOT NULL,
  rating      INTEGER CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
  tags        TEXT NOT NULL DEFAULT '[]',
  notes       TEXT,
  rated_at    TEXT,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS ContentPreferences (
  content_type  TEXT NOT NULL
    CHECK (content_type IN ('actor','category','maker','director')),
  content_id    TEXT NOT NULL,
  content_name  TEXT NOT NULL,
  hearted       INTEGER NOT NULL DEFAULT 0 CHECK (hearted IN (0, 1)),
  weight        REAL NOT NULL DEFAULT 1.0,
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  PRIMARY KEY (content_type, content_id)
);

CREATE INDEX IF NOT EXISTS idx_content_prefs_hearted
  ON ContentPreferences(content_type, hearted);
