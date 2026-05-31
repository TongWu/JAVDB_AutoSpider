# BFR-012: MovieMetadata upsert reflects over `detail.__dict__`, which the Rust parser object lacks

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: High
**Affected**: `javdb/storage/repos/metadata_repo.py` (`MetadataRepo.upsert`), `javdb/migrations/tools/backfill_movie_metadata.py` (`_process_href`), `javdb/spider/detail/runner.py` (detail-phase metadata persist)
**Related**: [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) (owns the `MovieMetadata` table + `MetadataRepo`), [IMP-ADR022-08](../ADR-022-User-Preference-Foundation/IMP-ADR022-08-metadata-backfill.md) (the metadata backfill that surfaced it), [BFR-010](../BFR-010-Relative-Href-Inconsistency/BFR-010-relative-href-inconsistency.md) (the nested-link href absolutization that the fix must preserve)

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

**Non-obvious side effect — a second, silent failure in the hot path.** The
same `.__dict__` form lived in `runner.py`'s detail-phase persist, the path the
**normal daily spider** takes for every scraped movie. There it was wrapped in
`try/except Exception: logger.debug(...)`, so it never surfaced: under the Rust
parser (i.e. production), every per-movie `MovieMetadata` upsert has been
failing silently at DEBUG level since ADR-022 shipped. The loud backfill error
is what exposed a defect that the spider had been swallowing all along —
`MovieMetadata` (an ADR-022 table destined for canonical D1) was simply not
being populated by the live pipeline.

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

None functional. Existing mapping callers (tests passing `_minimal_detail()`
dicts) are unaffected — `isinstance(detail, Mapping)` short-circuits the
coercion. The live spider now persists `MovieMetadata` for every scraped movie
again, which it had been silently failing to do under the Rust parser.

## Follow-Up

- [ ] Backfill (optional): the `MovieMetadata` rows the daily spider failed to
      write during the silent-failure window (ADR-022 ship → this fix) can be
      repopulated by re-running `--backfill-metadata`, which now succeeds.
- [ ] Audit for other `.__dict__` / `vars()` reflection over parser results that
      could hit the same Rust-vs-Python divergence. `result_to_dict`
      (`javdb/spider/html_validators.py`) already does this correctly via
      `hasattr(result, "to_dict")`; the `align_inventory_with_moviehistory.py`
      `r.__dict__` use is over a local `BackfillResult` dataclass (safe).
