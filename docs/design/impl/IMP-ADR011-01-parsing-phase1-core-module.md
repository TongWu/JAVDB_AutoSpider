# IMP-ADR011-01: ADR-011 Phase 1 — Parsing Core Module

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-011 Phase 1: establish `javdb.parsing` as the canonical parser module while preserving existing parser behavior exactly.

**Architecture:** Move parser models, common helpers, Rust-first dispatch, exact-search helpers, and frozen Python fallbacks under `javdb/parsing`. Keep `apps.api.parsers`, `apps.api.models`, and `javdb.spider.parser` as compatibility Adapters.

**Tech Stack:** Python 3.11, BeautifulSoup, Rust extension dispatch through `javdb.rust_core`, pytest, Markdown docs.

**Source spec:** [ADR-011](../adr/ADR-011-javdb-parsing-module.md), D1-D6, D8-D9 Phase 1.

**Non-negotiable:** This phase is behavior-preserving. Do not change parser output, fallback behavior, Rust-first dispatch semantics, legacy adapter return shapes, URL normalization, sentinel values, tag interpretation, or edge-case details.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/parsing/__init__.py` | New Rust-first production parser Interface. |
| `javdb/parsing/models.py` | Parser dataclasses and sentinels moved from `apps.api.models`. |
| `javdb/parsing/common.py` | Shared parser helpers moved from `apps.api.parsers.common`. |
| `javdb/parsing/search_exact.py` | Exact video-code search helpers moved from `apps.api.parsers.search_exact`. |
| `javdb/parsing/fallback/index_parser.py` | Frozen BeautifulSoup fallback for index/category/top pages. |
| `javdb/parsing/fallback/detail_parser.py` | Frozen BeautifulSoup fallback for detail pages. |
| `javdb/parsing/fallback/tag_parser.py` | Frozen BeautifulSoup fallback for tag pages. |
| `apps/api/parsers/__init__.py` | Compatibility Adapter re-exporting from `javdb.parsing`. |
| `apps/api/parsers/*.py` | Compatibility Adapters for old submodule imports. |
| `apps/api/models.py` | Compatibility Adapter re-exporting from `javdb.parsing.models`. |
| `javdb/spider/parser.py` | Temporary runtime Adapter importing parsing from `javdb.parsing`. |
| `tests/unit/test_api_models.py` | Keep compatibility coverage while adding parsing-model primary tests. |
| `tests/unit/test_api_parsers.py`, `tests/unit/test_api_common.py`, `tests/unit/test_api_tag_parser.py` | Move primary imports to `javdb.parsing`; keep targeted compatibility tests. |
| `tests/parity/test_parser_parity.py` | Compare Rust dispatch with `javdb.parsing.fallback.*`. |

---

## Task 1: Capture Current Behavior Before Moving Code

**Files:**
- Modify: parser/model/parity tests only.

- [ ] Add or update golden-output fixtures for representative index, detail, category, top, and tag pages.
- [ ] Include edge cases for missing actors, actor sentinels, supporting actor URL normalization, missing rates/comments, invalid scores, magnet tags, subtitle tags, relative URLs, and no-video-code entries.
- [ ] Add a parity test that imports current production parser outputs and records the exact canonicalized dataclass/dict output.
- [ ] Run:

```bash
pytest tests/unit/test_api_models.py tests/unit/test_api_common.py tests/unit/test_api_parsers.py tests/unit/test_api_tag_parser.py tests/parity/test_parser_parity.py -v
```

- [ ] Confirm the tests pass before structural moves begin.

## Task 2: Create `javdb.parsing.models`

**Files:**
- Create: `javdb/parsing/models.py`
- Modify: `apps/api/models.py`
- Modify: model tests.

- [ ] Move parser dataclasses and sentinels from `apps.api.models` to `javdb.parsing.models`.
- [ ] Make `apps.api.models` re-export the moved parser objects without changing object identity.
- [ ] Update primary model tests to import from `javdb.parsing.models`.
- [ ] Keep compatibility tests proving `apps.api.models.MovieDetail is javdb.parsing.models.MovieDetail`.
- [ ] Run:

```bash
pytest tests/unit/test_api_models.py -v
```

## Task 3: Create `javdb.parsing.common`

**Files:**
- Create: `javdb/parsing/common.py`
- Modify: `apps/api/parsers/common.py`
- Modify: common-helper tests.

- [ ] Move shared parser helpers to `javdb.parsing.common`.
- [ ] Make `apps.api.parsers.common` re-export from `javdb.parsing.common`.
- [ ] Preserve helper behavior exactly, including URL normalization and supporting-actor JSON behavior.
- [ ] Update primary tests to import from `javdb.parsing.common`.
- [ ] Keep compatibility tests for the old helper import path.
- [ ] Run:

```bash
pytest tests/unit/test_api_common.py tests/unit/test_commit_session_bulk.py tests/unit/test_batch_c_movie_history_id.py tests/unit/test_rollback_pending_mode.py -v
```

## Task 4: Move Frozen Python Fallbacks

**Files:**
- Create: `javdb/parsing/fallback/__init__.py`
- Create: `javdb/parsing/fallback/index_parser.py`
- Create: `javdb/parsing/fallback/detail_parser.py`
- Create: `javdb/parsing/fallback/tag_parser.py`
- Modify: `apps/api/parsers/index_parser.py`
- Modify: `apps/api/parsers/detail_parser.py`
- Modify: `apps/api/parsers/tag_parser.py`
- Modify: parser tests.

- [ ] Move BeautifulSoup parser implementations under `javdb.parsing.fallback`.
- [ ] Make old parser submodules re-export from the new fallback modules.
- [ ] Update fallback tests to import from `javdb.parsing.fallback.*`.
- [ ] Keep compatibility tests for `apps.api.parsers.index_parser`, `detail_parser`, and `tag_parser`.
- [ ] Run:

```bash
pytest tests/unit/test_api_parsers.py tests/unit/test_api_tag_parser.py tests/unit/test_api_tag_parser_security.py -v
```

## Task 5: Move Rust-First Dispatch

**Files:**
- Create: `javdb/parsing/__init__.py`
- Modify: `apps/api/parsers/__init__.py`
- Modify: runtime dispatch tests.

- [ ] Move the Rust-first parser dispatch to `javdb.parsing.__init__`.
- [ ] Preserve `RUST_PARSERS_AVAILABLE` semantics exactly.
- [ ] Make `apps.api.parsers` re-export from `javdb.parsing`.
- [ ] Add tests proving `from javdb.parsing import parse_index_page` uses Rust when available and fallback when unavailable.
- [ ] Add tests proving old API imports expose the same callables during compatibility.
- [ ] Run:

```bash
pytest tests/parity/test_parser_parity.py tests/unit/test_api_parsers.py -v
```

## Task 6: Move Exact Search Helpers

**Files:**
- Create: `javdb/parsing/search_exact.py`
- Modify: `apps/api/parsers/search_exact.py`
- Modify: video-code search tests.

- [ ] Move exact video-code search helpers to `javdb.parsing.search_exact`.
- [ ] Make `apps.api.parsers.search_exact` re-export from the new module.
- [ ] Update primary tests to import from `javdb.parsing.search_exact`.
- [ ] Keep compatibility tests for old import paths.
- [ ] Run:

```bash
pytest tests/unit/test_video_code_search.py tests/integration/test_align_inventory_with_moviehistory.py -v
```

## Task 7: Repoint Spider Parser Adapter

**Files:**
- Modify: `javdb/spider/parser.py`
- Modify: Spider parser tests.

- [ ] Change `javdb.spider.parser` to import parsing from `javdb.parsing`.
- [ ] Preserve `parse_index()`, `parse_detail()`, `result_to_dict()`, validation helpers, and legacy return shapes exactly.
- [ ] Add or keep tests that compare pre-migration legacy Spider outputs to post-migration outputs.
- [ ] Run:

```bash
pytest tests/unit/test_parser.py tests/unit/test_maintenance_detection.py tests/integration/test_spider_gateway.py -v
```

## Task 8: Phase 1 Gate

- [ ] Run parser/model/common/parity tests:

```bash
pytest tests/unit/test_api_models.py tests/unit/test_api_common.py tests/unit/test_api_parsers.py tests/unit/test_api_tag_parser.py tests/unit/test_api_tag_parser_security.py tests/parity/test_parser_parity.py -v
```

- [ ] Run Spider smoke tests:

```bash
pytest tests/unit/test_parser.py tests/integration/test_spider_gateway.py -v
```

- [ ] Run import compatibility checks:

```bash
python -c "from javdb.parsing import parse_index_page; from apps.api.parsers import parse_index_page as old; assert parse_index_page is old"
python -c "from javdb.parsing.models import MovieDetail; from apps.api.models import MovieDetail as old; assert MovieDetail is old"
```

- [ ] Confirm golden-output fixtures match pre-migration output.
- [ ] Confirm no production caller behavior was intentionally changed.
- [ ] Commit:

```bash
git add javdb/parsing apps/api/parsers apps/api/models.py javdb/spider/parser.py tests
git commit -m "refactor(parsing): establish javdb parsing module"
```
