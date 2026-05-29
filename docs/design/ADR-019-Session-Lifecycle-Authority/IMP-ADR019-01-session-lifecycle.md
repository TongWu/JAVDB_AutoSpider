# Session Lifecycle Authority — Phase 1: `SessionLifecycle`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `SessionLifecycle` as the single authority for legal session-status transitions, route every status **write** through it, and fix the latent corruption path where `committed→failed` / `failed→committed` are silently allowed. Pure transition legality becomes unit-testable with no database.

**Architecture:** A new deep module `javdb/storage/sessions/lifecycle.py` with a pure `can_transition`, a typed `get_state`, and a `transition` that validates then dispatches to the existing four primitives in `_db_reports.py`. Primitive SQL/signatures are **unchanged** this phase — `transition` calls them. This is a behavior-preserving reroute except that two truly-illegal edges now raise instead of silently mutating.

**Tech Stack:** Python 3.11+, pytest. Single repo. No schema change, no D1 change.

**Related:** [ADR-019](ADR-019-session-lifecycle-authority.md)

**Status:** Completed — implemented and verified on 2026-05-29 (commits `e3edd774` module + tests, `4fa58171` reroute, `c623e1a3` review nits; full storage/rollback/rclone/lifecycle suite green; `committed→failed` now provably blocked).

---

## Scope

- **In:** new `lifecycle.py`; reroute the ~9 status-write call-sites through `transition`; pure transition tests; CONTEXT.md reconciliation (ADR-019 D6).
- **Out:** changing the four primitives' SQL/signatures (kept as the dispatch targets); mass-migrating status *reads*; the `CommitPipeline` extraction (IMP-ADR019-02); the session-id global (Candidate C).
- **Behavior change (intended):** `transition` raises `IllegalTransition` on `committed→failed` and `failed→committed`. All other edges (incl. idempotent no-ops) keep today's behavior — return rowcount, `0` when nothing changed.

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Create | `javdb/storage/sessions/lifecycle.py` | `SessionStatus`, `IllegalTransition`, `can_transition` (pure), `get_state`, `transition` |
| Modify | `javdb/storage/db/_db_history_write.py` | Status flips at `:1535` (→finalizing), `:1636` (→committed) call `transition` |
| Modify | `javdb/storage/db/_db_rollback.py` | `:430` `mark_failed` via `transition`; **keep** the committed-refusal / finalizing-skip policy guards (`:403-427`) above it |
| Modify | `javdb/storage/sessions/commit.py` | `:216` `mark_committed` via `transition`; preserve the `n==0` branch (`:222-228`) |
| Modify | `apps/cli/db/commit_session.py` | `:430` `mark_committed` via `transition`; preserve the `n==0` branch (`:434`) |
| Modify | `javdb/integrations/rclone/manager.py` | `:1262,1267,1273,1536,1609,1660,1687` staging-session marks via `transition` (`in_progress→committed`/`→failed` must stay legal) |
| Modify | `CONTEXT.md` | Reconcile the non-existent `ReportsRepo.mark_committed()/.mark_failed()` entry; add `SessionLifecycle` vocabulary |
| Create | `tests/unit/test_session_lifecycle.py` | Pure 4×4 `can_transition` matrix + `transition` raise/idempotent behavior |

---

## Task 1: The `SessionLifecycle` module

- [ ] Create `javdb/storage/sessions/lifecycle.py`:

