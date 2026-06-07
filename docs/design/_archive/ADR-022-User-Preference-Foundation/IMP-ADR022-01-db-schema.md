# ADR-022 Phase 1 — Database Schema (D1 Migrations)

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `MovieMetadata`, `MovieRatings`, and `ContentPreferences` tables on D1, then re-align the local SQLite mirror.

**Architecture:** Three new tables land on the canonical D1 `history` database. All three sit outside the Pending→Commit session flow — writes are direct UPSERTs, not staged. SQLite is re-aligned after D1 is updated.

**Tech Stack:** Cloudflare D1, wrangler CLI, `python3 -m apps.cli.db.sync_d1_to_sqlite`

**Related:** [ADR-022](ADR-022-user-preference-foundation.md) · [IMP-ADR022-02](IMP-ADR022-02-metadata-repo.md) · [IMP-ADR022-03](IMP-ADR022-03-preference-repo.md)

**Depends on:** Nothing — this is the first phase.

**Blocks:** All other phases.

---

## Status — ✅ Implemented

Both D1 migrations exist (`javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql`,
`..._add_ratings_preferences_tables.sql`) and all three tables are mirrored verbatim in
the checked-in `_HISTORY_DDL` (`javdb/storage/db/_db_migrations.py`), so the D1↔local
schema-parity guard test
(`tests/unit/test_rollback_full_fidelity.py::TestD1MigrationsAreCoveredByLocalSchema`)
passes. See the divergence note below for the two gaps (local-DDL parity and the
token-gated force-overwrite) that were found and closed during implementation.

---

> **⚠ Divergence note (recorded during implementation, 2026-05-30).** Two gaps in
> the steps below were found and fixed:
> 1. **Local DDL parity (`init_db`) was missing.** Task 3 only re-aligns the
>    *runtime* SQLite file via `sync_d1_to_sqlite`. But the repo also has a
>    checked-in production schema initializer — `_HISTORY_DDL` in
>    `javdb/storage/db/_db_migrations.py` (used by `init_db`) — and a guard test
>    `tests/unit/test_rollback_full_fidelity.py::TestD1MigrationsAreCoveredByLocalSchema`
>    that fails if any D1-migration table/column is absent from that local DDL.
>    The three ADR-022 tables were added to `_HISTORY_DDL` verbatim (mirroring the
>    D1 migration files) so the D1↔local schema-parity contract holds.
>    **Rule for future migration phases:** a new D1 table must be added to BOTH
>    `javdb/migrations/d1/*.sql` AND `_HISTORY_DDL`/`_REPORTS_DDL`/`_OPERATIONS_DDL`.
> 2. **`CLOUDFLARE_API_TOKEN` unavailable locally.** `sync_d1_to_sqlite --apply
>    --force-overwrite-all` needs that token (HTTP D1 API) and could not run in
>    this session. The three tables were instead created in the local runtime
>    `reports/history.db` by applying the committed migration DDL directly
>    (same schema, no drift). The full force-overwrite reconciliation still runs
>    in CI / can be run later when the token is present.

## Task 1 — MovieMetadata migration

**Files:**
- Create: `javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql`

- [x] **Step 1: Create the migration file**

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

- [x] **Step 2: Apply to D1**

```bash
wrangler d1 execute javdb-history --remote \
  --file=javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql
```

Expected: `✅ Successfully applied migration`

- [x] **Step 3: Verify table exists on D1**

```bash
wrangler d1 execute javdb-history --remote \
  --command="SELECT name FROM sqlite_master WHERE type='table' AND name='MovieMetadata';"
```

Expected: one row with `MovieMetadata`.

- [x] **Step 4: Commit**

```bash
git add javdb/migrations/d1/2026_05_27_add_movie_metadata_table.sql
git commit -m "feat(db): add MovieMetadata table (ADR-022 phase 1)"
```

---

## Task 2 — MovieRatings + ContentPreferences migration

**Files:**
- Create: `javdb/migrations/d1/2026_05_27_add_ratings_preferences_tables.sql`

- [x] **Step 1: Create the migration file**

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

- [x] **Step 2: Apply to D1**

```bash
wrangler d1 execute javdb-history --remote \
  --file=javdb/migrations/d1/2026_05_27_add_ratings_preferences_tables.sql
```

Expected: `✅ Successfully applied migration`

- [x] **Step 3: Verify both tables exist**

```bash
wrangler d1 execute javdb-history --remote \
  --command="SELECT name FROM sqlite_master WHERE type='table' AND name IN ('MovieRatings','ContentPreferences');"
```

Expected: two rows.

- [x] **Step 4: Commit**

```bash
git add javdb/migrations/d1/2026_05_27_add_ratings_preferences_tables.sql
git commit -m "feat(db): add MovieRatings and ContentPreferences tables (ADR-022 phase 1)"
```

---

## Task 3 — Re-align SQLite mirror

- [x] **Step 1: Run sync**

```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

Expected: no errors; `MovieMetadata`, `MovieRatings`, and `ContentPreferences` present in local `reports/history.db`.

- [x] **Step 2: Verify locally**

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
