# IMP-ADR011-02: ADR-011 Phase 2 — Parsing Caller Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-011 Phase 2: migrate internal callers to `javdb.parsing`, move index-stage filtering into `javdb.pipeline.index_selection`, and reduce `javdb.spider.parser` to a thin compatibility Adapter.

**Architecture:** Parsing returns parsed page data. Pipeline selection decides which parsed entries a run should process. API services, migration tools, storage code, ops tooling, and tests should stop importing parser symbols from `apps.api.parsers` or `apps.api.models`.

**Tech Stack:** Python 3.11, pytest, Markdown docs.

**Source spec:** [ADR-011](../adr/ADR-011-javdb-parsing-module.md), D1-D9 Phase 2.

**Non-negotiable:** This phase is behavior-preserving. Do not alter parser output, Spider selection results, fallback behavior, URL normalization, legacy dict shapes, or edge-case handling while moving imports and selection logic.

---

## Files

| Path | Responsibility |
|---|---|
| `apps/api/services/*.py` | Import parser functions/models from `javdb.parsing`. |
| `javdb/spider/*.py`, `javdb/spider/fetch/*.py`, `javdb/spider/detail/*.py` | Import parsing and selection through new domain modules. |
| `javdb/migrations/tools/*.py` | Import parser helpers from `javdb.parsing`. |
| `javdb/storage/**/*.py`, `javdb/storage/repos/*.py` | Import parser helpers from `javdb.parsing.common`. |
| `apps/cli/ops/profile_hot_paths.py` | Profile `javdb.parsing` as the canonical parser entry. |
| `javdb/pipeline/index_selection.py` | New home for index-stage phase/ad hoc filtering. |
| `javdb/spider/parser.py` | Thin legacy Adapter over parsing and index selection. |
| `tests/**/*.py` | Primary tests import from `javdb.parsing`; compatibility tests are isolated. |
| `docs/handbook/en/developer/api-usage-guide.md`, `docs/handbook/zh/developer/api-usage-guide.md` | Update developer import guidance to `javdb.parsing`. |

---

## Task 1: Migrate Non-Adapter Parser Imports

**Files:**
- Modify: API services, migration tools, storage modules, ops profiling, tests.

- [ ] Replace non-Adapter imports of `apps.api.parsers` with `javdb.parsing`.
- [ ] Replace non-Adapter imports of `apps.api.parsers.common` with `javdb.parsing.common`.
- [ ] Replace non-Adapter imports of `apps.api.parsers.search_exact` with `javdb.parsing.search_exact`.
- [ ] Replace non-Adapter imports of parser dataclasses from `apps.api.models` with `javdb.parsing.models`.
- [ ] Leave `apps/api/parsers/*`, `apps/api/models.py`, and dedicated compatibility tests as the only old-path imports.
- [ ] Run:

```bash
pytest tests/unit/test_api_parsers.py tests/unit/test_api_common.py tests/unit/test_video_code_search.py tests/parity/test_parser_parity.py -v
```

## Task 2: Add `javdb.pipeline.index_selection`

**Files:**
- Create: `javdb/pipeline/index_selection.py`
- Create or modify: focused index-selection tests.

- [ ] Move phase 1 / phase 2 filtering out of `javdb.spider.parser.parse_index()`.
- [ ] Preserve current ad hoc mode behavior.
- [ ] Preserve release-date filtering, including today/yesterday logic.
- [ ] Preserve subtitle and magnet tag handling.
- [ ] Preserve P2 rate/comment thresholds and invalid score handling.
- [ ] Preserve no-video-code exclusion behavior.
- [ ] Preserve legacy entry dict output exactly.
- [ ] Add focused tests for phase 1, phase 2, ad hoc mode, ignored release date, invalid rate/comment, no video code, subtitle/magnet tags, and legacy dict output.
- [ ] Run:

```bash
pytest tests/unit/test_parser.py -v
```

## Task 3: Thin `javdb.spider.parser`

**Files:**
- Modify: `javdb/spider/parser.py`
- Modify: Spider tests.

- [ ] Reduce `javdb.spider.parser` to a compatibility Adapter over `javdb.parsing` and `javdb.pipeline.index_selection`.
- [ ] Keep the existing function names and return shapes for callers that still use it.
- [ ] Add a clear module comment that this Adapter is temporary and deleted by IMP-ADR011-03.
- [ ] Run:

```bash
pytest tests/unit/test_parser.py tests/unit/test_maintenance_detection.py tests/integration/test_spider_gateway.py -v
```

## Task 4: Migrate Spider Runtime Callers

**Files:**
- Modify: `javdb/spider/fetch/index.py`
- Modify: `javdb/spider/fetch/index_parallel.py`
- Modify: `javdb/spider/fetch/fallback.py`
- Modify: `javdb/spider/detail/parallel_mode.py`
- Modify: `javdb/infra/request.py`
- Modify: other Spider runtime callers found by grep.

- [ ] Move callers that need raw parsing to `javdb.parsing`.
- [ ] Move callers that need phase filtering to `javdb.pipeline.index_selection`.
- [ ] Keep legacy wrapper calls only where migration would require behavior work outside this phase.
- [ ] Run:

```bash
pytest tests/unit/test_parser.py tests/integration/test_spider_gateway.py -v
```

## Task 5: Migrate Documentation

**Files:**
- Modify: `docs/handbook/en/developer/api-usage-guide.md`
- Modify: `docs/handbook/zh/developer/api-usage-guide.md`
- Modify: `javdb/spider/README.md`
- Modify: `javdb/storage/repos/README.md`
- Modify: any top-level README only if it teaches parser import paths.

- [ ] Update primary developer import guidance to `javdb.parsing`.
- [ ] Mark `apps.api.parsers`, `apps.api.models`, and `javdb.spider.parser` as transitional compatibility paths where they are mentioned.
- [ ] Confirm README/wiki updates are not needed unless user-facing setup or usage guidance changed.

## Task 6: Phase 2 Grep Gate

- [ ] Run:

```bash
rg -n "from apps\.api\.parsers|import apps\.api\.parsers|from apps\.api\.models|import apps\.api\.models" apps javdb tests docs --glob '*.py' --glob '*.md'
```

- [ ] Confirm all remaining hits are one of:
  - compatibility Adapter modules;
  - explicit compatibility tests;
  - historical ADR/IMP/archive references;
  - transitional documentation that names the path as legacy.
- [ ] Run:

```bash
rg -n "from javdb\.spider\.parser|import javdb\.spider\.parser" apps javdb tests docs --glob '*.py' --glob '*.md'
```

- [ ] Confirm remaining hits are either active legacy callers intentionally deferred to IMP-ADR011-03 or historical references.

## Task 7: Phase 2 Verification

- [ ] Run parser parity tests:

```bash
pytest tests/parity/test_parser_parity.py -v
```

- [ ] Run Spider and API service tests:

```bash
pytest tests/unit/test_parser.py tests/integration/test_spider_gateway.py tests/unit/test_api_system_service.py tests/unit/test_video_code_search.py -v
```

- [ ] Confirm golden-output fixtures still match pre-migration output.
- [ ] Commit:

```bash
git add apps javdb tests docs
git commit -m "refactor(parsing): migrate callers to javdb parsing"
```
