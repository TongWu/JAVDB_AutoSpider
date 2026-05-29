# Mandatory Session Binding — Phase 1: Remove the Global Fallback

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `session_id` truly mandatory on the write functions that still fall back to the process global, completing ADR-005 amendment-2's "eliminate the `db_session._active` global" claim. After this phase, a write without an explicit `session_id` raises instead of silently producing an untagged (unrollbackable) row.

**Architecture:** Remove the `_SESSION_ID_SENTINEL` default + `_resolve_session_id(...)` fallback from the ~10 operations functions and the 2 history-batch functions. Audit every caller; thread `session_id` explicitly where it was relying on the global. Per-method binding (amendment-2's shape) is preserved — no constructor binding.

**Tech Stack:** Python 3.11+, pytest. Single repo. No schema change.

**Related:** [ADR-032](ADR-032-mandatory-session-binding.md)

**Status:** Proposed

---

## Scope

- **In:** drop the sentinel default from `_db_operations.py` (~10 functions) + `_db_history_write.py` (`db_batch_update_last_visited`, `db_batch_update_movie_actors`); thread `session_id` at every caller; tests pinning "no silent global".
- **Out:** trimming the `db_*` public interface (IMP-ADR032-02); deleting `set/get_active_session_id` (Phase 3, deferred); constructor binding (rejected).
- **Behavior change (intended):** omitting `session_id` now raises. Behavior-preserving for callers that already pass it.

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Modify | `javdb/storage/db/_db_operations.py` | Make `session_id` required on the ~10 functions (drop sentinel default + `_resolve_session_id`) |
| Modify | `javdb/storage/db/_db_history_write.py` | Same for `db_batch_update_last_visited` (`:570-575`), `db_batch_update_movie_actors` (`:652-657`) |
| Modify | `javdb/integrations/pikpak/bridge.py` | Thread `session_id` into `db_append_pikpak_history` (prime behavior-change suspect, `~:432`) |
| Modify | (callers found in Task 1 that relied on the global) | Pass `session_id` explicitly |
| Create | `tests/unit/test_mandatory_session_id.py` | Assert each affected `db_*` raises when `session_id` is omitted |

---

## Task 1: Audit every caller of the affected functions

> Do this BEFORE changing signatures — a missed caller becomes a runtime `TypeError`.

- [ ] List call sites: `grep -rn "db_append_pikpak_history\|db_save_dedup_records\|db_swap_rclone_inventory\|db_batch_update_last_visited\|db_batch_update_movie_actors\|db_save_.*_stats" javdb apps | grep -v _db_operations.py | grep -v _db_history_write.py`.
- [ ] Classify each: **already passes `session_id`** (e.g. `rclone/manager.py:1255,1260`, `dedup.py` via `OperationsRepo`, `history_manager.py:177`) vs **relies on the global**.
- [ ] Confirmed suspect: `pikpak/bridge.py` `db_append_pikpak_history(record)` passes no `session_id` today → must thread the active session id from the bridge's entry point.
- [ ] Low-risk: `align_inventory_with_moviehistory.py` align calls don't take `session_id` at all (they target different functions) — verify they are not in the affected set.

## Task 2: Make `session_id` required

- [ ] `_db_operations.py`: for each of the ~10 functions with `session_id: Any = _SESSION_ID_SENTINEL`, remove the default (make it a required keyword) and delete the `session_id = _resolve_session_id(session_id)` line. Functions at signatures `:95,111,250,322,355,420,465,484,516,577`; resolve-calls at `:104,115,257,326,368,427,474,490,521,580`.
- [ ] `_db_history_write.py`: `db_batch_update_last_visited` (`:554-557,570-575`) and `db_batch_update_movie_actors` (`:633-636,652-657`) — drop the `if session_id is None: session_id = _SESSION_ID_SENTINEL` shim and the `_resolve_session_id` call; require `session_id`.
- [ ] Thread `session_id` at every caller found in Task 1 that relied on the global (notably `pikpak/bridge.py`).
- [ ] Leave `_resolve_session_id` / `_SESSION_ID_SENTINEL` themselves in place for now (Phase 3 removes them once no reader remains).

## Task 3: Tests

- [ ] Create `tests/unit/test_mandatory_session_id.py`: a parametric test asserting each affected `db_*` function raises `TypeError` (missing required kwarg) when `session_id` is omitted — pins the "no silent global" invariant.
- [ ] **Regression — must stay green:** `pytest tests/unit/test_rclone_manager.py tests/unit/test_operations_repo.py tests/unit/test_pikpak_bridge.py tests/unit/test_rollback_pending_mode.py -q`. Note `test_rclone_manager.py:209` sets `set_active_session_id(None)` then asserts behavior — confirm it still passes (it should, because the manager passes `session_id` explicitly).

## Task 4: Verification gates

- [ ] `pytest tests/unit/test_mandatory_session_id.py -v` — green.
- [ ] Full storage + integrations suite green.
- [ ] **Grep proof:** `grep -rn "_SESSION_ID_SENTINEL\|_resolve_session_id" javdb/storage/db/_db_operations.py javdb/storage/db/_db_history_write.py` returns nothing (the fallback is gone from the write functions).
- [ ] Smoke a real pipeline path (or the spider smoke test) to confirm no caller hits the new raise in practice.
- [ ] Update this IMP's `Status` to `Completed`; check off `IMP-ADR032-01` in the ADR roadmap.

## Risks

- **A missed caller relying on the global** → runtime `TypeError`. That's the desired failure mode, but find them in Task 1 first. `pikpak/bridge.py` is the prime suspect.
- **Tests that set `set_active_session_id(...)` as setup** may need updating if they relied on the fallback rather than passing `session_id`.
