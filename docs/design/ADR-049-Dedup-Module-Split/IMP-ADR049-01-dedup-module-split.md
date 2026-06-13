# IMP-ADR049-01: Split `dedup.py` into types / query / store — Implementation Plan

> **Status: 🔲 Proposed (2026-06-13).** Single PR; pure relocation, no behaviour change. Authored from [ADR-049](ADR-049-dedup-module-split.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Related:** [ADR-049](ADR-049-dedup-module-split.md) — Phase 1 (the only phase).

**Goal:** Replace the 779-line `javdb/spider/services/dedup.py` with three deep modules (`dedup_types`, `dedup_query`, `dedup_store`), promote `_normalise_code` to a public `normalise_code` in `parsing/common.py`, delete the monolith + the two verbatim duplicates, and re-point every caller — with zero behaviour change.

**Architecture / approach:** Move-don't-rewrite. The three tiers are already structurally separated inside `dedup.py` (types L96–132, pure-query L53–77+L303–524, store L143–296+L531–779). `dedup_query` must not import `dedup_store` (it operates on an already-loaded inventory dict). Both import types from `dedup_types`. Production is unaffected; the existing `tests/unit/test_dedup_checker.py` + `test_dedup_reads_ledger.py` + the `tests/conftest.py` autouse fixture are the regression spec.

**Tech Stack:** Python 3, `pytest`, PyO3 (`javdb.rust_core` dedup bridge), `OperationsRepo`, `OwnershipLedgerRepo`.

**Verification posture:** No behaviour changes, so the gate is "existing suites stay green with only import lines changed", plus one new import-isolation regression test proving `dedup_types` does not drag in `OperationsRepo`/`rust_core`.

---

## File Structure

| Path | Action | Responsibility |
| --- | --- | --- |
| `javdb/spider/services/dedup_types.py` | **Create** | `RcloneEntry`, `DedupRecord`, `DEDUP_FIELDNAMES`. Zero deps (stdlib + `typing` only). |
| `javdb/spider/services/dedup_query.py` | **Create** | Rust bridge import + `RUST_DEDUP_AVAILABLE` + `should_skip_from_rclone`, `is_in_rclone_inventory`, `check_dedup_upgrade`, `check_redownload_dedup_upgrade`, `_redownload_category_matches_entry`. Imports `normalise_code` from `parsing.common`, types from `dedup_types`, priority aliases from `spider.contracts`. **No** `dedup_store` import. |
| `javdb/spider/services/dedup_store.py` | **Create** | inventory loading (`load_rclone_inventory`, `_legacy_load_rclone_inventory`, `_csv_load_rclone_inventory`, `_ledger_to_inventory`, `_ledger_has_gdrive_rows`, `_open_ledger_for_dedup`, `_split_glyph_category`, `should_skip_from_ownership`) + persistence (`append_dedup_record`, `mark_records_deleted`, `cleanup_deleted_records`, `load_dedup_csv`, `save_dedup_csv`, `export_dedup_db_to_csv`, `_load_pending_paths_cache`, `_ensure_db`, `_raw_csv_read`, `_atomic_csv_write`) + the process-globals `_db_initialised`, `_pending_paths_cache`. Imports types from `dedup_types`, `normalise_code` from `parsing.common`, `OperationsRepo`/ledger repo. |
| `javdb/spider/services/dedup.py` | **Delete** | Replaced by the three modules. |
| `javdb/parsing/common.py` | Modify | Add public `normalise_code(code: str) -> str` (NFKC + strip + upper) + `__all__` entry. |
| `javdb/ops/reconcile/code_resolver.py` | Modify | Delete the local `_normalise_code` (L44–46); `from javdb.parsing.common import normalise_code`; update the 3 call sites (L71, L75). |
| `javdb/ops/reconcile/service.py` | Modify | L285: `normalise_code` from `parsing.common` + `RcloneEntry` from `dedup_types`. L425: `normalise_code` from `parsing.common`. |
| `javdb/pipeline/models.py` | Modify | L8: `DedupRecord` from `dedup_types`. |
| `javdb/pipeline/planner.py` | Modify | L26–30: `DedupRecord` from `dedup_types`; `check_dedup_upgrade`/`check_redownload_dedup_upgrade` from `dedup_query`. |
| `javdb/spider/detail/runner.py` | Modify | L37–41: `DedupRecord`←`dedup_types`; `should_skip_from_rclone`←`dedup_query`; `append_dedup_record`←`dedup_store`. |
| `javdb/spider/app/run_service.py` | Modify | L45–50: `load_rclone_inventory`/`append_dedup_record`←`dedup_store`; `should_skip_from_rclone`/`check_dedup_upgrade`←`dedup_query`. |
| `javdb/integrations/rclone/manager/service.py` | Modify | L463/L828/L945–947 lazy imports re-pointed to `dedup_types` + `dedup_store`. |
| `javdb/migrations/tools/csv_to_sqlite.py` | Modify | L265–277: delete local `_DEDUP_FIELDNAMES`; `from javdb.spider.services.dedup_types import DEDUP_FIELDNAMES`. |
| `tests/conftest.py` | Modify | L31: `import javdb.spider.services.dedup_store as _dedup_store_mod`; L128–129: reset globals on `_dedup_store_mod`. |
| `tests/unit/test_dedup_checker.py`, `test_dedup_reads_ledger.py`, + other dedup test files | Modify | Re-point imports per tier (types/query/store). |
| `tests/unit/test_dedup_import_isolation.py` | **Create** | Regression: importing only `dedup_types` does not load `operations_repo`/`rust_core`/`ops.reconcile`/`storage.db` into `sys.modules`. |
| `CONTEXT.md` | Modify | Add skip-time-dedup module terms (ADR-049 Domain Language); cross-link the ADR-048 cleanup-time entry. |

## Task 0: Baseline & importer enumeration

- [ ] **Step 0.1 — Green baseline:** `pytest tests/unit -k "dedup" -q`.
- [ ] **Step 0.2 — Enumerate every importer of every `dedup.py` symbol:**
  ```bash
  grep -rn "spider.services.dedup\b\|from javdb.spider.services import dedup" javdb apps scripts tests --include="*.py"
  grep -rn "_normalise_code\|DEDUP_FIELDNAMES\|RcloneEntry\|DedupRecord" javdb apps scripts tests --include="*.py"
  ```
  **Verification gate:** the importer set matches ADR-049's enumeration (9 production files + `conftest.py` + the dedup test files + `csv_to_sqlite.py`). If anything else imports `dedup`, STOP and reconcile.

## Task 1: Promote `normalise_code` (do first — others depend on it)

- [ ] **Step 1.1 — Add `normalise_code` to `parsing/common.py`** (move the 3-line NFKC+strip+upper body verbatim) + `__all__`.
- [ ] **Step 1.2 — Delete the `code_resolver.py` local copy**, import the canonical one, update its 3 call sites.

  **Verification gate:** `pytest tests/unit -k "code_resolver or parsing" -q` green; `grep -rn "def _normalise_code" javdb` shows only (temporarily) `dedup.py` until Task 2.

## Task 2: Create the three modules (move, don't rewrite)

- [ ] **Step 2.1 — `dedup_types.py`:** move `RcloneEntry`/`DedupRecord`/`DEDUP_FIELDNAMES` verbatim.
- [ ] **Step 2.2 — `dedup_query.py`:** move the Rust bridge + pure decision functions; import `normalise_code` from `parsing.common`, types from `dedup_types`, priority aliases from `spider.contracts`. Assert no `dedup_store` import.
- [ ] **Step 2.3 — `dedup_store.py`:** move inventory loading + persistence + the two process-globals; import types from `dedup_types`, `normalise_code` from `parsing.common`. `should_skip_from_ownership` lands here (ADR-049 D6); leave a `# TODO(ADR-049): zero prod callers — dead-code candidate` marker.
- [ ] **Step 2.4 — Delete `dedup.py`.**

  **Verification gate:** `python -c "import javdb.spider.services.dedup_types, javdb.spider.services.dedup_query, javdb.spider.services.dedup_store; print('ok')"` — no circular import; `dedup_query` import does not pull `operations_repo`.

## Task 3: Re-point callers & tests

- [ ] **Step 3.1 — Production call sites** (9 files) per the File Structure table.
- [ ] **Step 3.2 — `tests/conftest.py`** autouse fixture (L31, L128–129) → `dedup_store`.
- [ ] **Step 3.3 — `csv_to_sqlite.py`** → import canonical `DEDUP_FIELDNAMES`.
- [ ] **Step 3.4 — dedup test files** re-pointed per tier.

  **Verification gate:** `pytest tests/unit -k "dedup" -q` green with assertions unchanged from Task 0.1.

## Task 4: Import-isolation regression + docs

- [ ] **Step 4.1 — `tests/unit/test_dedup_import_isolation.py`:** in a `sys.modules` snapshot, `import javdb.spider.services.dedup_types` and assert `javdb.storage.repos.operations_repo`, `javdb.rust_core`, `javdb.ops.reconcile`, `javdb.storage.db` are absent.
- [ ] **Step 4.2 — CONTEXT.md:** add the skip-time-dedup module terms; cross-link ADR-048's cleanup-time entry.

  **Verification gate:** the isolation test passes; `grep -n "Skip-time dedup\|dedup_query\|dedup_store" CONTEXT.md` non-empty.

## Task 5: Final gates

- [ ] `pytest tests/unit tests/smoke -q` green.
- [ ] `grep -rn "spider.services.dedup\b" javdb apps scripts tests --include="*.py"` → empty (only the three new submodules remain).
- [ ] `grep -rn "def _normalise_code" javdb` → empty (one canonical `normalise_code` in `parsing/common.py`).
- [ ] `ruff check javdb/spider/services javdb/parsing/common.py javdb/ops/reconcile` clean.
- [ ] `git diff --stat`: `dedup.py` deleted; three modules + isolation test created; two duplicate copies removed.

## Rollback

Pure refactor; revert the PR. No data/schema/D1/Rust change.

## Out of Scope

- Any dedup decision/persistence behaviour change.
- Deleting `should_skip_from_ownership` (flagged for a separate dead-code decision).
- `rclone/helper.py` cleanup-time dedup (ADR-048).
