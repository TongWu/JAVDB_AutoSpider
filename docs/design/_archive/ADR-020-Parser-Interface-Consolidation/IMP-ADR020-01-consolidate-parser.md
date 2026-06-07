# Parser Interface Consolidation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `javdb.parsing` the single parser interface returning finished domain objects, eliminate the parse-then-extract two-step (including in the hot path), migrate every caller, and delete `javdb/spider/parse_legacy_adapters.py`.

**Architecture:** Move magnet categorization (with its Rust-first dispatch) down into `javdb/parsing/magnet_categorize.py`, exposed as the free function `categorize(magnets)`; callers apply it to `detail.get_magnets_as_legacy()`; `javdb/spider/magnet_extractor.py` becomes a re-export. Index selection stays in `javdb.pipeline`. Each phase is independently shippable; the shim dies last.

> **Design correction (2026-05-29, during Phase 2):** `MovieDetail.categorize_magnets()` is **dead on the production path** — `parse_detail_page()` returns a Rust `RustMovieDetail` that lacks the method (only `get_magnets_as_legacy()` is uniform across the Rust and Python detail objects). The canonical interface is the free function `magnet_categorize.categorize(detail.get_magnets_as_legacy())` (matches `runner.py:700`). **Every `categorize_magnets()` reference below means that free function** — the method was removed.

**Tech Stack:** Python 3.11+, pytest, maturin/Rust (parity only — no Rust edits). Single repo.

**Related:** [ADR-020](ADR-020-parser-interface-consolidation.md)

**Status:** Completed (2026-05-30 closeout) — all phases implemented across PR #123 (Phases 1-2) and PR #124 (Phases 3-5). Both PRs are merged, and the ADR folder is archived.

---

## Decisions baked in (from grilling)

- **D2 → Option 1:** categorization moves *into* `javdb.parsing` (not a spider façade).
- **D3 → collapse:** the hot-path two-step at `runner.py:700` is removed (backends emit pre-categorized `data['magnet_links']`).
- **D5 → full migration:** legacy + migration tools migrate; the shim is deleted.

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Create | `javdb/parsing/magnet_categorize.py` | Relocated pure categorizer **+ Rust-first dispatch** (the algorithm from `magnet_extractor.py`) |
| Modify | `javdb/spider/magnet_extractor.py` | Becomes a re-export of `extract_magnets` + `_parse_size` from the new location (back-compat) |
| Modify | `javdb/parsing/models.py` | Add `MovieDetail.categorize_magnets(index=None) -> dict` (near `get_magnets_as_legacy`, `:199`) |
| Modify | `javdb/spider/detail/parallel_mode.py` | Build `data` from `MovieDetail` accessors; emit pre-categorized `data['magnet_links']` |
| Modify | `javdb/spider/fetch/fallback.py` | Re-source the 6-tuple from `MovieDetail` (drop shim import) |
| Modify | `javdb/spider/detail/runner.py` | `:700` reads `data['magnet_links']` directly; drop the `extract_magnets` call |
| Modify | `javdb/legacy/_spider_legacy.py` | Minimal import swap (shim → canonical); keep internal magnet step on the re-export |
| Modify | `javdb/migrations/tools/update_history_format.py`, `migrate_v7_to_v8.py` | Use `parse_detail_page` + `categorize_magnets()` / accessors |
| Modify | `apps/cli/ops/profile_hot_paths.py` | Repoint the benchmark to the finished-object path |
| Modify | `tests/unit/test_parser.py` | Repoint shim tests to canonical API + `categorize_magnets` |
| Modify | `javdb/infra/logging.py` | Remove the `parse_legacy_adapters` logging alias (`:91`) |
| Delete | `javdb/spider/parse_legacy_adapters.py` | The shim |

---

## Phase 0 — Pin behavior (no production edits)

- [x] Capture a green baseline: `pytest tests/unit/test_parser.py tests/unit/test_magnet_extractor.py tests/unit/test_magnet_parity.py tests/integration/test_align_inventory_with_moviehistory.py tests/smoke/test_spider_detail_runner.py -q`.
- [x] Confirm `tests/unit/test_magnet_parity.py` truly exercises Rust vs Python so it can guard D6 through every later phase.

## Phase 1 — Relocate categorization into `javdb.parsing` (additive)

- [x] Create `javdb/parsing/magnet_categorize.py`: move `_python_extract_magnets`, `infer_resolution`, `_parse_size`, `_sort_key` from `magnet_extractor.py:51-195` **and** the Rust-first dispatch (`magnet_extractor.py:16-44`) — so `categorize(magnets, index=None)` tries `javdb.rust_core.extract_magnets` first, falls back to the Python mirror. (Layer-legal: `parsing/__init__.py` already imports `javdb.rust_core`.)
- [x] Rewrite `javdb/spider/magnet_extractor.py` as a re-export: `from javdb.parsing.magnet_categorize import categorize as extract_magnets, _parse_size` (preserve both names — `pipeline/policies.py:10` imports `_parse_size`).
- [x] Add `MovieDetail.categorize_magnets(index=None) -> dict` in `javdb/parsing/models.py` (≈`:199`) feeding `[m.to_dict() for m in self.magnets]` through the categorizer. **Must** equal today's `extract_magnets(detail.get_magnets_as_legacy())` byte-for-byte.
- [x] **Verify:** `pytest tests/unit/test_magnet_extractor.py tests/unit/test_magnet_parity.py` (green via re-export) + a new test asserting `MovieDetail.categorize_magnets() == extract_magnets(detail.get_magnets_as_legacy())` on `test_parser.py` fixtures + `html/detailed_page_*.html`.

## Phase 2 — Migrate non-spider callers

