# ADR-022: User Preference Data Foundation

**Status:** Completed  
**Date:** 2026-05-26  
**Completed:** 2026-05-31  
**Author:** Ted  

---

> **Implementation status — Completed (2026-05-31).** All eight phases are implemented
> and merged: DB schema ([IMP-ADR022-01](IMP-ADR022-01-db-schema.md)), MetadataRepo +
> parser wiring ([IMP-ADR022-02](IMP-ADR022-02-metadata-repo.md)), PreferenceRepo +
> Python/TypeScript CRUD ([IMP-ADR022-03](IMP-ADR022-03-preference-repo.md),
> [IMP-ADR022-05](IMP-ADR022-05-typescript-sync.md)), the B2 upload gate
> ([IMP-ADR022-04](IMP-ADR022-04-upload-gate.md)), the web frontend C1/C3/C4/B3
> ([IMP-ADR022-06](IMP-ADR022-06-web-frontend.md)), unit tests
> ([IMP-ADR022-07](IMP-ADR022-07-tests.md), 35 passing), and the MovieMetadata backfill
> ([IMP-ADR022-08](IMP-ADR022-08-metadata-backfill.md)). Follow-up fixes BFR-010 (absolute
> hrefs) and BFR-012 (Rust `MovieDetail` coercion) also landed. The ML model direction
> continues in ADR-025.

## Context

JAVDB AutoSpider currently operates on rule-based heuristics with no awareness of user preferences. The detail page parser (`javdb/parsing/`) already extracts a rich `MovieDetail` dataclass — including categories, directors, maker, publisher, series, rating, want/watched counts, and more — but **every field except actor data is discarded** before reaching the database.

This means:

1. The system cannot adapt its download decisions to user taste.
2. Valuable metadata (categories, directors, maker) that is already parsed is silently thrown away on every run.
3. There is no mechanism for the user to express preferences about individual movies or content dimensions (actors, categories, makers).

The goal of this ADR is to establish the **data foundation layer** that will eventually feed a preference model. Model training itself is deferred to [ADR-025](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md), whose trainable phase depends on sufficient rating data.

---

## Decisions

### 1. Persist all valuable MovieDetail fields in a new `MovieMetadata` table

Rather than extending `MovieHistory` (a dedup/tracking table that flows through the Pending→Commit session path), create a separate `MovieMetadata` table that is written directly after detail page parsing, bypassing the session commit flow.

**Rationale for a separate table:**
- `MovieHistory` is on the critical Pending→Commit path; adding columns there requires changes to `PendingMovieHistoryWrites`, `HistoryRepo`, and session rollback logic — a large blast radius for what is effectively enrichment data.
- Metadata write failures are recoverable: if a `MovieMetadata` UPSERT fails, the next scrape of the same `href` will retry. No session integrity is at risk.
- Separation keeps `MovieHistory` as a pure dedup/tracking table and `MovieMetadata` as a content enrichment table with a clear single responsibility.

**Schema:**

```sql
CREATE TABLE MovieMetadata (
  href              TEXT PRIMARY KEY,  -- FK → MovieHistory.Href
  title             TEXT,
  video_code        TEXT,
  release_date      TEXT,              -- ISO 8601, e.g. "2025-10-28"
  duration_minutes  INTEGER,
  rate              REAL,              -- e.g. 4.2
  comment_count     INTEGER,           -- number of raters
  review_count      INTEGER,           -- number of short reviews
  want_count        INTEGER,           -- users who want to watch
  watched_count     INTEGER,           -- users who have watched
  maker             TEXT,              -- JSON {"name": ..., "href": ...}
  publisher         TEXT,              -- JSON {"name": ..., "href": ...}
  series            TEXT,              -- JSON {"name": ..., "href": ...}
  directors         TEXT,              -- JSON [{"name": ..., "href": ...}, ...]
  categories        TEXT,              -- JSON [{"name": ..., "href": ...}, ...]
  poster_url        TEXT,
  fanart_urls       TEXT,              -- JSON ["url", ...]
  trailer_url       TEXT,
  created_at        TEXT,
  updated_at        TEXT
);
```

