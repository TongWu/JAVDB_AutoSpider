# IMP-ADR046-03: ADR-046 Phase 3 — Route commit/rollback orchestration through repos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-046](ADR-046-retire-db-facade.md) — **Phase 3**. The repo methods this routes to ALL already exist (no new repo methods needed): `HistoryRepo.commit_session` / `resume_finalizing_session` / `pending_session_stats`, `SessionLifecycleRepo.rollback_session`.

**Goal:** Make the session-commit and rollback orchestrators call the **repo**, not the private `db_*` functions directly — so the repo is the single write path (toward the ADR-046 end-state). Behavior-preserving (the repo methods delegate to the same underlying functions).

**Architecture:** Four call sites in two orchestrators. `commit.py` calls the `HistoryRepo` methods directly. `rollback/core.py` uses a `_self`-module-alias monkeypatch contract (tests do `monkeypatch.setattr(core, "db_rollback_session", ...)`), so its two `db_*` names are replaced by **module-level functions that delegate to the repo** — this preserves both the `_self.` call sites AND the test monkeypatch surface while routing through the repo.

**Tech Stack:** Python 3 + `pytest`. Test command: `PYTHONPATH=javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest <files> -q`. Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

| Path | Modify | Responsibility |
| --- | --- | --- |
| `javdb/storage/sessions/commit.py` | Modify | `db_commit_session_history` → `HistoryRepo.commit_session`; `db_pending_session_stats` → `HistoryRepo.pending_session_stats` |
| `javdb/storage/rollback/core.py` | Modify | Replace the two module-level `db_*` imports with repo-delegating module-level functions (preserve the `_self` seam) |
| `tests/unit/test_adr046_p3_orchestration_routing.py` | **Create** | Assert the orchestrators construct the repo (not bare `db_*`) |

---

## Task 1: `commit.py` — route through `HistoryRepo`

**File:** `javdb/storage/sessions/commit.py`.

- [ ] **Step 1.1 — Failing test.** Create `tests/unit/test_adr046_p3_orchestration_routing.py`:
```python
"""ADR-046 Phase 3: commit/rollback orchestration routes through repos."""
import inspect
import javdb.storage.sessions.commit as commit_mod
import javdb.storage.rollback.core as rollback_core


def test_commit_module_routes_through_history_repo():
    src = inspect.getsource(commit_mod)
    assert "HistoryRepo(" in src, "commit must construct HistoryRepo"
    # the drain call goes through the repo, not the bare facade fn
    assert "HistoryRepo().commit_session(" in src or "repo.commit_session(" in src


def test_rollback_core_routes_through_session_lifecycle_repo():
    src = inspect.getsource(rollback_core)
    assert "SessionLifecycleRepo" in src, "rollback must route via SessionLifecycleRepo"
```
Run → FAIL.

- [ ] **Step 1.2 — Reroute the commit drain.** In `commit.py`, replace the local import (~line 210) `from javdb.storage.db._db_history_write import db_commit_session_history` and the call (~line 280) `drain = db_commit_session_history(req.session_id)` with:
```python
        from javdb.storage.repos.history_repo import HistoryRepo
        ...
                drain = HistoryRepo().commit_session(req.session_id)
```
(`HistoryRepo.commit_session(session_id, **kwargs)` forwards to the same `db_commit_session_history`, so behavior is identical; a test that patched `_db_history_write.db_commit_session_history` still bites because the repo calls it.)

- [ ] **Step 1.3 — Reroute the pending-stats metric.** In `_emit_commit_metrics` (~line 154-157), replace `from javdb.storage.db._db_reports import db_pending_session_stats` + `db_pending_session_stats(...)` with `from javdb.storage.repos.history_repo import HistoryRepo` + `HistoryRepo().pending_session_stats(...)`. (Same underlying query; `pending_session_stats` queries the history DB.)

- [ ] **Step 1.4 — Run.** `pytest tests/unit/test_commit_session_bulk.py tests/unit/test_adr046_p3_orchestration_routing.py -q` → PASS (commit behavior unchanged; the routing assertion now holds). Fix any commit test that asserted a bare-facade call shape.

