# ADR-020: Parser Interface Consolidation

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Implemented                                                          |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md) (the parsing module; intended this shim's deletion); [IMP-ADR020-01](IMP-ADR020-01-consolidate-parser.md) (Phase 1 — execution) |

> Originated from the 2026-05-29 architecture review (Candidate D): [architecture-review-2026-05-29.html](../architecture/architecture-review-2026-05-29.html).

## Context

Understanding "parse a JavDB page" today requires knowing **two parser entrypoints** and a **two-step magnet dance**:

1. **The shim.** `javdb/spider/parse_legacy_adapters.py` (118 lines) survived [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md) Phase 3 — that phase deleted the original `parser.py` but *relocated* its wrappers here instead of deleting them. It re-exposes `extract_video_code` (a pure pass-through to `javdb.parsing.common`), `parse_index` (wraps `parse_index_page` + applies `pipeline.index_selection.select_index_entries` + returns legacy dicts), and `parse_detail` (wraps `parse_detail_page` + reshapes `MovieDetail` into a legacy 6-tuple). It has **6+ live importers** (`spider/fetch/fallback.py`, `spider/detail/parallel_mode.py`, `legacy/_spider_legacy.py`, two `migrations/tools/*`, `apps/cli/ops/profile_hot_paths.py`) plus tests.
2. **The two-step.** Parsing returns *raw* magnets (`MovieDetail.magnets`); a *separate, later* `javdb/spider/magnet_extractor.py:extract_magnets(...)` call categorizes them into `subtitle / hacked_subtitle / hacked_no_subtitle / no_subtitle`. The hot path does this at `javdb/spider/detail/runner.py:700` (`extract_magnets(data['magnets'], idx_str)`), far from where parsing happened. Every detail caller must remember both steps.

So a caller must (a) pick which parser to import and (b) remember to categorize afterward. This is shallow: the shim adds shape-translation without behavior, and the categorization step leaks an internal detail of "what we extract from a detail page" into every caller.

## Decision

Consolidate to **one parser interface** — `javdb.parsing` returns *finished domain objects* — and delete the shim. Concretely:

### Design Decisions

**D1. One parser interface; delete the shim.** `javdb.parsing` (`parse_detail_page` / `parse_index_page` + the `MovieDetail` accessors) is the single entrypoint. `extract_video_code` is imported from `javdb.parsing.common` directly. `javdb/spider/parse_legacy_adapters.py` is deleted once all callers migrate.

**D2. Magnet categorization moves *into* the parsing layer.** Relocate the pure categorization algorithm **and its Rust-first dispatch** from `javdb/spider/magnet_extractor.py` into `javdb/parsing/magnet_categorize.py`. This is layer-legal and **idiomatic** — `javdb/parsing/` already follows exactly this "prefer `javdb.rust_core`, fall back to a frozen Python mirror" pattern for its parsers (`parsing/__init__.py`, `parsing/fallback/`). `javdb/spider/magnet_extractor.py` becomes a thin **re-export** preserving `extract_magnets` and `_parse_size` (consumed by `javdb/pipeline/policies.py:10`). The categorization entry point is the parsing-layer **free function** `magnet_categorize.categorize(magnets)`, which callers apply to `detail.get_magnets_as_legacy()`. **A method on the detail object cannot be the interface** — in production `parse_detail_page()` returns a Rust `RustMovieDetail` that can't carry a Python `categorize_magnets()` method; only `get_magnets_as_legacy()` is uniform across the Rust and Python detail objects. (This corrects the original "finished-object method" framing — see Status Log.)

**D3. Collapse the hot-path two-step.** The fetch backends emit a **pre-categorized** `data['magnet_links']` (via `magnet_categorize.categorize(detail.get_magnets_as_legacy())`) instead of raw `data['magnets']`; `runner.py:700` reads it directly and the separate `extract_magnets` call is removed. This eliminates the last parse-then-extract two-step in the production path. Shipped as a **separately-revertible commit** so a smoke-test delta can be isolated.

**D4. Index selection stays in `javdb.pipeline`.** `select_index_entries` reads config (`PHASE2_MIN_RATE`, …) — it is business policy, not parsing. Moving it into `javdb.parsing` would **invert** the clean dependency direction (`pipeline → spider → parsing`). Callers that need selected entries call `parse_index_page` + `select_index_entries` directly (exactly what the tests already do). The shim's only added value over this — an empty-list diagnostic log — is inlined where it matters.

**D5. Migrate every caller, including frozen code.** Production spider flow, the legacy spider, and the migration tools all migrate so the shim can be deleted. `javdb/legacy/` is frozen reference code — it gets a **minimal import swap only** (legacy `parse_index`/`parse_detail` → canonical `parse_*_page` + `select_index_entries`, reproducing its local tuple), **not** an internal refactor of its magnet two-step.

**D6. Preserve Rust acceleration (the #1 hazard).** The relocation must move the **Rust-first dispatch**, not just the Python fallback — otherwise categorization silently drops to the (frozen, slower) Python path. `tests/unit/test_magnet_parity.py` (Rust vs Python parity) is the guard and must stay green throughout.

## Consequences

### Positive

- **One parser interface, not two** — callers learn `javdb.parsing` only.
- **No parse-then-extract two-step** — a detail parse yields categorized magnets; the hot-path second call disappears (D3).
- **locality** — "what we extract from a detail page," including magnet categorization, lives in one layer.
- **Deletes a 118-line shallow shim** plus its logging alias (`infra/logging.py:91`).
- **Test surface improves** — removes the tautological "shim is a thin adapter" tests; magnet categorization gains a parser-level test co-located with the model.

### Negative

- **The fetch/fallback flow is the real work** — `fallback.py` carries a 6-tuple across ~20 return sites; re-sourcing it from `MovieDetail` is careful, mechanical churn.
- **Touches frozen `javdb/legacy/`** (import swap only) — minimal, but non-zero.
- **Relocation risk** — D6's Rust-dispatch hazard requires care + parity tests.

### Risks

- **Rust bypass** (D6) — mitigated by moving the dispatch wholesale + `test_magnet_parity.py`.
- **Fetch-engine boundary** — the `data` dict crosses thread/queue boundaries; keep passing plain dicts/strings (the categorized dict is plain `str/int/None`), never dataclasses, across the queue.
- **Migration tools operate on persisted data** — a silent shape change corrupts backfill; covered by the existing align/migration tests.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| All phases | [IMP-ADR020-01](IMP-ADR020-01-consolidate-parser.md) | Behavior baseline → categorization into parsing → migrate non-spider callers → migrate spider flow + collapse two-step → migrate legacy → migrate index/tests + delete shim | — |

## Out of Scope

- Index-selection policy itself (stays in `javdb.pipeline`, D4).
- Refactoring `javdb/legacy/`'s internals (import swap only, D5).
- The SELECT-skeleton / parser internals beyond magnet categorization.

## Status Log

- 2026-05-29: Proposed (from architecture review Candidate D grilling).
- 2026-05-29: Design correction (during implementation, Phase 2). `MovieDetail.categorize_magnets()` (the original D2 "finished-object method") is **dead on the production path** — `parse_detail_page()` returns a Rust `RustMovieDetail` that lacks the method. D2/D3 amended: the canonical interface is the parsing-layer free function `magnet_categorize.categorize(detail.get_magnets_as_legacy())` (uniform across Rust/Python detail objects, matches `runner.py:700`). The dead method + its test were removed.
- 2026-05-29: Implemented (all phases). Phases 1-2 (categorization relocated to `javdb/parsing/magnet_categorize.py`; non-spider callers migrated) shipped in PR #123. Phases 3-5 (spider detail flow migrated + hot-path two-step collapsed so backends emit pre-categorized `data['magnet_links']`; legacy spider import-swapped; `test_parser.py` repointed; **`parse_legacy_adapters.py` deleted** + logging alias removed) shipped in the stacked Phase 3-5 PR. `grep -rn parse_legacy_adapters javdb apps tests` is empty; `test_magnet_parity.py` green throughout (D6 Rust dispatch preserved). Status → Implemented; folder archives once both PRs merge.
- 2026-05-30: Fallback-policy dimension amended by [ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md). D6's value parity was a **migration-time** guard; the steady-state fallback is now **shape-contracted** (Best-Effort tier — `test_magnet_parity.py` / `tests/parity/` retired in favour of `tests/unit/test_fallback_shape.py`). The proxy pool / ban manager became **Rust-Required** (no Python mirror). ADR-020's interface consolidation is unchanged.
