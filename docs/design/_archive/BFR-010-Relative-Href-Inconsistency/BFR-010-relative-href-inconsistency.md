# BFR-010: Actor / movie hrefs stored inconsistently (relative vs absolute)

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: High
**Affected**: `javdb/storage/db/_db_history_write.py`, `javdb/storage/repos/metadata_repo.py`, `javdb/migrations/tools/absolutize_javdb_urls_in_history.py`, `javdb/migrations/tools/backfill_movie_metadata.py`
**Related**: [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) (MovieMetadata), CONTEXT.md → "Href"

---

## Symptom

While running the ADR-022 `MovieMetadata` backfill from the Migration workflow,
every detail-page fetch failed with `fetch_failed: empty response`. Two distinct
problems surfaced from one investigation:

1. **Backfill URL doubling** — the backfill built `base_url + href`, but
   `MovieHistory.Href` is stored as an absolute URL, so it requested
   `https://javdb.comhttps://javdb.com/v/..` (never resolves). Fixed separately
   in the backfill tool the same day (URL build + CF-bypass + parse gate).

2. **Mixed href formats in D1** — a follow-up audit of *every* href-bearing
   column across the three D1 databases found two columns in `MovieHistory`
   that mix relative and absolute values **within the same column**:

   | Column | absolute | relative (`/actors/..`) | empty |
   | --- | --- | --- | --- |
   | `MovieHistory.ActorLink` | 38,830 | **849** | 779 |
   | `MovieHistory.SupportingActors` (JSON inner `link`) | 26,613 | **605 rows** | — |

   All other populated href columns (`MovieHistory.Href`, `ReportMovies.Href`,
   `ReportSessions.Url`, `SpiderStats.FailedMovies`) were uniformly absolute.

## Root Cause

The parser intentionally emits **site-relative** paths:
`MovieDetail.get_first_actor_href()` and `get_supporting_actors_json()` return
`normalize_javdb_href_path(...)` (e.g. `/actors/x`). The codebase convention is
"absolutize at the DB write layer".

The daily ingestion write path stages parser output into
`PendingMovieHistoryWrites` via `db_stage_history_write()`, then drains it into
`MovieHistory` via the commit/overlay path (`_commit_session_bulk`). The commit
path **absolutizes `Href`** (`normalized_href = abs_href or href`) but copies
`ActorLink` / `SupportingActors` **verbatim** from the pending row — and the
stage step did not absolutize them either. So newly committed daily rows kept
the relative actor links.

Other writers — actor backfill (`batch_update_movie_actors`), the legacy audit
upsert (`_upsert_one_history_on_conn`), the inventory-align path
(`db_upsert_history`) — already absolutize, which is why the *majority* of rows
are absolute. The relative minority are daily-committed rows written after the
actor columns were introduced and never re-normalized.

**Forward risk (ADR-022 `MovieMetadata`).** The MovieMetadata writers — the
daily runner's `MetadataRepo().upsert(href, ...)` and the backfill — stored the
movie `href` key and the embedded link fields (`maker` / `publisher` / `series`
/ `directors` / `categories`, which carry relative parser hrefs) **without
absolutization**. `MovieMetadata` was empty at the time, but once populated this
would (a) mix formats in `MovieMetadata.href` and the JSON link payloads, and
(b) break the backfill join `mm.href = mh.Href` (relative vs absolute), making
every row look un-backfilled.

## Fix

- **Stage-layer normalization (MovieHistory).** `db_stage_history_write()` now
  absolutizes `ActorLink` and `SupportingActors` before inserting into
  `PendingMovieHistoryWrites`, via `javdb_absolute_url` /
  `absolutize_supporting_actors_json`. Because every commit variant copies
  pending rows verbatim and `db_stage_history_write` is the **sole** writer of
  that table, this single chokepoint guarantees committed
  `MovieHistory.ActorLink` / `SupportingActors` are absolute — matching the
  absolute `Href` the commit path already produces.
- **MovieMetadata writer (forward risk).** `MetadataRepo.upsert()` now
  absolutizes the `href` key and every embedded link
  (`maker`/`publisher`/`series`/`directors`/`categories`). `MetadataRepo.get()`
  absolutizes its lookup key symmetrically, so callers may pass either form and
  the backfill join key stays consistent with `MovieHistory.Href`.
- **D1 data correction.**
  `javdb/migrations/tools/absolutize_javdb_urls_in_history.py` was refactored to
  be **backend-aware** (routes through `get_db`, so `STORAGE_BACKEND=d1` targets
  the canonical D1) and to fetch only site-relative *candidate* rows, then
  rewrite them in chunked, auto-committing batches. Run against D1:
  **MovieHistory 849 rows corrected** (relative → absolute); ReportMovies 0
  (already clean).

## Side Effects

- `MovieMetadata.href` and its embedded link JSON now store absolute URLs going
  forward. The API layer (`apps/api/routers/preferences.py`) passes these to the
  frontend; consumers that previously expected relative link paths inside the
  `maker`/`publisher`/`series`/`directors`/`categories` JSON will now receive
  absolute URLs. This is the intended, consistent behavior. `MovieMetadata` was
  empty at fix time, so no stored data was affected.
- Two existing `MetadataRepo` unit tests were updated to assert absolute
  embedded links.
- No change to already-absolute columns (`MovieHistory.Href`,
  `ReportMovies.Href`, etc.).

## Follow-Up

- [x] Normalize existing D1 `MovieHistory.ActorLink` + `SupportingActors`
  (849 rows, run 2026-05-31).
- [x] Re-align the local SQLite mirror from D1 (force-overwrite rebuild) and
  commit the LFS-tracked `reports/{history,reports,operations}.db` so the
  committed mirror carries the absolute hrefs.
- [x] Contract test added — `tests/unit/test_href_absolute_contract.py` drives
  both public writers (MovieHistory stage→commit, `MetadataRepo.upsert`) with
  site-relative input and fails if any site-relative JavDB href survives in
  `MovieHistory` / `MovieMetadata`.