```python
"""Single authority for legal ReportSessions status transitions (ADR-019)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

# Closed status set (matches ReportSessions.Status / CONTEXT.md).
IN_PROGRESS, FINALIZING, COMMITTED, FAILED = "in_progress", "finalizing", "committed", "failed"
_ALL = {IN_PROGRESS, FINALIZING, COMMITTED, FAILED}

# Legal forward edges (idempotent same-state handled separately).
_LEGAL = {
    (IN_PROGRESS, FINALIZING),
    (IN_PROGRESS, COMMITTED),   # staging fast-path (rclone / empty commit)
    (IN_PROGRESS, FAILED),
    (FINALIZING, COMMITTED),
    (FINALIZING, FAILED),
}

class IllegalTransition(RuntimeError):
    pass

@dataclass(frozen=True)
class SessionState:
    write_mode: Optional[str]
    status: Optional[str]

def can_transition(frm: Optional[str], to: str) -> bool:
    """Pure. True if `frm → to` is legal or an idempotent no-op."""
    if to not in _ALL:
        return False
    if frm == to:
        return True            # idempotent
    if frm is None:
        return False           # unknown/missing session
    return (frm, to) in _LEGAL

def get_state(session_id: str, *, db_path: Optional[str] = None) -> SessionState:
    from javdb.storage.db import db_get_session_status
    write_mode, status = db_get_session_status(session_id, db_path=db_path)
    return SessionState(write_mode=write_mode, status=status)

def transition(session_id: str, to: str, *, db_path: Optional[str] = None, reason: Optional[str] = None) -> int:
    """Validate then dispatch to the matching _db_reports primitive.

    Raises IllegalTransition on a truly-illegal edge. Idempotent / no-op edges
    return 0 (preserving callers that branch on rowcount==0).
    """
    frm = get_state(session_id, db_path=db_path).status
    if not can_transition(frm, to):
        raise IllegalTransition(f"{session_id}: {frm} -> {to} is not allowed")
    from javdb.storage.db import (
        db_begin_finalize_session, db_finish_commit_session,
        db_mark_session_committed, db_mark_session_failed,
    )
    if to == FINALIZING:
        return db_begin_finalize_session(session_id, db_path=db_path)
    if to == FAILED:
        return db_mark_session_failed(session_id, db_path=db_path, reason=reason)
    if to == COMMITTED:
        # finalizing→committed uses the strict primitive; the in_progress→committed
        # staging fast-path uses the loose one. Pick by current state.
        if frm == FINALIZING:
            return db_finish_commit_session(session_id, db_path=db_path)
        return db_mark_session_committed(session_id, db_path=db_path)
    raise IllegalTransition(f"unknown target status {to!r}")
```

- [ ] Verify `db_get_session_status` returns `(write_mode, status)` (`_db_reports.py:203-237`) and the four primitive import names are exported from `javdb.storage.db` `__init__`.
- [ ] Confirm the strict-primitive selection for `→committed` matches today: `finalizing→committed` must go through `db_finish_commit_session` (strict), and `in_progress→committed` through `db_mark_session_committed` (loose) — read each current call-site (Task 2) to confirm which they use today and preserve it.

---

## Task 2: Route the status writes through `transition`

> Behavior-preserving: each site already performs a legal edge today. The only change is centralization + the new guard on illegal edges.

- [ ] First, **find every writer** to be safe: `grep -rn "db_mark_session_committed\|db_mark_session_failed\|db_begin_finalize_session\|db_finish_commit_session" javdb apps | grep -v _db_reports.py` and reconcile against the File Map list.
- [ ] `_db_history_write.py:1535` (`db_begin_finalize_session`) → `SessionLifecycle.transition(session_id, "finalizing", db_path=...)`. Strict edge, pure refactor.
- [ ] `_db_history_write.py:1636` (`db_finish_commit_session`) → `transition(session_id, "committed")` (current state is `finalizing` here, so it dispatches to the strict primitive). Preserve the crash-ordering invariant documented at `:1622-1633` (flip happens before pending delete).
- [ ] `sessions/commit.py:216` (`db_mark_session_committed`) → `transition(session_id, "committed")`. **Preserve** the `n==0` branch at `:222-228` (idempotent edge still returns 0).
- [ ] `apps/cli/db/commit_session.py:430` → `transition(session_id, "committed")`; preserve `n==0` branch at `:434`.
- [ ] `_db_rollback.py:430` (`db_mark_session_failed`) → `transition(session_id, "failed", reason=...)`. **Keep the existing policy guards above it** (`:403-408` committed-refusal, `:419-427` finalizing-skip) — they decide *whether* to flag; `transition` only enforces legality. (A committed session never reaches `transition` because the guard returns early.)
- [ ] `rclone/manager.py:1262,1267,1273,1536,1609,1660,1687` → route each through `transition`. These are staging sessions doing `in_progress→committed` and `in_progress→failed`; confirm both stay legal (they do, per `_LEGAL`).
- [ ] **Do not** change the four primitives' SQL or signatures in this phase.

