# ADR-022 Phase 1 — Database Schema (D1 Migrations)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `MovieMetadata`, `MovieRatings`, and `ContentPreferences` tables on D1, then re-align the local SQLite mirror.

**Architecture:** Three new tables land on the canonical D1 `history` database. All three sit outside the Pending→Commit session flow — writes are direct UPSERTs, not staged. SQLite is re-aligned after D1 is updated.

**Tech Stack:** Cloudflare D1, wrangler CLI, `python3 -m apps.cli.db.sync_d1_to_sqlite`

**Related:** [ADR-022](ADR-022-user-preference-foundation.md) · [IMP-ADR022-02](IMP-ADR022-02-metadata-repo.md) · [IMP-ADR022-03](IMP-ADR022-03-preference-repo.md)

**Depends on:** Nothing — this is the first phase.

**Blocks:** All other phases.

---

## Task 1 — MovieMetadata migration

**Files:**
- Create: `javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql`

- [ ] **Step 1: Create the migration file**

```sql
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
```

- [ ] **Step 2: Apply to D1**

```bash
wrangler d1 execute javdb-history --remote \
  --file=javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql
```

Expected: `✅ Successfully applied migration`

- [ ] **Step 3: Verify table exists on D1**

```bash
wrangler d1 execute javdb-history --remote \
  --command="SELECT name FROM sqlite_master WHERE type='table' AND name='MovieMetadata';"
```

Expected: one row with `MovieMetadata`.

- [ ] **Step 4: Commit**

```bash
git add javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql
git commit -m "feat(db): add MovieMetadata table (ADR-022 phase 1)"
```

---

## Task 2 — MovieRatings + ContentPreferences migration

**Files:**
- Create: `javdb/migrations/d1/2026_05_27_add_ratings_preferences_tables.sql`

- [ ] **Step 1: Create the migration file**

```sql
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
```

- [ ] **Step 2: Apply to D1**

```bash
wrangler d1 execute javdb-history --remote \
  --file=javdb/migrations/d1/2026_05_27_add_ratings_preferences_tables.sql
```

Expected: `✅ Successfully applied migration`

- [ ] **Step 3: Verify both tables exist**

```bash
wrangler d1 execute javdb-history --remote \
  --command="SELECT name FROM sqlite_master WHERE type='table' AND name IN ('MovieRatings','ContentPreferences');"
```

Expected: two rows.

- [ ] **Step 4: Commit**

```bash
git add javdb/migrations/d1/2026_05_27_add_ratings_preferences_tables.sql
git commit -m "feat(db): add MovieRatings and ContentPreferences tables (ADR-022 phase 1)"
```

---

## Task 3 — Re-align SQLite mirror

- [ ] **Step 1: Run sync**

```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

Expected: no errors; `MovieMetadata`, `MovieRatings`, and `ContentPreferences` present in local `reports/history.db`.

- [ ] **Step 2: Verify locally**

```bash
python3 -c "
import sqlite3, os
path = 'reports/history.db'
conn = sqlite3.connect(path)
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('MovieMetadata','MovieRatings','ContentPreferences')\").fetchall()
print([r[0] for r in tables])
conn.close()
"
```

Expected: `['MovieMetadata', 'MovieRatings', 'ContentPreferences']` (order may vary).

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | `MovieMetadata` on D1 | `wrangler d1 execute javdb-history --remote --command="SELECT COUNT(*) FROM MovieMetadata;"` → no error |
| 2 | `MovieRatings` on D1 | Same for `MovieRatings` |
| 3 | `ContentPreferences` on D1 | Same for `ContentPreferences` |
| 4 | SQLite re-aligned | All three tables present in `reports/history.db` |
