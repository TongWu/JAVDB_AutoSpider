-- 2026-05-27: Add MovieMetadata table (ADR-022 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql
--
-- MovieMetadata stores rich detail-page content outside the
-- Pending→Commit session flow. Rows are written via direct UPSERT
-- after each successful detail-page parse.

CREATE TABLE IF NOT EXISTS MovieMetadata (
  href              TEXT PRIMARY KEY,
  title             TEXT,
  video_code        TEXT,
  release_date      TEXT,
  duration_minutes  INTEGER,
  rate              REAL,
  comment_count     INTEGER,
  review_count      INTEGER,
  want_count        INTEGER,
  watched_count     INTEGER,
  maker             TEXT,
  publisher         TEXT,
  series            TEXT,
  directors         TEXT,
  categories        TEXT,
  poster_url        TEXT,
  fanart_urls       TEXT,
  trailer_url       TEXT,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_movie_metadata_video_code
  ON MovieMetadata(video_code);
