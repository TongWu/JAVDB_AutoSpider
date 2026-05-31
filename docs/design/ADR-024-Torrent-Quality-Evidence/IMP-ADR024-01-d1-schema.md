# IMP-ADR024-01: ADR-024 Phase 1 — D1 Schema (Torrent Quality Evidence)

**Status:** Completed — 2026-05-31.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the two canonical D1 tables — `TorrentQualityEvidence` (torrent-level objective facts) and `TorrentQualityEvaluation` (movie-context shadow scoring) — on the `javdb-reports` database, mirror them into the local `_REPORTS_DDL`, and re-align the SQLite mirror.

**Architecture:** Both tables land on the canonical D1 `reports` database (same DB as ADR-026 `OpsIncidents`). They sit **outside** the Pending→Commit session flow — rows are written via direct UPSERT. Evidence is keyed by `(info_hash, probe_schema_version, target_role)`; evaluation is keyed by `(info_hash, movie_href, scoring_version)`. Per ADR-024 the evaluation table also reserves `policy_mode` / `decision` / `would_replace_current_choice` / `shadow_rank` columns so Phase 2 (assist) and Phase 3 (enforce) need no schema migration.

**Tech Stack:** Cloudflare D1, wrangler CLI, `python3 -m apps.cli.db.sync_d1_to_sqlite`.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md), D1, D2, D3, D9, D11; "Evidence Model" section.

**Related:** [IMP-ADR024-02](IMP-ADR024-02-models-repo.md) (consumes these tables).

**Depends on:** Nothing — this is the first phase.

**Blocks:** All other ADR-024 IMPs.

---

> **⚠ Migration parity rule (learned in ADR-022/026).** A new D1 table must be
> added to BOTH `javdb/migrations/d1/*.sql` AND the matching local DDL constant
> in `javdb/storage/db/_db_migrations.py` (`_HISTORY_DDL` / `_REPORTS_DDL` /
> `_OPERATIONS_DDL`). The guard test
> `tests/unit/test_rollback_full_fidelity.py::TestD1MigrationsAreCoveredByLocalSchema`
> parses every D1 migration and fails if any declared column is absent from the
> local DDL. These tables live on `javdb-reports`, so they go into `_REPORTS_DDL`.

## Scope Boundaries

- Do **not** route these tables through `PendingMovieHistoryWrites` / session commit.
- Do **not** add SQLite-only columns. The D1 migration is canonical; SQLite is a mirror.
- Do **not** write any rows yet — this IMP only creates schema. Repos come in IMP-02.

---

## Task 1 — D1 migration file

**Files:**
- Create: `javdb/migrations/d1/2026_05_31_add_torrent_quality_tables.sql`

- [x] **Step 1: Create the migration file**

```sql
-- 2026-05-31: Add TorrentQualityEvidence + TorrentQualityEvaluation tables (ADR-024 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_31_add_torrent_quality_tables.sql
--
-- Both tables sit OUTSIDE the Pending->Commit session flow. Rows are written
-- via direct UPSERT in shadow mode. TorrentQualityEvidence holds torrent-level
-- objective facts keyed by (info_hash, probe_schema_version, target_role).
-- TorrentQualityEvaluation holds movie-context shadow scoring keyed by
-- (info_hash, movie_href, scoring_version). The policy_mode / decision /
-- would_replace_current_choice / shadow_rank columns are reserved for the
-- ADR-024 Phase 2 (assist) and Phase 3 (enforce) rollouts; Phase 1 only writes
-- shadow values.

CREATE TABLE IF NOT EXISTS TorrentQualityEvidence (
    info_hash             TEXT NOT NULL,
    probe_schema_version  TEXT NOT NULL,
    target_role           TEXT NOT NULL,
    probe_target_name     TEXT,
    metadata_status       TEXT,
    metadata_started_at   TEXT,
    metadata_completed_at TEXT,
    total_size_bytes      INTEGER,
    main_video_size_bytes INTEGER,
    main_video_ratio      REAL,
    video_file_count      INTEGER,
    subtitle_file_count   INTEGER,
    non_video_file_count  INTEGER,
    junk_size_bytes       INTEGER,
    junk_size_ratio       REAL,
    suspicious_file_count INTEGER,
    features_json         TEXT,
    reasons_json          TEXT,
    source_fingerprint    TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (info_hash, probe_schema_version, target_role)
);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_evidence_created_at
    ON TorrentQualityEvidence(created_at);

CREATE TABLE IF NOT EXISTS TorrentQualityEvaluation (
    info_hash                   TEXT NOT NULL,
    movie_href                  TEXT NOT NULL,
    scoring_version             TEXT NOT NULL,
    video_code                  TEXT,
    javdb_category              TEXT,
    magnet_name                 TEXT,
    javdb_tags_json             TEXT,
    javdb_size_text             TEXT,
    inferred_category           TEXT,
    category_consistent         INTEGER,
    subtitle_evidence           TEXT,
    resolution_consistent       INTEGER,
    source_trust                TEXT,
    score                       REAL,
    shadow_rank                 INTEGER,
    would_replace_current_choice INTEGER,
    policy_mode                 TEXT,
    decision                    TEXT,
    reasons_json                TEXT,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (info_hash, movie_href, scoring_version)
);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_movie_href
    ON TorrentQualityEvaluation(movie_href);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_video_code
    ON TorrentQualityEvaluation(video_code);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_created_at
    ON TorrentQualityEvaluation(created_at);
```

- [x] **Step 2: Apply to D1**

Run:

```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_05_31_add_torrent_quality_tables.sql
```