- [x] `migrations/tools/update_history_format.py:33-34,65-73` → `parse_detail_page` + `detail.categorize_magnets()` + `detail.get_first_actor_name()`; drop shim + magnet_extractor imports.
- [x] `migrations/tools/migrate_v7_to_v8.py:466-482` (`_backfill_parse` discards magnets) → `parse_detail_page` + accessors; drop shim import.
- [x] `apps/cli/ops/profile_hot_paths.py:156-163` → repoint `bench_parse_detail_wrapper` to `parse_detail_page` + `categorize_magnets`; keep `bench_parse_detail_canonical` as the raw-parse baseline.
- [x] **Verify:** `python -c "import javdb.migrations.tools.update_history_format, javdb.migrations.tools.migrate_v7_to_v8"` + migration/align tests.

## Phase 3 — Migrate the spider detail flow + collapse the two-step (the real work)

- [x] `spider/detail/parallel_mode.py:41-58` (`_spider_parse_fn`) → `detail = parse_detail_page(html)`; build `data` from `detail.get_*()`; set `data['magnet_links'] = categorize(detail.get_magnets_as_legacy(), idx)` (from `javdb.parsing.magnet_categorize`; **pre-categorized**, D3).
- [x] `spider/fetch/fallback.py:4,506-665` → replace `parse_detail` with `parse_detail_page` + accessors, sourcing the existing internal 6-tuple from `MovieDetail` (keep the tuple as fallback.py's internal contract across its ~20 return sites; only change its *source*). Drop the shim import (`:4`).
- [x] `spider/detail/runner.py:700` → read `data['magnet_links']` directly; **delete** the `extract_magnets(data['magnets'], …)` call. This is the separately-revertible "collapse" commit (D3).
- [x] Update test monkeypatch targets that stub `runner.extract_magnets` (`tests/smoke/test_spider_detail_runner.py:313,420`, detail-runner unit tests) — categorization now happens in the backend, so the stub target moves (or the tests assert on `data['magnet_links']`).
- [x] **Boundary safety:** keep passing plain dicts/strings across the FetchEngine queue; never ship `MovieDetail`/`MagnetInfo` dataclasses through it. The categorized dict is plain `str/int/None`.
- [x] **Verify:** `pytest tests/smoke/test_spider_detail_runner.py tests/unit/test_detail_runner_work_distributor.py tests/unit/test_detail_runner_movie_claim.py -q`.

## Phase 4 — Migrate the legacy spider (minimal, frozen)

- [x] `legacy/_spider_legacy.py:39` → swap `from javdb.spider.parse_legacy_adapters import parse_index, parse_detail` for `parse_index_page` + `select_index_entries` (from `javdb.pipeline.index_selection`) + `parse_detail_page`, reproducing the local tuple exactly as `fallback.py` does. Do **not** refactor legacy's magnet two-step — keep `extract_magnets` (the re-export) at `:1465,1637`.
- [x] Read the ~40 lines around `_spider_legacy.py:1462-1464` first — if legacy already wraps parse output in a result object, the swap is smaller.
- [x] **Verify:** `pytest tests/unit/test_adr005_pr3a_repo_callers.py -q` (it monkeypatches `legacy.extract_magnets`) + `python -c "import javdb.legacy._spider_legacy"`.

## Phase 5 — Migrate the index path + tests, then delete the shim

- [x] `tests/unit/test_parser.py`: keep the `TestParseIndex` selection tests (`:106-203`, already use `parse_index_page` + `select_index_entries`); repoint the `parse_index`/`parse_detail` shim tests (`:19-48,243-451`) to the canonical API + `categorize_magnets`/accessors; drop the tautological "shim is a thin adapter" assertions.
- [x] Inline the shim's only real logic (the empty-list diagnostic log, `parse_legacy_adapters.py:52-60`) into any caller that needs it (legacy already logs page-empty conditions).
- [x] **Delete** `javdb/spider/parse_legacy_adapters.py`.
- [x] Remove the logging alias `javdb/infra/logging.py:91` (`'javdb.spider.parse_legacy_adapters': 'Parser'`).
- [x] **Grep gate:** `grep -rn "parse_legacy_adapters" javdb apps tests` returns only `_archive`/historical references.

## Verification gates

- [x] `pytest tests/unit/test_parser.py tests/unit/test_magnet_extractor.py tests/unit/test_magnet_parity.py tests/smoke/test_spider_detail_runner.py tests/integration/test_align_inventory_with_moviehistory.py -q` — all green.
- [x] `test_magnet_parity.py` green at every phase boundary (D6 Rust guard).
- [x] `python -c "import javdb.parsing; import javdb.spider; import javdb.legacy._spider_legacy"` — clean.
- [x] Grep gate passes (no live `parse_legacy_adapters` importers).
- [x] Update this IMP's `Status` to `Completed`. Folder archived on 2026-05-30 after PR #123 and PR #124 merged.

## Risks

- **Rust bypass (D6, #1 hazard):** the new `magnet_categorize.py` must keep the Rust-first dispatch, not just the Python fallback. `test_magnet_parity.py` is the guard — run it after Phase 1 and again after Phase 3.
- **fallback.py 6-tuple (~20 return sites):** keep the tuple shape verbatim; only re-source it from `MovieDetail`. A missed conversion silently changes a magnet/actor field.
- **Migration tools touch persisted data:** verify `update_history_format` reproduces `filtered_links` + `actor_info` exactly.
- **FetchEngine queue boundary:** plain dicts/strings only; the pre-categorized `magnet_links` dict is safe.

## Out of scope

- Index-selection policy (stays in `javdb.pipeline`).
- Refactoring `javdb/legacy/` internals (import swap only).
