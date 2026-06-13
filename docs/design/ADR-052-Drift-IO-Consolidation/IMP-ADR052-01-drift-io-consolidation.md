# IMP-ADR052-01: Consolidate Drift Helpers + Unify the Drift-Log Writer into `drift_io` — Implementation Plan

> **Status: 🔲 Proposed (2026-06-13).** Single PR; behaviour-preserving relocation (one added WARNING; path resolution moves import-time → call-time). Authored from [ADR-052](ADR-052-drift-io-consolidation.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Related:** [ADR-052](ADR-052-drift-io-consolidation.md) — Phase 1 (the only phase). Coordinate with [ADR-050](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md) on the `append_jsonl_record` import path.

**Goal:** Extract a stdlib-only `javdb/storage/drift_io.py` holding the consolidated drift-cell helpers, JSONL reader, and the **single** call-time-bound thread-safe `append_jsonl_record` + a `drift_log_path` resolver; retire `dual_connection._DRIFT_LOG_PATH`/`_DRIFT_LOG_LOCK`/`_append_drift_record`; re-point every caller and the 3 monkeypatch tests to the unified `$REPORTS_DIR` seam.

**Architecture / approach:** Move-don't-rewrite for the helpers (verified value-identical). The two writers merge into one on the lifecycle (call-time) model + the drift (locked + guard) safety. `_DRIFT_LOG_PATH`'s 6 log-message references become `drift_log_path()` calls. `drift_io` must import nothing from `javdb` (stdlib-only) so `migrations/tools` can use it.

**Tech Stack:** Python 3, `pytest`, `threading.Lock`, JSONL, `logging`.

**Verification posture:** Behaviour-preserving — existing drift/dual/rollback/diagnose suites stay green with import lines changed; the 3 `_DRIFT_LOG_PATH` tests switch to `$REPORTS_DIR` isolation; one new test pins the unified writer's lock + guard + call-time resolution.

---

## File Structure

| Path | Action | Responsibility |
| --- | --- | --- |
| `javdb/storage/drift_io.py` | **Create** | stdlib-only. `_values_equal` (reconcile's docstring), `_row_to_dict`, `read_jsonl(path)` (warn on malformed), `drift_log_path(reports_dir=None) -> str`, `append_jsonl_record(record, *, reports_dir=None, filename="d1_drift.jsonl")` (call-time, `_DRIFT_LOG_LOCK`, single pytest guard). |
| `javdb/storage/dual_connection.py` | Modify | Delete `_DRIFT_LOG_PATH`, `_DRIFT_LOG_LOCK`, `_append_drift_record`. The 3 write sites (L446, L485, L1207) → `drift_io.append_jsonl_record(record)`. The 6 log-message refs (L451, L498, L750, L930, L1213, L1219) → `drift_io.drift_log_path()`. |
| `javdb/storage/sessions/lifecycle_helpers.py` | Modify | Remove `append_jsonl_record` (moved to `drift_io`); keep `write_github_output`/`attach_run_identity`. |
| `javdb/storage/drift_diagnose.py` | Modify | Delete local `_values_equal`/`_read_jsonl`/`_row_to_dict`; import from `drift_io`. Re-point `append_jsonl_record` import (L40) to `drift_io`. |
| `javdb/migrations/tools/reconcile_d1_drift.py` | Modify | Delete local `_values_equal`/`_row_to_dict`/`_read_drift_log`; import `_values_equal`/`_row_to_dict`/`read_jsonl` from `drift_io`. |
| `apps/cli/db/pending_health.py` | Modify | Delete local `_read_jsonl`; import `read_jsonl` from `drift_io`. |
| `javdb/storage/sessions/commit.py`, `javdb/storage/rollback/core.py`, `apps/cli/db/commit_session.py` | Modify | Re-point `append_jsonl_record` import to `drift_io`. **Coordinate with ADR-050** (same files). |
| `tests/unit/test_d1_dual.py`, `tests/unit/test_batch_c_movie_history_id.py`, `tests/unit/test_system_state_repo.py` | Modify | Replace `monkeypatch.setattr(dual_connection, "_DRIFT_LOG_PATH", …)` with `monkeypatch.setenv("REPORTS_DIR", str(tmp_path))` (unified seam). |
| `tests/unit/test_drift_diagnose.py`, `tests/unit/test_session_helpers.py` | Modify | Re-point `append_jsonl_record`/helper monkeypatch targets to `drift_io`. |
| `tests/unit/test_drift_io.py` | **Create** | Pin the unified writer: call-time `reports_dir`/`$REPORTS_DIR` resolution; the lock serializes concurrent appends; the pytest guard refuses the tracked default; `read_jsonl` warns on a malformed line; `_values_equal`/`_row_to_dict` parity cases. |
| `CONTEXT.md` | Modify | Add **Drift IO** + **Drift-log writer** (ADR-052 Domain Language). |

## Task 0: Baseline & seam enumeration

- [ ] **Step 0.1 — Green baseline:** `pytest tests/unit/test_d1_dual.py tests/unit/test_drift_diagnose.py tests/unit/test_session_helpers.py tests/unit/test_batch_c_movie_history_id.py tests/unit/test_system_state_repo.py -q`.
- [ ] **Step 0.2 — Enumerate every helper/writer/path reference:**
  ```bash
  grep -rn "_values_equal\|_row_to_dict\|_read_jsonl\|_read_drift_log\|append_jsonl_record\|_append_drift_record\|_DRIFT_LOG_PATH\|_DRIFT_LOG_LOCK" javdb apps tests --include="*.py"
  ```
  **Verification gate:** the reference set matches ADR-052 (4 helper copies, 2 writers, 3 writer-call sites, 6 log refs, 3 `_DRIFT_LOG_PATH` monkeypatch test files). If anything else references them, STOP and reconcile.

## Task 1: Create `drift_io.py` (stdlib-only)

- [ ] **Step 1.1 — Helpers:** `_values_equal` (reconcile's precision docstring), `_row_to_dict` (verbatim), `read_jsonl(path)` warning on malformed (`json.JSONDecodeError` → `logger.warning`, then continue).
- [ ] **Step 1.2 — `drift_log_path(reports_dir=None) -> str`:** resolve `reports_dir` → `$REPORTS_DIR` → `"reports"`, return `<base>/D1/<filename>`.
- [ ] **Step 1.3 — `append_jsonl_record(record, *, reports_dir=None, filename="d1_drift.jsonl")`:** resolve via `drift_log_path`; the pytest guard refuses when the resolved path is the tracked `reports/D1/...` under `PYTEST_CURRENT_TEST`; write under a module-level `threading.Lock`; never raise (log at WARNING on failure). Assert **no `javdb` imports** in this module.

  **Verification gate:** `python -c "import javdb.storage.drift_io; print('ok')"`; `grep -n "import javdb\|from javdb" javdb/storage/drift_io.py` → empty (stdlib-only).

## Task 2: Retire the import-time writer in `dual_connection`

- [ ] **Step 2.1 — Delete** `_DRIFT_LOG_PATH`, `_DRIFT_LOG_LOCK`, `_append_drift_record`.
- [ ] **Step 2.2 — Write sites** (L446, L485, L1207) → `drift_io.append_jsonl_record(record)`.
- [ ] **Step 2.3 — Log-message refs** (L451, L498, L750, L930, L1213, L1219) → `drift_io.drift_log_path()`.

  **Verification gate:** `grep -n "_DRIFT_LOG_PATH\|_append_drift_record" javdb/storage/dual_connection.py` → empty; `python -c "import javdb.storage.dual_connection"` ok.

## Task 3: Re-point helper callers + the writer callers

- [ ] **Step 3.1 — `drift_diagnose.py`:** delete local helpers; import from `drift_io`; re-point `append_jsonl_record` (L40).
- [ ] **Step 3.2 — `reconcile_d1_drift.py`:** delete local `_values_equal`/`_row_to_dict`/`_read_drift_log`; import from `drift_io` (use `read_jsonl` for `_read_drift_log`'s call sites).
- [ ] **Step 3.3 — `pending_health.py`:** delete local `_read_jsonl`; import `read_jsonl`.
- [ ] **Step 3.4 — `lifecycle_helpers.py`:** remove `append_jsonl_record`. **Step 3.5 — `sessions/commit.py`, `rollback/core.py`, `apps/cli/db/commit_session.py`:** re-point the `append_jsonl_record` import to `drift_io` (coordinate with ADR-050).

  **Verification gate:** `grep -rn "def _values_equal\|def _row_to_dict\|def _read_jsonl\|def _read_drift_log\|def append_jsonl_record" javdb apps` → only `drift_io.py`.

## Task 4: Tests (unified `$REPORTS_DIR` seam) + new coverage + docs

- [ ] **Step 4.1 — The 3 `_DRIFT_LOG_PATH` tests:** replace the monkeypatch with `monkeypatch.setenv("REPORTS_DIR", str(tmp_path))`; assert the drift line lands under `tmp_path/D1/d1_drift.jsonl`.
- [ ] **Step 4.2 — `test_drift_diagnose.py` / `test_session_helpers.py`:** re-point `append_jsonl_record`/helper targets to `drift_io`.
- [ ] **Step 4.3 — `test_drift_io.py`:** new coverage per the File Structure row.
- [ ] **Step 4.4 — CONTEXT.md:** add the two terms.

  **Verification gate:** `pytest tests/unit/test_drift_io.py -q` green; the 3 migrated tests green via `$REPORTS_DIR`.

## Task 5: Final gates

- [ ] `pytest tests/unit -k "drift or dual or rollback or commit or pending or system_state or batch_c or session_helpers" -q` green.
- [ ] `grep -rn "_DRIFT_LOG_PATH\|_append_drift_record" javdb apps tests` → empty.
- [ ] `grep -n "import javdb\|from javdb" javdb/storage/drift_io.py` → empty (stdlib-only).
- [ ] `ruff check javdb/storage/drift_io.py javdb/storage/dual_connection.py javdb/storage/drift_diagnose.py javdb/migrations/tools/reconcile_d1_drift.py` clean.
- [ ] `git diff --stat`: `drift_io.py` + `test_drift_io.py` created; 4 helper copies + 2 writers collapsed; 3 tests on one seam.

## Rollback

Pure refactor; revert the PR. No data/schema/D1 change; the drift-log format and write class (diagnostic, ADR-042) are unchanged.

## Out of Scope

- Drift classification / diagnosis / reconciliation logic (ADR-009 / ADR-047).
- The pending-verify *builder* (ADR-050) — this owns the writer underneath it.
- Changing the drift-log file, format, or location.