**Fields deliberately excluded:**
- `code_prefix_link`: navigation-only, no analytical value.
- User review body text: high volume, high noise; reserved for a future NLP ADR.
- User-specific list membership: user-scoped, not content metadata.

**Write strategy:** Direct UPSERT (`INSERT OR REPLACE`) after detail page parsing completes, outside the Pending→Commit flow. Failures are silent and retriable.

---

### 2. Add two new tables for user ratings and content preferences

**`MovieRatings`** — per-movie explicit ratings set by the user:

```sql
CREATE TABLE MovieRatings (
  href         TEXT PRIMARY KEY,   -- FK → MovieHistory.Href
  video_code   TEXT NOT NULL,
  rating       INTEGER,            -- 1–5; NULL = unrated
  tags         TEXT DEFAULT '[]',  -- JSON array of predefined tag slugs
  notes        TEXT,               -- free-text annotation
  rated_at     TEXT,
  updated_at   TEXT
);
```

**`ContentPreferences`** — per-dimension (actor / category / maker / director) preferences:

```sql
CREATE TABLE ContentPreferences (
  content_type  TEXT NOT NULL,   -- 'actor' | 'category' | 'maker' | 'director'
  content_id    TEXT NOT NULL,   -- href slug or normalized name
  content_name  TEXT NOT NULL,
  hearted       INTEGER DEFAULT 0,  -- 1 = hearted
  weight        REAL DEFAULT 1.0,   -- reserved for ADR-025 crawl-priority use
  PRIMARY KEY (content_type, content_id)
);
```

---

### 3. Predefined tag vocabulary (12 tags, 3 groups)

Tags are stored as a JSON array of slugs in `MovieRatings.tags`. The UI renders them as multi-select chips grouped by dimension.

| Group | Slug | Display |
|-------|------|---------|
| Quality / Technical | `quality_high` | 画质优秀 |
| | `quality_low` | 画质差 |
| | `resolution_bad` | 分辨率不足 |
| | `encoding_bad` | 编码问题 |
| Content preference | `plot_good` | 剧情好 |
| | `actress_standout` | 女主出色 |
| | `not_my_type` | 不合口味 |
| | `category_miss` | 类别标错/不符 |
| Collection / Decision | `would_rewatch` | 值得重看 |
| | `keep_long_term` | 长期保留 |
| | `delete_candidate` | 可以删除 |
| | `upgrade_wanted` | 希望找更好版本 |

The vocabulary is defined as a constant in the Python backend and mirrored in the TypeScript backend; both must be updated in sync when tags are added or renamed.

---

### 4. Rating UX patterns

| Pattern | Where | Interaction |
|---------|-------|-------------|
| **C1 — Inline rating** | `/data` page (MovieHistory browser) | Star widget + tag chips + notes field per row; saves on blur/submit |
| **C3 — Batch annotation** | `/data` page, "Annotate" mode toggle | Keyboard-driven: `j`/`k` navigate, `1`–`5` rate, `Space` skip, `Enter` save & advance |
| **C4 — Heart on dimension** | `/data` actor/category/maker/director chips | Heart icon toggle → writes `ContentPreferences` row; visible on all pages that display these dimensions |
| **C2 — Email prompt** | Pipeline notification email | Deferred to a future enhancement |

---

### 5. Downstream consumers (rule-based placeholders until ADR-025)

**B2 — Upload filter hook:**  
A preference gate is added to the qBittorrent upload decision path. For ADR-022, the gate uses a simple rule: skip upload if the movie's lead actor has an explicit `hearted = false` entry in `ContentPreferences`. The hook point is implemented so that [ADR-025](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) can replace this rule with a model score without further refactoring.

**B3 — Web console preference score:**  
`/data` and `/browse` pages display a computed preference score alongside each history entry. For ADR-022, the score is a weighted average:

```
score = (movie_rating / 5.0) * 0.5
      + (actor_hearted ? 1.0 : 0.5) * 0.3
      + (category_match_ratio) * 0.2
```

This rule-based score is a placeholder; [ADR-025](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) replaces it with a trained model output.

**B1 — Dynamic crawl-priority adjustment:**  
Deferred to [ADR-025](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md). The `weight` column in `ContentPreferences` is reserved for this purpose.

---

## Phase Boundary

| Capability | ADR-022 (this ADR) | ADR-025 (deferred) |
|------------|-------------------|-----------------|
| `MovieMetadata` table + parser wiring | ✅ | — |
| `MovieRatings` + `ContentPreferences` tables | ✅ | — |
| C1 inline rating UI | ✅ | — |
| C3 batch annotation UI | ✅ | — |
| C4 heart on dimension | ✅ | — |
| B2 upload filter hook (rule-based) | ✅ | ML model replaces rule |
| B3 preference score display (rule-based) | ✅ | ML model replaces rule |
| B1 crawl-priority adjustment | — | ✅ |
| ML model training pipeline | — | ✅ |
| Model serving / inference | — | ✅ |
| C2 email rating prompts | — | ✅ |

[ADR-025](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) defines the model direction. Its trainable model phase should still wait until at least 200 movie ratings have been collected via C1/C3, giving enough signal for meaningful model training.

---

## Migration

All three new tables (`MovieMetadata`, `MovieRatings`, `ContentPreferences`) land on **D1 first** via new migration files under `javdb/migrations/d1/`. SQLite is re-aligned afterwards:

```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

The `MovieMetadata` table does not participate in Pending→Commit session flow. `MovieRatings` and `ContentPreferences` are user-authored and likewise outside the session flow.

---

## Alternatives Considered

### A — Extend `MovieHistory` with new columns

Add `Categories`, `Directors`, `Maker`, etc. directly to `MovieHistory`.

**Rejected** because `MovieHistory` is on the critical Pending→Commit session path. Adding columns requires matching changes to `PendingMovieHistoryWrites`, `HistoryRepo.stage_movie()`, and session rollback logic. The blast radius is disproportionate for enrichment data whose write failures are harmless.

### No separate `ContentPreferences` table — store actor/category preferences in `MovieRatings` tags

Encode actor/category preferences as special tags (e.g., `actor_hearted:EvkJ`).

**Rejected** because preferences are dimension-level (applies across all movies featuring an actor), not movie-level. A dedicated table with a typed `content_type` column makes queries and future ML feature extraction straightforward.

---

## Consequences

**Positive:**
- All valuable detail page fields are preserved from the first implementation, avoiding a second round of schema migration when ADR-025 implementation starts.
- The preference data model is simple and queryable without ML infrastructure.
- `MovieHistory` remains a pure dedup/tracking table with no added complexity.
- B2 and B3 are functional from day one with rule-based logic, providing immediate utility while ratings accumulate.

**Negative / Trade-offs:**
- `MovieMetadata` is written outside the session flow, so a failed run may leave partial metadata. Acceptable because metadata is enrichment-only and retried on next scrape.
- The rule-based B2/B3 placeholders add code that will be replaced by ADR-025; this is intentional scaffolding, not waste.
- The TypeScript backend (`javdb-autospider-web/server/`) must be updated in the same PR or a linked follow-up to expose `MovieMetadata`, `MovieRatings`, and `ContentPreferences` via the shared D1 query surface.

---

## Related

- [ADR-005](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) — Pending mode write flow
- [ADR-011](../ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md) — Parsing module structure and `MovieDetail` dataclass
- [ADR-014](../ADR-014-Storage-Cli-Layering/ADR-014-storage-cli-layering.md) — Storage / CLI layering
- [ADR-030](../ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md) — Web console feature parity
- [ADR-025](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) — User Preference Model; depends on this ADR
