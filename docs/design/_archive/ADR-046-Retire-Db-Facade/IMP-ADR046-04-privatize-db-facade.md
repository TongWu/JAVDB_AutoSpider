# IMP-ADR046-04: ADR-046 Phase 4 — Privatize the `db_*` Facade Implementation Plan

> **Status: ✅ Implemented 2026-06-10** (branch `claude/pedantic-hamilton-9d8be3` — facade re-exports dropped; repos are the sole public read/write seam; 0 facade regressions in the full suite).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-046](ADR-046-retire-db-facade.md) — **Phase 4 (the finale)**. Depends on Phases 2, 3, and 5 having reduced the live caller set. The boundary is fixed by the grilling decision: **privatize only the read/write *facade* functions that have a repo equivalent; KEEP public the infrastructure** (`get_db`, `get_local_sqlite_db`, `close_db`, `current_backend`, `init_db`, `SCHEMA_VERSION`, the `*_DB_PATH` constants, `generate_session_id`/`generate_integer_id`, the session-state setters/getters, migration helpers).

> **⚠ Directional plan.** Phase 4's blast radius is dominated by the test surface (≈37 files / ≈594 refs today) and a handful of production callers. Phases 2/3/5 will shrink and reshape this set before Phase 4 runs. **Re-run the Task 0 inventory greps at execution time** and reconcile against this plan; treat the file lists here as the 2026-06-03 snapshot, not gospel.

**Goal:** Remove the `db_*` functions from the public `javdb.storage.db` package API (`__init__.py` re-exports + `__all__`) so the **repository classes are the only public way** to read/write storage. The functions remain in their `_db_*` private modules (already underscore-named modules); only the public re-export is retired, and the remaining callers move to repos (or, where a repo wrapper is genuinely unavailable, to an explicit `from javdb.storage.db._db_<x> import …` private-module import).

**Tech Stack:** Python 3 + `pytest`. Test command: `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest <files> -q`. Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## Task 0: Re-inventory (MANDATORY at execution time) + add the 7 missing repo wrappers

The facade is **53** `db_*` names in `__all__` (+ 3 imported-but-not-exported: `db_count_in_progress_sessions_for_run`, `db_begin_finalize_session`, `db_finish_commit_session`). **46 already have a repo wrapper; 7 do NOT** and must get one before they can be privatized:

| Facade fn (no wrapper yet) | Add to | As |
| --- | --- | --- |
| `db_get_session_status` | `SessionsRepo` (read) | `get_session_status(session_id)` |
| `db_get_report_rows` | `SessionsRepo` | `get_report_rows(...)` |
| `db_get_latest_session` | `SessionsRepo` | `get_latest_session()` |
| `db_get_sessions_by_date` | `SessionsRepo` | `get_sessions_by_date(date)` |
| `db_count_in_progress_sessions_for_run` | `SessionLifecycleRepo` | `count_in_progress_sessions_for_run(run_id, run_attempt)` |
| `db_begin_finalize_session` | `SessionLifecycleRepo` | `begin_finalize_session(session_id)` |
| `db_finish_commit_session` | `SessionLifecycleRepo` | `finish_commit_session(session_id)` |

- [ ] **Step 0.1 — Re-inventory.** Re-run, and reconcile against the table above:
```bash
# public facade names still re-exported:
grep -nE "^\s*db_[a-z_]+," javdb/storage/db/__init__.py
# external production callers (outside javdb/storage/db/ and the repos):
grep -rn "from javdb.storage.db import .*db_\|from javdb.storage.db._db_.* import db_" javdb apps --include='*.py' | grep -vE "javdb/storage/(db|repos|sessions|rollback)/"
# test refs:
grep -rln "db_" tests --include='*.py' | xargs grep -lE "\bdb_(stage|commit|rollback|load|batch|create_report|mark_session|get_session|insert_report|find_|append_|save_|replace_|merge_|swap_|open_rclone|drop_rclone|upsert_align|pending_session)" | sort
```
- [ ] **Step 0.2 — Add each missing wrapper (TDD).** For each of the 7, write a test that the repo method returns the same as the underlying `db_*` against a seeded DB, then implement the thin delegate (mirror the existing wrappers, e.g. `SessionLifecycleRepo.find_in_progress_sessions` at `session_lifecycle_repo.py:139`). Confirm `SessionsRepo` (`javdb/storage/repos/sessions_repo.py`) is the right home for the read wrappers (grep it first — some may already exist under a different name).
- [ ] **Step 0.3 — Commit** the 7 wrappers: `feat(storage): add repo wrappers for the last 7 unwrapped session facade fns (ADR-046 P4)`.