- [ ] **Step 1.5 — Commit.** `git add javdb/storage/sessions/commit.py tests/unit/test_adr046_p3_orchestration_routing.py` + commit `refactor(storage): route session commit through HistoryRepo (ADR-046 P3)`.

---

## Task 2: `rollback/core.py` — repo-delegating module seams (preserve `_self` monkeypatch)

**File:** `javdb/storage/rollback/core.py`. The module resolves helpers via `import javdb.storage.rollback.core as _self` and calls `_self.db_rollback_session(...)`; tests monkeypatch `core.db_rollback_session`. So we keep those module-level NAMES but make them delegate to the repo.

- [ ] **Step 2.1 — Replace the rollback import with a repo-delegating function.** Replace (line 44) `from javdb.storage.db._db_rollback import db_rollback_session` with:
```python
def db_rollback_session(session_id, **kwargs):
    """ADR-046 P3: module-level seam that routes rollback through the repo.

    Kept as a module-level name so the ``_self.db_rollback_session`` call site
    (and the existing test monkeypatch surface) resolve unchanged, while the
    actual work now goes through ``SessionLifecycleRepo``.
    """
    from javdb.storage.repos.session_lifecycle_repo import SessionLifecycleRepo
    return SessionLifecycleRepo().rollback_session(session_id, **kwargs)
```
The call site (~line 362, `counts = _self.db_rollback_session(sid, dry_run=..., scope=..., force=..., run_started_at=..., failure_reason=..., auto_resume_finalizing=...)`) is UNCHANGED — those kwargs match `SessionLifecycleRepo.rollback_session`'s signature. Tests that `monkeypatch.setattr(core, "db_rollback_session", ...)` keep working.

- [ ] **Step 2.2 — Replace the pending-stats import with a repo-delegating function.** Replace (line 43) `from javdb.storage.db._db_reports import db_pending_session_stats` with:
```python
def db_pending_session_stats(session_id, **kwargs):
    """ADR-046 P3: route pending-stats through the repo (module-level seam)."""
    from javdb.storage.repos.history_repo import HistoryRepo
    return HistoryRepo().pending_session_stats(session_id, **kwargs)
```
The call site (~line 193) is unchanged.

- [ ] **Step 2.3 — Run the rollback suites.** `pytest tests/unit/test_rollback.py tests/unit/test_rollback_pending_mode.py tests/unit/test_rollback_full_fidelity.py tests/unit/test_adr046_p3_orchestration_routing.py -q` → PASS. The `_self` monkeypatch tests still pass (the names exist); behavior is unchanged (the seams delegate to the repo which delegates to the same underlying functions).

- [ ] **Step 2.4 — Commit.** `git add javdb/storage/rollback/core.py` + commit `refactor(storage): route rollback through SessionLifecycleRepo (ADR-046 P3)`.

---

## Task 3: Verification

- [ ] **Step 3.1 — No bare facade calls remain in the orchestrators.**
```bash
grep -n "db_commit_session_history\|db_rollback_session\|db_pending_session_stats" javdb/storage/sessions/commit.py javdb/storage/rollback/core.py
```
Expected: in `commit.py` — none (replaced by `HistoryRepo`); in `core.py` — only the two NEW module-level seam *definitions* (which internally call the repo), not raw `_db_*` imports.
- [ ] **Step 3.2 — Full unit suite.** `pytest tests/unit -q` green (modulo the known pre-existing failures). Pay attention to `test_rollback*` and `test_commit*` — they exercise the `_self` monkeypatch contract.

## Out of Scope

- Privatizing `db_*` (Phase 4) — Phase 3 only changes the *orchestrators'* call path; the `db_*` functions stay public until Phase 4.
- Deleting the global (Phase 5).

## Self-Review

- All four reroute targets already exist as repo methods (confirmed in the ADR-047/ADR-046 surface extraction); Phase 3 adds NO new repo methods.
- The `rollback/core.py` `_self`-monkeypatch contract is preserved by keeping module-level names that delegate to the repo — zero call-site/test churn for the rollback path, while still routing through the repo (the ADR-046 goal).
- Behavior-preserving: the repo methods are thin delegates to the same underlying `db_*` functions.
