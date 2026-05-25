# IMP-ADR011-03: ADR-011 Phase 3 — Delete Parsing Compatibility

**Status:** Completed — delivered 2026-05-26.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-011 Phase 3: remove temporary parser compatibility Adapters after internal callers and developer docs use `javdb.parsing`.

**Architecture:** `javdb.parsing` is the only parser Interface. `javdb.pipeline.index_selection` owns index-stage selection. `apps.api` may keep API-local schemas, but it must not own or re-export parser symbols. `javdb.spider.parser` is deleted rather than preserved as a second parser Interface.

**Tech Stack:** Python 3.11, pytest, grep gates, Markdown docs.

**Source spec:** [ADR-011](ADR-011-javdb-parsing-module.md), D1-D9 Phase 3.

**Non-negotiable:** Compatibility deletion must have no parser behavior delta. If deleting a wrapper reveals behavior drift, stop and fix the migration or fixtures before deleting the wrapper.

---

## Files

| Path | Responsibility |
|---|---|
| `apps/api/parsers/` | Delete parser re-export Adapters. |
| `apps/api/models.py` | Remove parser dataclass re-exports; keep only API-local schemas if needed. |
| `javdb/spider/parser.py` | Delete legacy Spider parser Adapter after callers move. |
| `apps/**/*.py`, `javdb/**/*.py`, `tests/**/*.py` | Remove final legacy imports. |
| `docs/handbook/en/developer/api-usage-guide.md`, `docs/handbook/zh/developer/api-usage-guide.md` | Remove transitional old-path guidance. |
| `javdb/spider/README.md`, `javdb/legacy/README.md`, `apps/cli/ops/profile_hot_paths.py` | Remove or update legacy parser references. |

---

## Task 1: Prove No Active Old-Path Callers Remain

- [x] Run:

```bash
rg -n "from apps\.api\.parsers|import apps\.api\.parsers|apps\.api\.parsers" apps javdb tests docs --glob '*.py' --glob '*.md'
rg -n "from apps\.api\.models|import apps\.api\.models|apps\.api\.models" apps javdb tests docs --glob '*.py' --glob '*.md'
rg -n "from javdb\.spider\.parser|import javdb\.spider\.parser|javdb\.spider\.parser" apps javdb tests docs --glob '*.py' --glob '*.md'
```

- [x] Classify every hit as historical documentation, compatibility code to delete in this phase, or a missed active caller.
- [x] Migrate any missed active caller to `javdb.parsing` or `javdb.pipeline.index_selection`.
- [x] Do not delete compatibility modules until active callers are gone.

## Task 2: Delete API Parser Compatibility

**Files:**
- Delete: `apps/api/parsers/`
- Modify: tests and docs that still reference old parser paths.

- [x] Delete `apps/api/parsers/__init__.py`.
- [x] Delete `apps/api/parsers/common.py`.
- [x] Delete `apps/api/parsers/index_parser.py`.
- [x] Delete `apps/api/parsers/detail_parser.py`.
- [x] Delete `apps/api/parsers/tag_parser.py`.
- [x] Delete `apps/api/parsers/search_exact.py`.
- [x] Remove compatibility tests that only prove old parser paths work.
- [x] Run:

```bash
pytest tests/unit/test_api_parsers.py tests/unit/test_api_common.py tests/unit/test_api_tag_parser.py tests/unit/test_api_tag_parser_security.py tests/parity/test_parser_parity.py -v
```

## Task 3: Remove Parser Re-Exports From `apps.api.models`

**Files:**
- Modify: `apps/api/models.py`
- Modify: model tests.

- [x] Remove parser dataclass/sentinel re-exports from `apps.api.models`.
- [x] Keep or create API-local schema modules only if API handlers still need local request/response schemas.
- [x] Remove compatibility tests that import parser dataclasses from `apps.api.models`.
- [x] Run:

```bash
pytest tests/unit/test_api_models.py -v
```

## Task 4: Delete `javdb.spider.parser`

**Files:**
- Delete: `javdb/spider/parser.py`
- Modify: Spider runtime imports and tests if any remain.

- [x] Move any remaining raw parsing imports to `javdb.parsing`.
- [x] Move any remaining phase-filtering imports to `javdb.pipeline.index_selection`.
- [x] Move generic HTML validation helpers to the appropriate runtime module if they are still needed.
- [x] Delete `javdb/spider/parser.py`.
- [x] Run:

```bash
pytest tests/unit/test_parser.py tests/unit/test_maintenance_detection.py tests/integration/test_spider_gateway.py -v
```

## Task 5: Remove Transitional Documentation

**Files:**
- Modify: developer docs and README files that mention old parser paths.

- [x] Remove guidance that teaches `apps.api.parsers` as a usable import path.
- [x] Remove guidance that teaches parser dataclasses from `apps.api.models`.
- [x] Remove guidance that presents `javdb.spider.parser` as the parser Interface.
- [x] Keep historical ADR/IMP/archive references intact.
- [x] Confirm root `README.md`, `README_CN.md`, and the wiki do not contain stale parser usage guidance.

## Task 6: Final Grep Gate

- [x] Run:

```bash
rg -n "from apps\.api\.parsers|import apps\.api\.parsers|apps\.api\.parsers" apps javdb tests docs --glob '*.py' --glob '*.md'
rg -n "from apps\.api\.models|import apps\.api\.models|apps\.api\.models" apps javdb tests docs --glob '*.py' --glob '*.md'
rg -n "from javdb\.spider\.parser|import javdb\.spider\.parser|javdb\.spider\.parser" apps javdb tests docs --glob '*.py' --glob '*.md'
```

- [x] Confirm remaining hits are historical ADR/IMP/archive records only.
- [x] Run:

```bash
python -c "import javdb.parsing; print('javdb.parsing OK')"
```

## Task 7: Phase 3 Verification

- [x] Run parser parity tests:

```bash
pytest tests/parity/test_parser_parity.py -v
```

- [x] Run relevant parser, Spider, API, migration, and storage tests:

```bash
pytest tests/unit/test_api_models.py tests/unit/test_api_common.py tests/unit/test_api_parsers.py tests/unit/test_api_tag_parser.py tests/unit/test_video_code_search.py tests/unit/test_parser.py tests/integration/test_spider_gateway.py tests/integration/test_align_inventory_with_moviehistory.py -v
```

- [x] Confirm golden-output fixtures still match pre-migration output.
- [x] Confirm no parser Implementation or parser re-export remains under `apps.api`.
- [x] Commit:

```bash
git add apps javdb tests docs
git commit -m "refactor(parsing): remove legacy parser adapters"
```
