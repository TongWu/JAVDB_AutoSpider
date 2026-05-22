# IMP-ADR011-03: ADR-011 Phase 3 — Delete Parsing Compatibility

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-011 Phase 3: remove temporary parser compatibility Adapters after internal callers and developer docs use `javdb.parsing`.

**Architecture:** `javdb.parsing` is the only parser Interface. `javdb.pipeline.index_selection` owns index-stage selection. `apps.api` may keep API-local schemas, but it must not own or re-export parser symbols. `javdb.spider.parser` is deleted rather than preserved as a second parser Interface.

**Tech Stack:** Python 3.11, pytest, grep gates, Markdown docs.

**Source spec:** [ADR-011](../adr/ADR-011-javdb-parsing-module.md), D1-D9 Phase 3.

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

- [ ] Run:

```bash
rg -n "from apps\.api\.parsers|import apps\.api\.parsers|apps\.api\.parsers" apps javdb tests docs --glob '*.py' --glob '*.md'
rg -n "from apps\.api\.models|import apps\.api\.models|apps\.api\.models" apps javdb tests docs --glob '*.py' --glob '*.md'
rg -n "from javdb\.spider\.parser|import javdb\.spider\.parser|javdb\.spider\.parser" apps javdb tests docs --glob '*.py' --glob '*.md'
```

- [ ] Classify every hit as historical documentation, compatibility code to delete in this phase, or a missed active caller.
- [ ] Migrate any missed active caller to `javdb.parsing` or `javdb.pipeline.index_selection`.
- [ ] Do not delete compatibility modules until active callers are gone.

## Task 2: Delete API Parser Compatibility

**Files:**
- Delete: `apps/api/parsers/`
- Modify: tests and docs that still reference old parser paths.

- [ ] Delete `apps/api/parsers/__init__.py`.
- [ ] Delete `apps/api/parsers/common.py`.
- [ ] Delete `apps/api/parsers/index_parser.py`.
- [ ] Delete `apps/api/parsers/detail_parser.py`.
- [ ] Delete `apps/api/parsers/tag_parser.py`.
- [ ] Delete `apps/api/parsers/search_exact.py`.
- [ ] Remove compatibility tests that only prove old parser paths work.
- [ ] Run:

```bash
pytest tests/unit/test_api_parsers.py tests/unit/test_api_common.py tests/unit/test_api_tag_parser.py tests/unit/test_api_tag_parser_security.py tests/parity/test_parser_parity.py -v
```

## Task 3: Remove Parser Re-Exports From `apps.api.models`

**Files:**
- Modify: `apps/api/models.py`
- Modify: model tests.

- [ ] Remove parser dataclass/sentinel re-exports from `apps.api.models`.
- [ ] Keep or create API-local schema modules only if API handlers still need local request/response schemas.
- [ ] Remove compatibility tests that import parser dataclasses from `apps.api.models`.
- [ ] Run:

```bash
pytest tests/unit/test_api_models.py -v
```

## Task 4: Delete `javdb.spider.parser`

**Files:**
- Delete: `javdb/spider/parser.py`
- Modify: Spider runtime imports and tests if any remain.

- [ ] Move any remaining raw parsing imports to `javdb.parsing`.
- [ ] Move any remaining phase-filtering imports to `javdb.pipeline.index_selection`.
- [ ] Move generic HTML validation helpers to the appropriate runtime module if they are still needed.
- [ ] Delete `javdb/spider/parser.py`.
- [ ] Run:

```bash
pytest tests/unit/test_parser.py tests/unit/test_maintenance_detection.py tests/integration/test_spider_gateway.py -v
```

## Task 5: Remove Transitional Documentation

**Files:**
- Modify: developer docs and README files that mention old parser paths.

- [ ] Remove guidance that teaches `apps.api.parsers` as a usable import path.
- [ ] Remove guidance that teaches parser dataclasses from `apps.api.models`.
- [ ] Remove guidance that presents `javdb.spider.parser` as the parser Interface.
- [ ] Keep historical ADR/IMP/archive references intact.
- [ ] Confirm root `README.md`, `README_CN.md`, and the wiki do not contain stale parser usage guidance.

## Task 6: Final Grep Gate

- [ ] Run:

```bash
rg -n "from apps\.api\.parsers|import apps\.api\.parsers|apps\.api\.parsers" apps javdb tests docs --glob '*.py' --glob '*.md'
rg -n "from apps\.api\.models|import apps\.api\.models|apps\.api\.models" apps javdb tests docs --glob '*.py' --glob '*.md'
rg -n "from javdb\.spider\.parser|import javdb\.spider\.parser|javdb\.spider\.parser" apps javdb tests docs --glob '*.py' --glob '*.md'
```

- [ ] Confirm remaining hits are historical ADR/IMP/archive records only.
- [ ] Run:

```bash
python -c "import javdb.parsing; print('javdb.parsing OK')"
```

## Task 7: Phase 3 Verification

- [ ] Run parser parity tests:

```bash
pytest tests/parity/test_parser_parity.py -v
```

- [ ] Run relevant parser, Spider, API, migration, and storage tests:

```bash
pytest tests/unit/test_api_models.py tests/unit/test_api_common.py tests/unit/test_api_parsers.py tests/unit/test_api_tag_parser.py tests/unit/test_video_code_search.py tests/unit/test_parser.py tests/integration/test_spider_gateway.py tests/integration/test_align_inventory_with_moviehistory.py -v
```

- [ ] Confirm golden-output fixtures still match pre-migration output.
- [ ] Confirm no parser Implementation or parser re-export remains under `apps.api`.
- [ ] Commit:

```bash
git add apps javdb tests docs
git commit -m "refactor(parsing): remove legacy parser adapters"
```