---

## Task 1: Reroute the production external callers (small)

Outside the storage layer, only ~3 modules call the facade live (re-confirm via Step 0.1):

- [ ] **Step 1.1 — `javdb/migrations/tools/align_inventory_with_moviehistory.py`** (the big one — 6 functions): imports (`:73-78`) `db_load_history`, `db_delete_align_no_exact_match`, `db_load_align_no_exact_match_codes`, `db_load_rclone_inventory`, `db_upsert_align_no_exact_match`; calls at `:246, :618-624, :807, :944`. Replace each with the repo equivalent: `HistoryRepo().load_history()`, `OperationsRepo().load_rclone_inventory()`, `OperationsRepo().load_align_no_exact_match_codes()`, `OperationsRepo(session_id=<sid>).upsert_align_no_exact_match(...)`, `OperationsRepo(session_id=<sid>).delete_align_no_exact_match(...)`. (This tool sets the global via `set_active_session_id(args.session_id)` at `:1177` — after Phase 5 that's gone; here, pass `args.session_id` explicitly into the repo constructor.) Run `pytest tests/integration/test_align_inventory_with_moviehistory.py -q`.
- [ ] **Step 1.2 — `javdb/spider/app/run_service.py:590,608`** (`db_get_session_status`): route to the new `SessionsRepo().get_session_status(...)` (Task 0). Smoke-import run_service.
- [ ] **Step 1.3 — `apps/cli/ops/profile_hot_paths.py:289,295,370`** (`db_load_history` from `_db_history_read`): either route to `HistoryRepo().load_history()` or keep the explicit private-module import (it is a benchmark harness, not production) — pick the private-module import to avoid perturbing the benchmark subject; just update the import path away from the package facade.
- [ ] **Step 1.4 — Verify + commit.** `grep` confirms no `from javdb.storage.db import db_*` in production outside the storage layer; commit `refactor: route remaining production db_* callers through repos (ADR-046 P4)`.

---

## Task 2: Migrate the test surface (the bulk — strategy, not per-diff)

≈37 test files reference the facade (≈594 refs; `test_db.py` alone ≈113 / 24 functions). Two migration modes per file:

- **Behavior tests** (assert what a write/read *does*) → migrate to constructing the **repo** and asserting through it (the ADR-046 contract). Examples: `test_operations_repo.py`, `test_stats_repo.py`, `test_history_repo.py`, `test_adr032_phase2_repo_methods.py`, `test_rollback*.py`, `test_commit_session_bulk.py`.
- **Facade-function unit tests** (assert the function itself, e.g. `test_db.py`) → change the import from the package facade (`from javdb.storage.db import db_x`) to the **private module** (`from javdb.storage.db._db_<area> import db_x`). The function still exists; only the public re-export is going away. This keeps the low-level coverage without forcing a rewrite into repo assertions where that would lose granularity.

- [ ] **Step 2.1 — Triage each test file** from Step 0.1's list into {behavior → repo} vs {facade-fn → private import}. Record the mapping (a checklist of 37 files).
- [ ] **Step 2.2 — Migrate group-by-group**, committing per logical group (e.g. one commit for the operations tests, one for rollback, one for `test_db.py`), running that group green after each:
  - `test_db.py` (≈113 refs, 24 fns) → private-module imports (it is the low-level facade-fn suite). Largest single file; do it as its own commit.
  - rollback group (`test_rollback.py`, `test_rollback_pending_mode.py`, `test_rollback_full_fidelity.py`) → repo (`SessionLifecycleRepo`) where they assert behavior; private import where they assert the fn.
  - operations/stats group (`test_operations_repo.py`, `test_db_stats.py`, `test_stats_repo.py`, `test_rclone_manager.py`) → repo.
  - sessions/report group (`test_session_helpers.py`, `test_adr005_pr3a_repo_callers.py`, `test_mandatory_session_id.py`, `test_cleanup_stale_in_progress.py`) → repo / new Task-0 wrappers.
  - history group (`test_history_manager.py`, `test_history_repo.py`, `test_batch_c_movie_history_id.py`, `test_pending_torrent_overlay_merge.py`, `test_actor_link_absolutize_on_commit.py`) → repo / private import.
  - the long tail (≈15 smaller files) → triage + migrate.
- [ ] **Step 2.3 — Gate after each group:** `pytest <that group> -q` green before the next. Full `pytest tests/unit tests/integration -q` green after the last group (modulo known pre-existing failures).

---

## Task 3: Drop the public re-exports (the actual privatization)

**Only after Tasks 0-2 leave zero `from javdb.storage.db import db_*` callers.**

- [ ] **Step 3.1 — Remove the `db_*` names from `javdb/storage/db/__init__.py`.** Delete the `db_*` entries from the per-module import blocks (history-read `:62-67`, history-write `:70-79`, reports `:82-101`, operations `:104-125`, stats `:128-138`, rollback `:141-144`) AND from `__all__` (`:147-223`). **KEEP** every non-`db_*` infra name (connection, init/migrations, session-state, paths). Keep the underscored internals that other modules import (`_execute_backend_batch`, `_row_to_jsonable_dict`, `_compute_indicators`, `_pending_torrent_overlay`, `_commit_one_movie`, the DDL blobs) — verify each is still imported somewhere before removing; if only the repos use them, leave them (they import from the `_db_*` modules directly).
- [ ] **Step 3.2 — (Optional) `_`-prefix the functions.** ADR-046 D1 mentions `_`-prefixing the `db_*` functions. The functions already live in `_db_*.py` *modules* (private by module), so dropping the re-export already retires the public facade. The intra-name `db_*`→`_db_*` rename touches every repo wrapper + every `_db_*` cross-import and is **cosmetic** once the re-export is gone — **defer it** unless a reviewer insists (YAGNI; record the decision). If done, it is a mechanical rename pinned by the green test suite.
- [ ] **Step 3.3 — Verify the facade is gone.**
```bash
python -c "import javdb.storage.db as d; assert not [n for n in d.__all__ if n.startswith('db_')], 'db_* still public'; print('facade retired')"
grep -rn "from javdb.storage.db import" javdb apps tests --include='*.py' | grep -E "\bdb_[a-z]" && echo "STILL HAS PACKAGE-FACADE db_* IMPORTS" || echo "✅ none"
```
- [ ] **Step 3.4 — Commit.** `refactor(storage): retire the public db_* facade from javdb.storage.db (ADR-046 P4)`.

---

## Task 4: Final verification + close ADR-046

- [ ] **Step 4.1 — Full suites green** (`pytest tests/unit tests/integration tests/smoke -q`, modulo known pre-existing failures); `ruff check javdb apps` clean on touched files.
- [ ] **Step 4.2 — Importer integrity:** `python -c "import javdb.storage.db, javdb.storage.repos.history_repo, javdb.storage.repos.operations_repo, ..."` for every touched module.
- [ ] **Step 4.3 — Mark ADR-046 Completed** (both `.md`/`.zh.md`) once Phases 2/3/4 (+5) are done, and archive the folder per the repo's archival convention. (If Phase 5 is still pending, mark Phase 4 done but leave the ADR open.)

## Out of Scope

- Deleting the global session machinery — Phase 5.
- The `_`-prefix rename — deferred (Step 3.2), cosmetic once the re-export is gone.
- Infra functions (`get_db`/`init_db`/connection/migrations) — stay public (grilling decision).

## Self-Review

- Boundary matches the grilling decision: facade-with-repo-equivalent privatized; infra kept public.
- Ordering: add missing wrappers (Task 0) → reroute prod (Task 1) → migrate tests (Task 2) → drop re-exports (Task 3). The privatization (Task 3) cannot pass until Tasks 0-2 remove all package-facade `db_*` importers.
- Directional: the test/caller lists are a 2026-06-03 snapshot; Task 0.1 re-inventory is mandatory because Phases 2/3/5 reshape the set first.
