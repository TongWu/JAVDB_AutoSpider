# Mandatory Session Binding — Phase 2: Repo as the Single Public Interface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the dual public interface to one. The Repo classes become the single public storage surface; the module-level `db_*` write/operations functions stop being re-exported and are migrated off at every caller. After this phase there is one obvious way to write storage.

**Architecture:** Migrate the remaining direct `db_*` call sites (~67 expressions across ~28 non-test files) to Repo methods, add the few missing thin Repo methods, then trim the migrated names from `javdb/storage/db/__init__.py` `__all__`. Stateless primitives stay exported. Migration tools and single-use `align_*` functions are excluded.

**Tech Stack:** Python 3.11+, pytest. Single repo.

**Related:** [ADR-032](ADR-032-mandatory-session-binding.md), [IMP-ADR032-01](IMP-ADR032-01-mandatory-session-id.md) (land first)

**Status:** Proposed

---

## Scope

- **In:** migrate production `db_*` callers to Repos; add missing thin Repo methods; trim `__init__.__all__` + the `from ._db_* import (...)` blocks; boundary tests.
- **Out (D6):** `migrations/tools/*` (one-shot scripts) and `align_*` functions — left on module functions. Stateless primitives (`get_db`, `*_DB_PATH`, `init_db`, `generate_session_id`, `generate_integer_id`, `verify_d1_schema_versions`) stay exported.
- **Behavior:** strictly preserving — each migration is a 1:1 swap `db_foo(x, session_id=s)` → `Repo(...).foo(x, session_id=s)`.

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Modify | `javdb/storage/repos/history_repo.py` | Add missing thin methods (e.g. `resume_finalizing_session` wrapping `db_resume_finalizing_session`) |
| Modify | `javdb/storage/repos/operations_repo.py` | Add any missing thin methods callers need |
| Modify | `javdb/integrations/rclone/manager.py` | ~18 `db_*` calls (`:1226-1271`, `:1506-1668`) → `OperationsRepo` / `HistoryRepo` |
| Modify | `javdb/storage/sessions/commit.py`, `apps/cli/db/commit_session.py`, `apps/cli/db/cleanup_stale_in_progress.py` | `db_commit_session_history` / `db_resume_finalizing_session` → Repo methods |
| Modify | (~24 more files with direct `db_*` calls — enumerate in Task 1) | 1:1 swap to Repo methods |
| Modify | `javdb/storage/db/__init__.py` | Trim migrated `db_*` from `__all__` (`:147-223`) + the `from ._db_* import` blocks (`:62-144`); keep stateless primitives |
| Modify | (boundary tests) | Extend the `_raw_db_forbidden` pattern to each newly-migrated module |

---

## Task 1: Enumerate the migration surface

- [ ] `grep -rn "\bdb_[a-z_]\+(" javdb apps | grep -v "test" | grep -v "_db_.*\.py:" | grep -vE "generate_session_id|generate_integer_id|get_db|init_db|verify_d1_schema_versions"` → the full call-site list (~67 expressions / ~28 files expected).
- [ ] Bucket by Repo: history-writes → `HistoryRepo`; rclone/dedup/pikpak → `OperationsRepo`; session commit/resume → `HistoryRepo`/`SessionsRepo`; stats → `StatsRepo`.
- [ ] Mark the **excluded** sites (D6): `migrations/tools/*`, `align_*` — do not migrate.

## Task 2: Add the missing thin Repo methods

- [ ] `HistoryRepo.resume_finalizing_session(session_id)` → wraps `db_resume_finalizing_session`.
- [ ] Any other 1:1 wrappers callers need so every production `db_*` has a Repo target (except the excluded set).
- [ ] Keep them thin — this phase consolidates the interface, it does not move SQL.

## Task 3: Migrate callers (per-module, behavior-preserving)

- [ ] Migrate module-by-module, starting with the biggest concentration (`rclone/manager.py`, ~18 calls). Each swap: `db_foo(x, session_id=s, db_path=p)` → `OperationsRepo(db_path=p).foo(x, session_id=s)` (match the Repo's actual signature).
- [ ] After each module, add/extend its boundary test (Task 5) so the migration can't silently regress.

## Task 4: Trim the `db_*` exports

- [ ] Once a `db_*` name has no remaining production caller, remove it from `javdb/storage/db/__init__.py` `__all__` (`:147-223`) and from the `from ._db_* import (...)` blocks (`:62-144`).
- [ ] Keep exporting: `get_db`, `*_DB_PATH`, `init_db`, `generate_session_id`, `generate_integer_id`, `verify_d1_schema_versions`.
- [ ] Verify the repo↔db import shim still resolves: `_db_history_write.py` imports `history_repo` directly from the submodule (not via `__init__`), so trimming `__all__` is safe — confirm with an import smoke test.

## Task 5: Tests

- [ ] For each migrated module, add the `_raw_db_forbidden` boundary test (pattern: `tests/unit/test_adr005_pr3a_repo_callers.py:9-13` — monkeypatch the raw `db_*` to raise, assert the caller still works through the Repo).
- [ ] Migrate the `db_*`-targeted contract tests onto Repo methods first (today ~42 test files reference `db_*` directly vs ~12 the Repo): `test_db.py`, `test_commit_session_bulk.py`, `test_pending_torrent_overlay_merge.py`, `test_d1_dual.py` etc. — rewrite against Repo methods so the function family can be de-exported without losing coverage.
- [ ] **Regression:** `pytest tests/unit/test_operations_endpoints.py tests/unit/test_rclone_manager.py tests/unit/test_commit_session_bulk.py tests/unit/test_adr005_pr3a_repo_callers.py -q`.

## Task 6: Verification gates

- [ ] Full unit suite green.
- [ ] **Import-boundary proof:** `python -c "from javdb.storage.db import db_stage_history_write"` now fails (the name is de-exported), while `from javdb.storage.repos.history_repo import HistoryRepo` works.
- [ ] **Grep proof:** no production (non-test, non-migration-tool) file imports a migrated `db_*` name.
- [ ] Update this IMP's `Status` to `Completed`; check off `IMP-ADR032-02`. If both ADR-032 IMPs are done, mark the ADR Completed and archive the folder per the docs convention.

## Risks

- **Large diff (~28 files)** — migrate per-module with its boundary test to keep each step verifiable.
- **De-exporting too eagerly** — only remove a `db_*` from `__all__` after its last production caller is migrated; the `_db_*` submodules keep the implementation.
- **Test coverage moves, not vanishes** — the `db_*`-targeted tests must be rewritten onto Repos before the names are removed, or coverage drops.

## Out of scope

- `migrations/tools/*` + `align_*` (D6, stay on module functions).
- Deleting `set/get_active_session_id` (Phase 3, deferred).