Expected: `✅ Successfully applied migration` (or, if `CLOUDFLARE_API_TOKEN`
is unavailable locally, run this in CI — see the divergence note style in
[IMP-ADR022-01](../ADR-022-User-Preference-Foundation/IMP-ADR022-01-db-schema.md)).

- [x] **Step 3: Verify both tables exist on D1**

Run:

```bash
wrangler d1 execute javdb-reports --remote \
  --command="SELECT name FROM sqlite_master WHERE type='table' AND name IN ('TorrentQualityEvidence','TorrentQualityEvaluation');"
```

Expected: two rows.

- [x] **Step 4: Commit**

```bash
git add javdb/migrations/d1/2026_05_31_add_torrent_quality_tables.sql
git commit -m "feat(db): add torrent quality evidence tables (ADR-024 phase 1)"
```

---

## Task 2 — Local DDL parity (`_REPORTS_DDL`)

**Files:**
- Modify: `javdb/storage/db/_db_migrations.py`

- [x] **Step 1: Add both tables to `_REPORTS_DDL`**

In `javdb/storage/db/_db_migrations.py`, inside the `_REPORTS_DDL = """ ... """`
block (the same block that defines `OpsIncidents`, `ContentFilterRule`,
`PipelineEvent`, …), append the **verbatim** DDL below just before the closing
`"""` of `_REPORTS_DDL`. The column list must match the migration in Task 1
character-for-character (the guard test compares column names).

```sql

-- ADR-024 Phase 1: torrent quality evidence + shadow evaluation.
CREATE TABLE IF NOT EXISTS TorrentQualityEvidence (
    info_hash             TEXT NOT NULL,
    probe_schema_version  TEXT NOT NULL,
    target_role           TEXT NOT NULL,
    probe_target_name     TEXT,
    metadata_status       TEXT,
    metadata_started_at   TEXT,
    metadata_completed_at TEXT,
    total_size_bytes      INTEGER,
    main_video_size_bytes INTEGER,
    main_video_ratio      REAL,
    video_file_count      INTEGER,
    subtitle_file_count   INTEGER,
    non_video_file_count  INTEGER,
    junk_size_bytes       INTEGER,
    junk_size_ratio       REAL,
    suspicious_file_count INTEGER,
    features_json         TEXT,
    reasons_json          TEXT,
    source_fingerprint    TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (info_hash, probe_schema_version, target_role)
);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_evidence_created_at
    ON TorrentQualityEvidence(created_at);

CREATE TABLE IF NOT EXISTS TorrentQualityEvaluation (
    info_hash                   TEXT NOT NULL,
    movie_href                  TEXT NOT NULL,
    scoring_version             TEXT NOT NULL,
    video_code                  TEXT,
    javdb_category              TEXT,
    magnet_name                 TEXT,
    javdb_tags_json             TEXT,
    javdb_size_text             TEXT,
    inferred_category           TEXT,
    category_consistent         INTEGER,
    subtitle_evidence           TEXT,
    resolution_consistent       INTEGER,
    source_trust                TEXT,
    score                       REAL,
    shadow_rank                 INTEGER,
    would_replace_current_choice INTEGER,
    policy_mode                 TEXT,
    decision                    TEXT,
    reasons_json                TEXT,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (info_hash, movie_href, scoring_version)
);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_movie_href
    ON TorrentQualityEvaluation(movie_href);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_video_code
    ON TorrentQualityEvaluation(video_code);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_created_at
    ON TorrentQualityEvaluation(created_at);
```

- [x] **Step 2: Run the parity guard test to verify it passes**

Run:

```bash
pytest tests/unit/test_rollback_full_fidelity.py::TestD1MigrationsAreCoveredByLocalSchema -v
```

Expected: PASS. If it fails with a missing column, the column name in the
migration (Task 1) and the `_REPORTS_DDL` block do not match — fix the typo.

- [x] **Step 3: Materialize tables locally without a token**

`init_db()` is the token-free way to create the new tables in the local SQLite
mirror (it applies `_REPORTS_DDL`). Run:

```bash
python3 -c "
from javdb.storage.db import REPORTS_DB_PATH
from javdb.storage.db._db_migrations import init_db
init_db(REPORTS_DB_PATH)
import sqlite3
conn = sqlite3.connect(REPORTS_DB_PATH)
names = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('TorrentQualityEvidence','TorrentQualityEvaluation')\").fetchall()]
print(sorted(names))
conn.close()
"
```

Expected: `['TorrentQualityEvaluation', 'TorrentQualityEvidence']`.

- [x] **Step 4: Commit**

```bash
git add javdb/storage/db/_db_migrations.py
git commit -m "feat(db): mirror torrent quality tables into local reports DDL (ADR-024)"
```

---

## Task 3 — Re-align SQLite mirror (when a token is present)

- [x] **Step 1: Force-overwrite the local mirror from D1**

When `CLOUDFLARE_API_TOKEN` is available (CI or local), reconcile the runtime
SQLite mirror from D1's verbatim DDL:

```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

Expected: no errors; both tables present in local `reports/reports.db`. If the
token is not available, this step is deferred to CI — `init_db()` in Task 2
Step 3 already created the tables locally with the same schema.

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | `TorrentQualityEvidence` on D1 | `wrangler d1 execute javdb-reports --remote --command="SELECT COUNT(*) FROM TorrentQualityEvidence;"` → no error |
| 2 | `TorrentQualityEvaluation` on D1 | Same for `TorrentQualityEvaluation` |
| 3 | Parity guard passes | `pytest tests/unit/test_rollback_full_fidelity.py::TestD1MigrationsAreCoveredByLocalSchema -v` → PASS |
| 4 | Local mirror has both tables | Task 2 Step 3 prints both table names |
