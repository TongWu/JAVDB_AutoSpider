# BFR-012: MovieMetadata upsert reflects over `detail.__dict__`, which the Rust parser object lacks

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: High
**Affected**: `javdb/storage/repos/metadata_repo.py` (`MetadataRepo.upsert`), `javdb/migrations/tools/backfill_movie_metadata.py` (`_process_href`), `javdb/spider/detail/runner.py` (detail-phase metadata persist)
**Related**: [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) (owns the `MovieMetadata` table + `MetadataRepo`), [IMP-ADR022-08](../_archive/ADR-022-User-Preference-Foundation/IMP-ADR022-08-metadata-backfill.md) (the metadata backfill that surfaced it), [BFR-010](../BFR-010-Relative-Href-Inconsistency/BFR-010-relative-href-inconsistency.md) (the nested-link href absolutization that the fix must preserve)

---

## Symptom

The `--backfill-metadata` run of the Migration workflow failed every href with
a write error:

```text
⚠ javdb.migrat  [meta-1/1000] https://javdb.com/v/EbvX49 — write_failed: 'builtins.RustMovieDetail' object has no attribute '__dict__'
⚠ javdb.migrat  [meta-2/1000] https://javdb.com/v/G4P12  — write_failed: 'builtins.RustMovieDetail' object has no attribute '__dict__'
```

Every `MovieMetadata` upsert raised before touching the DB, so the backfill made
zero progress.

## Root Cause

`MetadataRepo.upsert` consumes a **mapping** (`detail.get('title')`,
`detail.get('maker')`, …). Both call sites built that mapping by reflecting over
the parse result with `detail.__dict__`:

- `backfill_movie_metadata.py` — `MetadataRepo().upsert(href, detail.__dict__)`
- `runner.py` — `MetadataRepo().upsert(href, movie_detail.__dict__)`

`parse_detail_page` is the **Rust** parser whenever `javdb.rust_core` is
installed (production, and this CI runner). It returns a `RustMovieDetail` PyO3
object, which exposes every field as a getter but has **no `__dict__`** —
accessing it raises `AttributeError: 'builtins.RustMovieDetail' object has no
attribute '__dict__'`. The pure-Python `MovieDetail` dataclass *does* have
`__dict__`, so the bug is invisible on the Python fallback path and only fires
when the Rust extension is active.

**Non-obvious side effect — a second, latent failure in the hot path.** The
same `.__dict__` form lived in `runner.py`'s detail-phase persist, the path the
**normal daily spider** takes for every scraped movie, wrapped in
`try/except Exception: logger.debug(...)` — so under the Rust parser it would
fail *silently* at DEBUG level rather than surface. Crucially, this path had
**not yet run in production** when the bug was found (see Side Effects for the
full timeline): the spider wiring (`e055804e`) merged to `main` at
2026-05-30 20:07 (+0800), ~7 minutes *after* that evening's daily ingestion had
already checked out the prior HEAD, and the fix landed before the next cron. So
the defect was **latent** — had it gone unnoticed, the 2026-05-31 20:00 cron
would have begun silently dropping every per-movie `MovieMetadata` write. The
loud backfill error exposed it first.

**Why the design was wrong, not just what broke.** `.__dict__` is a fragile way
to turn a domain object into a field map: it assumes a pure-Python object and
silently couples the call site to the parser's implementation backend. The repo
already documented its input as "MovieDetail.__dict__ *or an equivalent
mapping*", but never coerced — it trusted each caller to produce the mapping,
and the obvious way to do that (`obj.__dict__`) is exactly the one that breaks
on a PyO3 object.

## Fix

Move the coercion **into** `MetadataRepo.upsert` and have callers pass the
object directly:

- `metadata_repo.py` — `upsert` now accepts either a `Mapping` or a
  MovieDetail-like object. Non-mappings are coerced via
  `{f: getattr(detail, f, None) for f in _UPSERT_FIELDS}`, which works
  identically for the Python dataclass and the Rust `RustMovieDetail`. Nested
  link fields (`maker`/`publisher`/`series`/`directors`/`tags`) are deliberately
  kept as their **original objects** (Rust/Python `MovieLink`, both exposing
  `.name`/`.href`) so `_link`/`_links` can still absolutize their hrefs.
  **`MovieDetail.to_dict()` is explicitly NOT used** — it flattens nested links
  into plain dicts, which would route `_link` down its `json.dumps(obj)` branch
  and skip the BFR-010 absolutization.
- `backfill_movie_metadata.py` — `upsert(href, detail.__dict__)` →
  `upsert(href, detail)`.
- `runner.py` — `upsert(href, movie_detail.__dict__)` →
  `upsert(href, movie_detail)`.

Tests (`tests/unit/test_metadata_repo.py`):

- Added `test_upsert_accepts_object_without_dict`, which feeds a `__slots__`
  object (genuinely no `__dict__`, mirroring the Rust object's failure mode) and
  asserts the row is written **and** nested link hrefs are absolutized. The test
  first asserts `not hasattr(obj, '__dict__')` so it can never silently regress
  into exercising a dict.
- Verified empirically against the real extension: a `RustMovieDetail` with
  nested `RustMovieLink`s upserts cleanly with absolutized
  `maker`/`directors` hrefs.

## Side Effects

**No data loss.** Existing mapping callers (tests passing `_minimal_detail()`
dicts) are unaffected — `isinstance(detail, Mapping)` short-circuits the
coercion.

Timeline confirming zero production impact on the spider path:

- `e055804e` (spider metadata wiring) merged to `main` at 2026-05-30 20:07 (+0800).
- The daily ingestion cron fires at 12:00 UTC (20:00 +0800). That evening's run
  had already checked out the prior `main` HEAD — which did *not* contain
  `e055804e` — ~7 min before the merge, so it executed the pre-wiring code. Its
  result auto-commit (`94c30ab1`, 20:31) lists the later merges as git ancestors
  only because the push step rebased onto the updated `main`; the spider itself
  ran the 20:00 checkout. (`merge-base --is-ancestor` reflects graph topology,
  not the code a CI run actually executed.)
- The next cron (2026-05-31 20:00) never ran under the bug — the fix landed
  2026-05-31 10:21 (+0800).
- No ad-hoc ingestion ran in the window (operator-confirmed).

So `runner.py`'s upsert **never executed against a `RustMovieDetail` in
production** — no `MovieMetadata` rows were lost. The only path that actually
hit the bug was the Migration `--backfill-metadata` run, which fails *loudly*
(no silent loss); re-running it after the fix populates the table.

## Follow-Up

- [ ] Re-run the Migration `--backfill-metadata` job — it was fully blocked by
      this bug (1000/1000 hrefs `write_failed`) and now succeeds; this is the
      action that actually populates `MovieMetadata`. **No spider-side backfill
      is needed** — that path never ran under the bug (see Side Effects).
- [ ] Audit for other `.__dict__` / `vars()` reflection over parser results that
      could hit the same Rust-vs-Python divergence. `result_to_dict`
      (`javdb/spider/html_validators.py`) already does this correctly via
      `hasattr(result, "to_dict")`; the `align_inventory_with_moviehistory.py`
      `r.__dict__` use is over a local `BackfillResult` dataclass (safe).