---

## Task 3: Reconcile CONTEXT.md (ADR-019 D6)

- [ ] In `CONTEXT.md`, fix the Rollback/Reports section that documents `ReportsRepo(conn, session_id).mark_committed()` / `.mark_failed()` (these do not exist). Replace with the real surface: the `_db_reports` primitives + the new `SessionLifecycle.transition` as the canonical write path.
- [ ] Add a `SessionLifecycle` glossary entry (interface: `can_transition` / `transition` / `get_state`; the legal graph; `IllegalTransition`). Mark "ADR-019".

---

## Task 4: Tests

- [ ] Create `tests/unit/test_session_lifecycle.py`:
  - Pure `can_transition` over the full 4×4 matrix (+ `None` source, + unknown target): assert exactly the `_LEGAL` set ∪ same-state are True, everything else False. **No DB.**
  - `transition` raises `IllegalTransition` on `committed→failed` and `failed→committed` (use a fake `get_state`/monkeypatch or a tiny in-memory session).
  - `transition` to an already-current state returns `0` (idempotent), and dispatches to the correct primitive per source state (`finalizing→committed` → strict; `in_progress→committed` → loose) — assert via monkeypatched primitives.
- [ ] **Regression — must stay green:** `pytest tests/unit/test_commit_session_bulk.py tests/unit/test_rollback_pending_mode.py tests/unit/test_rollback.py tests/unit/test_commit_session_library.py tests/unit/test_rclone_manager.py -q`.

---

## Task 5: Verification gates

- [ ] `pytest tests/unit/test_session_lifecycle.py -v` — green.
- [ ] Full storage + rollback + rclone suites green (Task 4 regression list).
- [ ] **Bug-fix proof:** a focused test asserting the OLD corruption is now blocked — create a `committed` session, call `transition(sid, "failed")`, assert `IllegalTransition` (previously `db_mark_session_failed` would have flipped it).
- [ ] `grep -rn "db_mark_session_committed\|db_mark_session_failed\|db_begin_finalize_session\|db_finish_commit_session" javdb apps | grep -v "_db_reports.py\|sessions/lifecycle.py"` returns **nothing** (all writers now go through `transition`).
- [ ] Update this IMP's `Status` to `Completed` and check off `IMP-ADR019-01` in the ADR roadmap.

---

## Implementation notes (discovered during Phase 1, accepted)

- **+1 `get_state` read per status flip.** Routing through `transition` adds an indexed single-row `SELECT ReportSessions` before each dispatch (≈ +2 reads per commit, +1 per rclone flip). A conscious trade of a cheap read for centralized legality; acceptable at this scale.
- **`failed→failed` no longer re-writes `FailureReason`.** The old `db_mark_session_failed` ran an unconditional `UPDATE`; `transition` treats same-state as an idempotent no-op (returns 0). So re-rolling-back an already-`failed` session keeps the *first* failure reason (canonical) rather than overwriting it. Marginal, and arguably better.
- **`commit.py` `failed→committed` now raises** instead of silently flipping — the intended ADR-019 corruption refusal. The committed/failed terminal states are unreachable as commit sources in normal flow; the raise is a backstop.

## Out of scope

- `CommitPipeline` substep extraction → IMP-ADR019-02.
- Changing the four primitives to private / removing their loose guards → optional later cleanup (once all writers go through `transition`, the primitives' own guards become a redundant second line of defense; leave them).
- Session-id process global → Candidate C.
